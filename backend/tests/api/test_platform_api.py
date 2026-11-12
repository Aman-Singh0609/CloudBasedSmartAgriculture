"""Cross-cutting behaviour: weather endpoint, health, errors, CSRF, headers,
rate limiting, observability, audit trail, database outage, front-end serving."""

import json
import logging
import re

import pytest
import requests
from pymongo.errors import ServerSelectionTimeoutError

from app.db import Database
from app.extensions import limiter
from tests.conftest import (
    RICE_INPUTS,
    TEST_PASSWORD,
    FakeResponse,
    assert_error,
    assert_success,
    login,
    predict_json,
    register_and_login,
    reset_rate_limits,
    signup,
    weather_payload,
)

API_KEY = "unit-test-weather-key"


# ==================================================================== weather
def test_weather_lookup_returns_temperature_and_humidity(auth_client, fake_weather):
    data = assert_success(auth_client.get("/api/weather?city=Pune"))
    assert data["location"] == "Pune, IN"
    assert data["temperature"] == 27.3
    assert data["humidity"] == 64.0
    assert data["source"] == "weather_api"
    assert data["provider"] == "openweathermap"
    assert data["observed_at"].endswith("Z")
    assert data["conditions"] == "light rain"


def test_weather_never_supplies_model_rainfall(auth_client, fake_weather):
    """Last-hour rain is not the growing-period rainfall the model expects."""
    data = assert_success(auth_client.get("/api/weather?city=Pune"))
    assert data["rainfall"] is None
    assert data["observed_rain_last_hour_mm"] == 0.4
    assert "not filled in automatically" in data["rainfall_note"]


def test_weather_missing_rainfall_is_reported_as_null(auth_client, fake_weather):
    payload = weather_payload()
    del payload["rain"]
    fake_weather.result = FakeResponse(200, payload)
    data = assert_success(auth_client.get("/api/weather?city=Pune"))
    assert data["observed_rain_last_hour_mm"] is None
    assert data["temperature"] == 27.3


def test_weather_api_key_is_never_sent_to_the_browser(auth_client, fake_weather):
    response = auth_client.get("/api/weather?city=Pune")
    assert API_KEY not in response.get_data(as_text=True)
    assert API_KEY not in str(response.headers)
    assert API_KEY not in auth_client.get("/api/config").get_data(as_text=True)
    assert fake_weather.calls[0]["params"]["appid"] == API_KEY


@pytest.mark.parametrize("query", ["", "?city=", "?city=%20", "?city=A", "?city=123", "?city=Pune%26appid%3Dx", "?city=" + "x" * 90, "?city=%3Cscript%3E"])
def test_weather_rejects_invalid_city_without_calling_the_provider(auth_client, fake_weather, query):
    error = assert_error(auth_client.get(f"/api/weather{query}"), 422, "INVALID_INPUT")
    assert "city" in error["details"]
    assert fake_weather.calls == []


@pytest.mark.parametrize(
    "result,status,code",
    [
        (FakeResponse(404, {"message": "city not found"}), 404, "WEATHER_CITY_NOT_FOUND"),
        (FakeResponse(401, {"message": "Invalid API key"}), 503, "WEATHER_UNAVAILABLE"),
        (FakeResponse(429, {}), 503, "WEATHER_UNAVAILABLE"),
        (FakeResponse(500, {}), 502, "WEATHER_PROVIDER_ERROR"),
        (FakeResponse(200, text="<html>oops</html>"), 502, "WEATHER_PROVIDER_ERROR"),
        (FakeResponse(200, {"main": {"temp": 20}}), 502, "WEATHER_PROVIDER_ERROR"),
        (requests.Timeout("slow"), 504, "WEATHER_TIMEOUT"),
        (requests.ConnectionError(f"url: /weather?appid={API_KEY}"), 503, "WEATHER_UNAVAILABLE"),
    ],
)
def test_weather_failures_map_to_clear_errors(auth_client, fake_weather, result, status, code):
    fake_weather.result = result
    response = auth_client.get("/api/weather?city=Pune")
    error = assert_error(response, status, code)
    text = response.get_data(as_text=True)
    assert API_KEY not in text
    assert "Traceback" not in text and "requests." not in text
    if status >= 500:
        assert "manually" in error["message"] or "yourself" in error["message"] or "try again" in error["message"].lower()


