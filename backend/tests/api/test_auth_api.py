"""Signup, login, logout, session and password-reset endpoints."""

import re
from datetime import datetime, timedelta, timezone

import pytest

from tests.conftest import (
    OTHER_PASSWORD,
    TEST_PASSWORD,
    assert_error,
    assert_success,
    login,
    predict_json,
    register_and_login,
    signup,
)


# --------------------------------------------------------------------- signup
def test_signup_creates_an_account(client, db):
    data = assert_success(signup(client, email="  Asha@Example.TEST "), 201)
    assert data["user"]["email"] == "asha@example.test"
    assert data["user"]["name"] == "Asha Patel"
    assert "password" not in str(data).lower()

    stored = db["users"].find_one({"email": "asha@example.test"})
    assert stored["name"] == "Asha Patel"
    assert stored["session_version"] == 1
    assert stored["created_at"].tzinfo is not None
    assert abs(datetime.now(timezone.utc) - stored["created_at"]) < timedelta(seconds=30)


def test_password_is_stored_hashed_never_in_plaintext(client, db):
    signup(client)
    stored = db["users"].find_one({"email": "asha@example.test"})
    assert "password" not in stored
    assert stored["password_hash"].startswith("scrypt:")
    assert TEST_PASSWORD not in str(stored)


def test_signup_does_not_log_the_user_in(client):
    signup(client)
    assert assert_success(client.get("/api/auth/me"))["authenticated"] is False


def test_duplicate_email_is_a_conflict(client, db):
    signup(client)
    error = assert_error(signup(client, email="ASHA@example.test", name="Someone Else"), 409, "EMAIL_ALREADY_REGISTERED")
    assert "already exists" in error["message"]
    assert db["users"].count_documents({}) == 1


@pytest.mark.parametrize(
    "overrides,field",
    [
        ({"email": "not-an-email"}, "email"),
        ({"email": ""}, "email"),
        ({"name": ""}, "name"),
        ({"name": "<script>alert(1)</script>"}, "name"),
        ({"password": "short1"}, "password"),
        ({"password": "nodigitshere"}, "password"),
        ({"password": "password123"}, "password"),
        ({"confirm_password": "Something-Else-9"}, "confirm_password"),
    ],
)
def test_signup_rejects_invalid_input(client, db, overrides, field):
    payload = {"name": "Asha Patel", "email": "asha@example.test", "password": TEST_PASSWORD, "confirm_password": TEST_PASSWORD}
    payload.update(overrides)
    error = assert_error(client.post("/api/auth/signup", json=payload), 422, "INVALID_INPUT")
    assert field in error["details"]
    assert db["users"].count_documents({}) == 0


def test_signup_ignores_and_never_stores_government_id(client, db):
    assert_success(signup(client, GovtId="1234-5678-9012", GovtIdType="aadhaar", Phone="9999999999", govt_id="X"), 201)
    stored = db["users"].find_one({})
    assert not any(key.lower().startswith(("govt", "phone")) for key in stored)
    assert "1234-5678-9012" not in str(stored)


def test_signup_cannot_set_privileged_fields(client, db):
    signup(client, session_version=99, is_admin=True, _id="aaaaaaaaaaaaaaaaaaaaaaaa")
    stored = db["users"].find_one({})
    assert stored["session_version"] == 1
    assert "is_admin" not in stored


def test_signup_requires_json(client):
    response = client.post("/api/auth/signup", data={"name": "A", "email": "a@example.test"})
    assert_error(response, 415, "UNSUPPORTED_MEDIA_TYPE")


@pytest.mark.parametrize("body", ["{not json", "[1, 2]", '"text"', "null"])
def test_malformed_json_is_a_bad_request(client, body):
    response = client.post("/api/auth/signup", data=body, content_type="application/json")
    assert_error(response, 400, "BAD_REQUEST")


# ---------------------------------------------------------------------- login
def test_login_starts_a_session(client):
    signup(client)
    data = assert_success(login(client, email=" ASHA@example.test "))
    assert data["user"]["email"] == "asha@example.test"
    me = assert_success(client.get("/api/auth/me"))
    assert me == {"authenticated": True, "user": data["user"]}


def test_login_records_last_login(client, db):
    signup(client)
    login(client)
    assert db["users"].find_one({})["last_login_at"] is not None


@pytest.mark.parametrize(
    "credentials",
    [
        {"email": "asha@example.test", "password": "Wrong-Passw0rd"},
        {"email": "nobody@example.test", "password": TEST_PASSWORD},
        {"email": "not-an-email", "password": TEST_PASSWORD},
        {"email": "asha@example.test", "password": ""},
        {"email": "asha@example.test"},
        {},
        # NoSQL operator injection attempts
        {"email": {"$ne": None}, "password": {"$ne": None}},
        {"email": {"$gt": ""}, "password": TEST_PASSWORD},
        {"email": "asha@example.test", "password": {"$regex": ".*"}},
        {"email": ["asha@example.test"], "password": [TEST_PASSWORD]},
    ],
)
def test_failed_logins_all_look_the_same(client, credentials):
    signup(client)
    error = assert_error(client.post("/api/auth/login", json=credentials), 401, "INVALID_CREDENTIALS")
    assert error["message"] == "Invalid email or password."
    assert assert_success(client.get("/api/auth/me"))["authenticated"] is False