def test_weather_not_configured(app_factory):
    app = app_factory(WEATHER_API_KEY=None)
    client = register_and_login(app)
    assert assert_success(client.get("/api/config"))["weather_enabled"] is False
    assert_error(client.get("/api/weather?city=Pune"), 503, "WEATHER_NOT_CONFIGURED")


def test_prediction_still_works_when_weather_is_down(auth_client, fake_weather):
    """Workflow D: the advisor stays usable with manual entry."""
    fake_weather.result = requests.ConnectionError("down")
    assert_error(auth_client.get("/api/weather?city=Pune"), 503, "WEATHER_UNAVAILABLE")
    data = assert_success(predict_json(auth_client), 201)
    assert data["recommended_crop"] == "rice"
    assert set(data["input_sources"].values()) == {"manual"}


def test_server_decides_which_readings_came_from_the_weather_service(auth_client, fake_weather):
    """Workflow B: provenance is verified server-side, not claimed by the client."""
    weather = assert_success(auth_client.get("/api/weather?city=Pune"))
    data = assert_success(predict_json(auth_client, temperature=weather["temperature"], humidity=weather["humidity"]), 201)
    assert data["input_sources"]["temperature"] == "weather_api"
    assert data["input_sources"]["humidity"] == "weather_api"
    assert data["input_sources"]["rainfall"] == "manual"
    assert data["input_sources"]["nitrogen"] == "manual"
    assert data["weather"]["location"] == "Pune, IN"
    assert data["weather"]["provider"] == "openweathermap"
    assert data["weather"]["observed_at"].endswith("Z")


def test_edited_weather_values_count_as_manual(auth_client, fake_weather):
    weather = assert_success(auth_client.get("/api/weather?city=Pune"))
    data = assert_success(predict_json(auth_client, temperature=weather["temperature"] + 2, humidity=weather["humidity"]), 201)
    assert data["input_sources"]["temperature"] == "manual"
    assert data["input_sources"]["humidity"] == "weather_api"

    data = assert_success(predict_json(auth_client, temperature=30, humidity=50), 201)
    assert set(data["input_sources"].values()) == {"manual"}
    assert data["weather"] is None


def test_weather_failure_is_audited(auth_client, fake_weather, db):
    fake_weather.result = FakeResponse(500, {})
    auth_client.get("/api/weather?city=Pune")
    event = db["audit_logs"].find_one({"event": "weather.provider_failure"})
    assert event["outcome"] == "failure"
    assert event["details"] == {"error_code": "WEATHER_PROVIDER_ERROR"}


# ===================================================================== health
def test_health_reports_each_dependency(client):
    response = client.get("/health")
    data = assert_success(response)
    assert data["status"] == "ok"
    assert data["checks"] == {
        "application": "ok",
        "database": "ok",
        "model": "ok",
        "migrations": "up_to_date",
        "weather": "configured",
    }
    assert data["timestamp"].endswith("Z") and data["version"]


def test_liveness_probe(client):
    assert assert_success(client.get("/health/live")) == {"status": "ok"}


def test_health_is_unhealthy_when_the_model_is_missing(app, client, monkeypatch):
    monkeypatch.setattr(type(app.extensions["services"].prediction), "is_available", property(lambda self: False))
    response = client.get("/health")
    assert response.status_code == 503
    body = response.get_json()
    assert body["data"]["status"] == "unhealthy"
    assert body["data"]["checks"]["model"] == "unavailable"
    assert body["data"]["checks"]["database"] == "ok"


def test_weather_outage_does_not_make_the_application_unhealthy(app_factory):
    app = app_factory(WEATHER_API_KEY=None)
    data = assert_success(app.test_client().get("/health"))
    assert data["status"] == "ok"
    assert data["checks"]["weather"] == "not_configured"


def test_health_exposes_no_infrastructure_details(app_factory):
    app = app_factory(SECRET_KEY="super-secret-key-value-1234567890-abcdef", MONGODB_URI="mongodb://dbuser:dbpass@db.internal:27017/x")
    text = app.test_client().get("/health").get_data(as_text=True)
    for secret in ("super-secret-key", "dbpass", "dbuser", "db.internal", "27017", "mongodb://", API_KEY, ".pkl"):
        assert secret not in text


# ============================================================ database outage
@pytest.fixture
def offline_app(app_factory, monkeypatch):
    """An application whose database cannot be reached (Workflow E)."""
    app = app_factory()
    client = register_and_login(app)

    broken = Database("mongodb://127.0.0.1:1/agri_test_down", "agri_test_down", server_selection_timeout_ms=200)
    app.extensions["database"]._client = broken.client
    return app, client


def test_database_outage_gives_a_friendly_503(offline_app, caplog):
    app, client = offline_app
    with caplog.at_level(logging.ERROR):
        response = client.get("/api/predictions")
    error = assert_error(response, 503, "DATABASE_UNAVAILABLE")
    assert "cannot reach our database" in error["message"]

    text = response.get_data(as_text=True)
    for leak in ("pymongo", "ServerSelectionTimeoutError", "127.0.0.1", "Traceback", "Timeout:", "topology"):
        assert leak not in text

    records = [r for r in caplog.records if getattr(r, "event", "") == "database_error"]
    assert records and records[0].error_type == "ServerSelectionTimeoutError"


def test_database_outage_fails_writes_safely(offline_app):
    _app, client = offline_app
    assert_error(predict_json(client), 503, "DATABASE_UNAVAILABLE")
    assert_error(client.post("/api/auth/login", json={"email": "asha@example.test", "password": TEST_PASSWORD}), 503, "DATABASE_UNAVAILABLE")


def test_public_endpoints_keep_working_during_a_database_outage(offline_app):
    _app, client = offline_app
    assert client.get("/health/live").status_code == 200
    assert client.get("/api/config").status_code == 200
    response = client.get("/health")
    assert response.status_code == 503
    assert response.get_json()["data"]["checks"]["database"] == "unavailable"
    assert response.get_json()["data"]["checks"]["migrations"] == "unknown"


def test_logout_still_works_during_a_database_outage(offline_app):
    _app, client = offline_app
    assert_success(client.post("/api/auth/logout"))
    with client.session_transaction() as session:
        assert "user_id" not in session


def test_database_outage_does_not_log_the_user_out(offline_app):
    """A transient outage must not be mistaken for 'not logged in'."""
    _app, client = offline_app
    assert client.get("/api/auth/me").status_code == 503
    with client.session_transaction() as session:
        assert "user_id" in session


# ===================================================================== errors
def test_unknown_route_is_a_json_404(client):
    error = assert_error(client.get("/api/does-not-exist"), 404, "NOT_FOUND")
    assert error["message"] == "The requested resource does not exist."
    assert_error(client.get("/totally/unknown"), 404, "NOT_FOUND")


def test_wrong_method_is_405_with_allow_header(client):
    response = client.delete("/api/auth/login")
    assert_error(response, 405, "METHOD_NOT_ALLOWED")
    assert response.headers["Allow"] == "OPTIONS, POST"