def test_session_cookie_is_httponly_and_samesite(client):
    signup(client)
    cookie = login(client).headers["Set-Cookie"]
    assert cookie.startswith("agricloud_session=")
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie


def test_session_cookie_is_secure_in_production(app_factory):
    app = app_factory(SESSION_COOKIE_SECURE=True)
    client = app.test_client()
    signup(client)
    assert "Secure" in login(client).headers["Set-Cookie"]


def test_login_clears_any_pre_login_session(app, client):
    """Session fixation: nothing set before login survives into the authenticated session."""
    with client.session_transaction() as session:
        session["attacker_marker"] = "planted"
        session["user_id"] = "000000000000000000000000"
    signup(client)
    login(client)
    with client.session_transaction() as session:
        assert "attacker_marker" not in session
        assert session["user_id"] != "000000000000000000000000"


def test_forged_session_user_id_is_not_trusted(client):
    with client.session_transaction() as session:
        session["user_id"] = "not-an-object-id"
        session["sv"] = 1
    assert assert_success(client.get("/api/auth/me"))["authenticated"] is False
    assert_error(client.get("/api/predictions"), 401, "AUTHENTICATION_REQUIRED")


def test_deleted_account_loses_access_immediately(auth_client, db):
    db["users"].delete_many({})
    assert_error(auth_client.get("/api/predictions"), 401, "AUTHENTICATION_REQUIRED")


def test_changing_session_version_invalidates_existing_sessions(auth_client, db):
    db["users"].update_one({}, {"$inc": {"session_version": 1}})
    assert_error(auth_client.get("/api/predictions"), 401, "AUTHENTICATION_REQUIRED")


# --------------------------------------------------------------------- logout
def test_logout_ends_the_session(auth_client):
    assert_success(auth_client.post("/api/auth/logout"))
    assert assert_success(auth_client.get("/api/auth/me"))["authenticated"] is False
    assert_error(auth_client.get("/api/predictions"), 401, "AUTHENTICATION_REQUIRED")


def test_logout_is_post_only(auth_client):
    response = auth_client.get("/api/auth/logout")
    assert_error(response, 405, "METHOD_NOT_ALLOWED")
    assert "POST" in response.headers["Allow"]
    assert assert_success(auth_client.get("/api/auth/me"))["authenticated"] is True


def test_logout_when_not_logged_in_is_harmless(client):
    assert_success(client.post("/api/auth/logout"))


# ----------------------------------------------------------- protected routes
@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/api/predictions"),
        ("post", "/api/predictions"),
        ("get", "/api/predictions/trends"),
        ("get", "/api/predictions/aaaaaaaaaaaaaaaaaaaaaaaa"),
        ("delete", "/api/predictions/aaaaaaaaaaaaaaaaaaaaaaaa"),
        ("get", "/api/weather?city=Pune"),
    ],
)
def test_protected_endpoints_require_login(client, method, path):
    kwargs = {"json": {}} if method == "post" else {}
    assert_error(getattr(client, method)(path, **kwargs), 401, "AUTHENTICATION_REQUIRED")


@pytest.mark.parametrize("path", ["/api/config", "/api/csrf-token", "/api/auth/me", "/health", "/health/live"])
def test_public_endpoints_do_not_require_login(client, path):
    assert client.get(path).status_code == 200


# ------------------------------------------------------------- password reset
def _reset_link(app) -> str:
    outbox = app.extensions["services"].mailer.outbox
    assert len(outbox) == 1
    return re.search(r"/reset-password/([A-Za-z0-9_-]+)", outbox[0].body).group(1)


def test_password_reset_full_flow(app, client, db):
    old_session = register_and_login(app)
    data = client.post("/api/auth/forgot-password", json={"email": "ASHA@example.test"})
    assert_success(data, 202)

    message = app.extensions["services"].mailer.outbox[0]
    assert message.to == "asha@example.test"
    assert "http://localhost/reset-password/" in message.body
    token = _reset_link(app)

    assert assert_success(client.get(f"/api/auth/reset-password/{token}")) == {"valid": True}
    assert_success(
        client.post(
            f"/api/auth/reset-password/{token}",
            json={"password": OTHER_PASSWORD, "confirm_password": OTHER_PASSWORD},
        )
    )

    # old password no longer works, new one does, earlier sessions are dead
    assert_error(login(client, password=TEST_PASSWORD), 401, "INVALID_CREDENTIALS")
    assert_success(login(client, password=OTHER_PASSWORD))
    assert_error(old_session.get("/api/predictions"), 401, "AUTHENTICATION_REQUIRED")
    assert db["password_reset_tokens"].count_documents({}) == 0