def test_unexpected_exception_returns_a_generic_500(app, client, caplog, db):
    @app.get("/api/_boom")
    def boom():
        raise RuntimeError("secret internal detail: /srv/app/config.py line 42")

    with caplog.at_level(logging.ERROR):
        response = client.get("/api/_boom")
    error = assert_error(response, 500, "INTERNAL_ERROR")
    text = response.get_data(as_text=True)
    assert "secret internal detail" not in text and "RuntimeError" not in text and "Traceback" not in text
    assert error["message"] == "Something went wrong on our side. Please try again."

    # ...but developers get the full story in the log, with the request ID.
    record = next(r for r in caplog.records if getattr(r, "event", "") == "unhandled_exception")
    assert record.exc_info is not None
    formatted = json.loads(__import__("app.logging_config", fromlist=["JsonFormatter"]).JsonFormatter().format(record))
    assert "secret internal detail" in formatted["exception"]
    assert db["audit_logs"].find_one({"event": "server.unhandled_exception"})["details"]["error_type"] == "RuntimeError"


def test_driver_errors_never_reach_the_client(app, client):
    @app.get("/api/_dberror")
    def dberror():
        raise ServerSelectionTimeoutError("db.internal.example:27017: [Errno 111] Connection refused")

    response = client.get("/api/_dberror")
    assert_error(response, 503, "DATABASE_UNAVAILABLE")
    assert "db.internal" not in response.get_data(as_text=True)


@pytest.mark.parametrize("path", ["/health", "/api/config", "/api/does-not-exist", "/api/predictions"])
def test_every_response_is_json_with_a_request_id(client, path):
    response = client.get(path)
    assert response.mimetype == "application/json"
    assert re.fullmatch(r"[0-9a-f]{32}", response.headers["X-Request-ID"])
    body = response.get_json()
    assert isinstance(body["success"], bool)
    if not body["success"]:
        assert body["request_id"] == response.headers["X-Request-ID"]


def test_well_formed_inbound_request_id_is_honoured(client):
    response = client.get("/health/live", headers={"X-Request-ID": "trace-abc_123.xyz"})
    assert response.headers["X-Request-ID"] == "trace-abc_123.xyz"


@pytest.mark.parametrize("bad", ["short", "x" * 80, 'inject"{"level":"ERROR"}', "has space in it", "<script>alert(1)</script>"])
def test_malformed_inbound_request_id_is_replaced(client, bad):
    response = client.get("/health/live", headers={"X-Request-ID": bad})
    assert re.fullmatch(r"[0-9a-f]{32}", response.headers["X-Request-ID"])


# ============================================================ security headers
@pytest.mark.parametrize("path", ["/health", "/api/config", "/api/predictions", "/api/nope"])
def test_security_headers_are_set_on_every_response(client, path):
    headers = client.get(path).headers
    csp = headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp
    assert "script-src 'self'" in csp and "unsafe-inline" not in csp and "unsafe-eval" not in csp
    assert "frame-ancestors 'none'" in csp and "object-src 'none'" in csp and "form-action 'self'" in csp
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert headers["Cache-Control"] == "no-store"
    assert "Access-Control-Allow-Origin" not in headers  # same-origin only: no CORS


def test_hsts_only_when_cookies_are_secure(app_factory, client):
    assert "Strict-Transport-Security" not in client.get("/health").headers
    secure = app_factory(SESSION_COOKIE_SECURE=True).test_client()
    assert "max-age=31536000" in secure.get("/health").headers["Strict-Transport-Security"]