def test_reset_token_is_stored_only_as_a_hash(app, client, db):
    signup(client)
    client.post("/api/auth/forgot-password", json={"email": "asha@example.test"})
    token = _reset_link(app)
    record = db["password_reset_tokens"].find_one({})
    assert token not in str(record)
    assert len(record["token_hash"]) == 64
    assert record["expires_at"] > datetime.now(timezone.utc)


def test_reset_token_can_be_used_only_once(app, client):
    signup(client)
    client.post("/api/auth/forgot-password", json={"email": "asha@example.test"})
    token = _reset_link(app)
    body = {"password": OTHER_PASSWORD, "confirm_password": OTHER_PASSWORD}
    assert_success(client.post(f"/api/auth/reset-password/{token}", json=body))
    assert_error(client.post(f"/api/auth/reset-password/{token}", json=body), 422, "INVALID_RESET_TOKEN")
    assert_error(client.get(f"/api/auth/reset-password/{token}"), 422, "INVALID_RESET_TOKEN")


def test_expired_reset_token_is_rejected(app, client, db):
    signup(client)
    client.post("/api/auth/forgot-password", json={"email": "asha@example.test"})
    token = _reset_link(app)
    db["password_reset_tokens"].update_many({}, {"$set": {"expires_at": datetime.now(timezone.utc) - timedelta(minutes=1)}})
    body = {"password": OTHER_PASSWORD, "confirm_password": OTHER_PASSWORD}
    assert_error(client.post(f"/api/auth/reset-password/{token}", json=body), 422, "INVALID_RESET_TOKEN")
    assert_success(login(client))  # original password untouched


@pytest.mark.parametrize("token", ["short", "a" * 43, "../../etc/passwd", "a" * 200, "has space in it....."])
def test_unknown_reset_tokens_are_rejected(client, token):
    response = client.get(f"/api/auth/reset-password/{token}")
    assert response.status_code in (404, 422)
    assert response.get_json()["success"] is False


def test_reset_with_weak_password_keeps_the_token_usable(app, client):
    signup(client)
    client.post("/api/auth/forgot-password", json={"email": "asha@example.test"})
    token = _reset_link(app)
    error = assert_error(
        client.post(f"/api/auth/reset-password/{token}", json={"password": "weak", "confirm_password": "weak"}),
        422,
        "INVALID_INPUT",
    )
    assert "password" in error["details"]
    assert assert_success(client.get(f"/api/auth/reset-password/{token}")) == {"valid": True}


def test_reset_request_does_not_reveal_whether_an_account_exists(app, client):
    signup(client)
    known = client.post("/api/auth/forgot-password", json={"email": "asha@example.test"})
    unknown = client.post("/api/auth/forgot-password", json={"email": "nobody@example.test"})
    assert known.status_code == unknown.status_code == 202
    assert known.get_json()["message"] == unknown.get_json()["message"]
    assert known.get_json()["data"] == unknown.get_json()["data"] == {}
    assert len(app.extensions["services"].mailer.outbox) == 1


def test_a_new_reset_request_replaces_the_previous_token(app, client, db):
    signup(client)
    client.post("/api/auth/forgot-password", json={"email": "asha@example.test"})
    first = _reset_link(app)
    app.extensions["services"].mailer.outbox.clear()
    client.post("/api/auth/forgot-password", json={"email": "asha@example.test"})
    assert db["password_reset_tokens"].count_documents({}) == 1
    assert_error(client.get(f"/api/auth/reset-password/{first}"), 422, "INVALID_RESET_TOKEN")


def test_forgot_password_validates_email_format(client):
    error = assert_error(client.post("/api/auth/forgot-password", json={"email": "nope"}), 422, "INVALID_INPUT")
    assert "email" in error["details"]


def test_password_reset_is_absent_when_mail_is_disabled(app_factory):
    """No mail backend -> the feature does not exist, rather than pretending to send email."""
    app = app_factory(MAIL_BACKEND="disabled")
    client = app.test_client()
    signup(client)
    assert assert_success(client.get("/api/config"))["password_reset_enabled"] is False
    assert_error(client.post("/api/auth/forgot-password", json={"email": "asha@example.test"}), 404, "NOT_FOUND")
    assert_error(client.get("/api/auth/reset-password/" + "a" * 43), 404, "NOT_FOUND")


def test_reset_logs_out_the_current_browser(app, db):
    client = register_and_login(app)
    predict_json(client)
    client.post("/api/auth/forgot-password", json={"email": "asha@example.test"})
    token = _reset_link(app)
    body = {"password": OTHER_PASSWORD, "confirm_password": OTHER_PASSWORD}
    assert_success(client.post(f"/api/auth/reset-password/{token}", json=body))
    assert assert_success(client.get("/api/auth/me"))["authenticated"] is False
    assert db["predictions"].count_documents({}) == 1  # data survives a reset