def test_cross_origin_requests_get_no_cors_permission(client):
    response = client.get("/api/config", headers={"Origin": "https://evil.example"})
    assert "Access-Control-Allow-Origin" not in response.headers
    preflight = client.options("/api/predictions", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert "Access-Control-Allow-Origin" not in preflight.headers


def test_stored_text_is_returned_as_json_data_not_markup(auth_client, db):
    """XSS: a hostile name in the database is only ever serialised as a JSON string."""
    db["users"].update_one({}, {"$set": {"name": '<img src=x onerror="alert(1)">'}})
    response = auth_client.get("/api/auth/me")
    assert response.mimetype == "application/json"
    assert response.get_json()["data"]["user"]["name"] == '<img src=x onerror="alert(1)">'
    assert response.headers["X-Content-Type-Options"] == "nosniff"


# ======================================================================= CSRF
@pytest.fixture
def csrf_client(app_factory):
    app = app_factory(WTF_CSRF_ENABLED=True)
    client = app.test_client()
    return app, client


def _token(client) -> str:
    return client.get("/api/csrf-token").get_json()["data"]["csrf_token"]


def test_state_changing_requests_without_a_csrf_token_are_rejected(csrf_client, db=None):
    app, client = csrf_client
    payload = {"name": "Asha Patel", "email": "asha@example.test", "password": TEST_PASSWORD, "confirm_password": TEST_PASSWORD}
    error = assert_error(client.post("/api/auth/signup", json=payload), 400, "CSRF_VALIDATION_FAILED")
    assert "reload" in error["message"]
    assert app.extensions["database"].db["users"].count_documents({}) == 0
    assert_error(client.post("/api/auth/login", json={"email": "a@example.test", "password": "x"}), 400, "CSRF_VALIDATION_FAILED")
    assert_error(client.post("/api/auth/logout"), 400, "CSRF_VALIDATION_FAILED")


def test_csrf_protected_flow_with_tokens(csrf_client):
    app, client = csrf_client
    payload = {"name": "Asha Patel", "email": "asha@example.test", "password": TEST_PASSWORD, "confirm_password": TEST_PASSWORD}
    headers = {"X-CSRFToken": _token(client)}
    assert_success(client.post("/api/auth/signup", json=payload, headers=headers), 201)
    assert_success(client.post("/api/auth/login", json={"email": "asha@example.test", "password": TEST_PASSWORD}, headers=headers))

    # Logging in starts a new session, so the pre-login token no longer works.
    assert_error(client.post("/api/predictions", json=RICE_INPUTS, headers=headers), 400, "CSRF_VALIDATION_FAILED")
    assert_error(client.post("/api/predictions", json=RICE_INPUTS), 400, "CSRF_VALIDATION_FAILED")

    headers = {"X-CSRFToken": _token(client)}
    created = assert_success(client.post("/api/predictions", json=RICE_INPUTS, headers=headers), 201)

    assert_error(client.delete(f"/api/predictions/{created['id']}"), 400, "CSRF_VALIDATION_FAILED")
    assert_error(client.delete(f"/api/predictions/{created['id']}", headers={"X-CSRFToken": "forged.token.value"}), 400, "CSRF_VALIDATION_FAILED")
    assert app.extensions["database"].db["predictions"].count_documents({}) == 1
    assert_success(client.delete(f"/api/predictions/{created['id']}", headers=headers))

    assert_success(client.post("/api/auth/logout", headers=headers))


def test_a_token_from_another_session_is_rejected(csrf_client):
    app, client = csrf_client
    attacker = app.test_client()
    stolen = _token(attacker)
    payload = {"name": "Asha Patel", "email": "asha@example.test", "password": TEST_PASSWORD, "confirm_password": TEST_PASSWORD}
    _token(client)  # victim has a session of their own
    assert_error(client.post("/api/auth/signup", json=payload, headers={"X-CSRFToken": stolen}), 400, "CSRF_VALIDATION_FAILED")


def test_read_only_requests_need_no_csrf_token(csrf_client):
    _app, client = csrf_client
    for path in ("/api/config", "/api/auth/me", "/health"):
        assert client.get(path).status_code == 200


# ============================================================== rate limiting
@pytest.fixture
def limited_app(app_factory):
    app = app_factory(RATELIMIT_ENABLED=True)
    limiter.enabled = True
    reset_rate_limits()
    yield app
    reset_rate_limits()
    limiter.enabled = False


def test_repeated_failed_logins_for_one_account_are_throttled(limited_app):
    client = limited_app.test_client()
    signup(client)
    for _ in range(8):
        assert_error(login(client, password="Wrong-Passw0rd"), 401, "INVALID_CREDENTIALS")
    response = login(client, password="Wrong-Passw0rd")
    assert_error(response, 429, "RATE_LIMITED")
    assert int(response.headers["Retry-After"]) > 0
    # Even the correct password is refused while the account is throttled...
    assert_error(login(client, password=TEST_PASSWORD), 429, "RATE_LIMITED")
    # ...but other accounts are unaffected.
    signup(client, email="ravi@example.test", name="Ravi Kumar")
    assert_success(login(client, email="ravi@example.test"))


def test_successful_logins_do_not_consume_the_failure_budget(limited_app):
    client = limited_app.test_client()
    signup(client)
    for _ in range(10):
        assert_success(login(client))
    assert_error(login(client, password="Wrong-Passw0rd"), 401, "INVALID_CREDENTIALS")


def test_predictions_are_limited_per_user_not_per_ip(limited_app):
    first = register_and_login(limited_app)
    second = register_and_login(limited_app, name="Ravi Kumar", email="ravi@example.test")
    for _ in range(30):
        assert predict_json(first).status_code == 201
    assert_error(predict_json(first), 429, "RATE_LIMITED")
    # Same IP address, different account: still allowed.
    assert predict_json(second).status_code == 201


def test_signup_is_rate_limited(limited_app):
    client = limited_app.test_client()
    for _ in range(20):
        assert client.post("/api/auth/signup", json={"email": "bad"}).status_code == 422
    assert_error(client.post("/api/auth/signup", json={"email": "bad"}), 429, "RATE_LIMITED")


def test_weather_lookups_are_rate_limited(limited_app):
    client = register_and_login(limited_app)
    for _ in range(20):
        assert client.get("/api/weather?city=1").status_code == 422
    assert_error(client.get("/api/weather?city=1"), 429, "RATE_LIMITED")


def test_password_reset_requests_are_rate_limited(limited_app):
    client = limited_app.test_client()
    signup(client)
    for _ in range(3):
        assert client.post("/api/auth/forgot-password", json={"email": "asha@example.test"}).status_code == 202
    assert_error(client.post("/api/auth/forgot-password", json={"email": "asha@example.test"}), 429, "RATE_LIMITED")
    assert len(limited_app.extensions["services"].mailer.outbox) == 3


def test_health_checks_are_never_rate_limited(limited_app):
    client = limited_app.test_client()
    assert all(client.get("/health/live").status_code == 200 for _ in range(320))


# ====================================================== observability / audit
def test_each_request_is_recorded_without_sensitive_content(auth_client, db):
    auth_client.get("/api/predictions?page=1&secret_query=do-not-store")
    predict_json(auth_client)
    logs = list(db["api_logs"].find({}))
    by_endpoint = {(log["method"], log["endpoint"]): log for log in logs}

    created = by_endpoint[("POST", "predictions.create")]
    assert created["status"] == 201
    assert created["route"] == "/api/predictions"
    assert created["duration_ms"] >= 0
    assert re.fullmatch(r"[0-9a-f]{32}", created["request_id"])
    assert created["user_id"] and created["created_at"].tzinfo is not None
    assert set(created) == {"_id", "endpoint", "route", "method", "status", "duration_ms", "request_id", "user_id", "created_at"}

    text = str(logs)
    for leak in (TEST_PASSWORD, "do-not-store", "secret_query", "202.9", "asha@example.test"):
        assert leak not in text
    assert by_endpoint[("POST", "auth.login")]["status"] == 200


def test_request_log_lines_are_structured(auth_client, caplog):
    with caplog.at_level(logging.INFO, logger="app.request"):
        predict_json(auth_client)
    record = next(r for r in caplog.records if getattr(r, "event", "") == "http_request")
    assert (record.method, record.status, record.endpoint) == ("POST", 201, "predictions.create")
    assert record.duration_ms > 0 and record.request_id


def test_audit_trail_covers_account_and_prediction_events(app, db):
    client = app.test_client()
    signup(client)
    login(client, password="Wrong-Passw0rd")
    login(client)
    created = assert_success(predict_json(client), 201)
    client.delete(f"/api/predictions/{created['id']}")
    client.post("/api/auth/logout")

    events = [(e["event"], e["outcome"]) for e in db["audit_logs"].find({}).sort("_id", 1)]
    assert events == [
        ("auth.signup", "success"),
        ("auth.login_failure", "failure"),
        ("auth.login_success", "success"),
        ("prediction.created", "success"),
        ("prediction.deleted", "success"),
        ("auth.logout", "success"),
    ]
    user_id = db["users"].find_one({})["_id"]
    prediction_event = db["audit_logs"].find_one({"event": "prediction.created"})
    assert prediction_event["user_id"] == user_id
    assert prediction_event["details"]["recommended_crop"] == "rice"
    assert prediction_event["request_id"]


def test_audit_log_and_application_log_never_contain_credentials(app, db, caplog):
    client = app.test_client()
    with caplog.at_level(logging.DEBUG):
        signup(client)
        # A user who types their password into the email box by mistake:
        client.post("/api/auth/login", json={"email": "My-Real-Passw0rd!", "password": "asha@example.test"})
        login(client, password="Wrong-Passw0rd")
        login(client)
    audit_text = str(list(db["audit_logs"].find({})))
    for secret in (TEST_PASSWORD, "Wrong-Passw0rd", "My-Real-Passw0rd!", "scrypt:"):
        assert secret not in audit_text
        assert secret not in caplog.text
    failure = db["audit_logs"].find_one({"event": "auth.login_failure"})
    assert set(failure["details"]) == {"subject_hash"}
    assert "@" not in failure["details"]["subject_hash"]


# ========================================================== front-end serving
@pytest.fixture
def spa_app(app_factory, tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>AgricultureCloud</title><div id=root></div>", encoding="utf-8")
    (dist / "assets" / "app-abc123.js").write_text("console.log('app')", encoding="utf-8")
    (dist / "favicon.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("outside the build directory", encoding="utf-8")
    return app_factory(FRONTEND_DIST=str(dist))


def test_built_front_end_is_served_with_client_side_route_fallback(spa_app):
    client = spa_app.test_client()
    for path in ("/", "/login", "/history", "/history/aaaaaaaaaaaaaaaaaaaaaaaa", "/reset-password/abc"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert "<div id=root>" in response.get_data(as_text=True)
        assert response.headers["Cache-Control"] == "no-store"
        assert "script-src 'self'" in response.headers["Content-Security-Policy"]


def test_hashed_assets_are_cached_long_term(spa_app):
    response = spa_app.test_client().get("/assets/app-abc123.js")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "public, max-age=31536000, immutable"
    assert spa_app.test_client().get("/favicon.svg").status_code == 200


def test_front_end_fallback_never_shadows_the_api(spa_app):
    client = spa_app.test_client()
    assert_error(client.get("/api/does-not-exist"), 404, "NOT_FOUND")
    assert_error(client.get("/api"), 404, "NOT_FOUND")
    assert client.get("/health").mimetype == "application/json"
    assert client.get("/api/config").mimetype == "application/json"


@pytest.mark.parametrize("path", ["/../secret.txt", "/assets/../../secret.txt", "/%2e%2e/secret.txt", "/..%2fsecret.txt"])
def test_front_end_serving_cannot_escape_its_directory(spa_app, path):
    text = spa_app.test_client().get(path).get_data(as_text=True)
    assert "outside the build directory" not in text


def test_without_a_build_only_the_api_exists(client):
    assert_error(client.get("/"), 404, "NOT_FOUND")
