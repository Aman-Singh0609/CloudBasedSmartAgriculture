"""Prediction service, weather client, mailers and log redaction."""

import json
import logging
import smtplib

import numpy as np
import pytest
import requests

from app.config import BASE_DIR, BUNDLED_MODEL_SHA256
from app.errors import ModelUnavailableError
from app.logging_config import REDACTED, JsonFormatter
from app.models.features import FEATURES, FEATURES_BY_KEY
from app.services.mail_service import (
    ConsoleMailer,
    DisabledMailer,
    MailDeliveryError,
    MemoryMailer,
    OutgoingEmail,
    SmtpMailer,
    build_mailer,
)
from app.services.prediction_service import PredictionService
from app.services.weather_service import (
    CityNotFoundError,
    WeatherNotConfiguredError,
    WeatherProviderError,
    WeatherService,
    WeatherTimeoutError,
    WeatherUnavailableError,
)
from app.utils.security import is_sensitive_key
from tests.conftest import RICE_INPUTS, FakeResponse, FakeSession, weather_payload

MODEL_PATH = BASE_DIR / "crop_recommendation_model.pkl"
API_KEY = "s3cr3t-weather-key-do-not-leak"
BASE_URL = "https://api.openweathermap.org/data/2.5/weather"


# ============================================================ prediction
def test_model_loads_and_reports_non_sensitive_info(prediction_service):
    assert prediction_service.is_available
    info = prediction_service.info
    assert info["model_type"] == "VotingClassifier"
    assert info["model_version"] == "2.0"
    assert info["n_classes"] == 22
    assert info["sha256_prefix"] == BUNDLED_MODEL_SHA256[:12]
    assert str(MODEL_PATH) not in json.dumps(info)


def test_model_is_loaded_once_and_reused(prediction_service):
    model = prediction_service._model
    assert prediction_service.load() is True
    assert prediction_service._model is model


def test_prediction_structure(prediction_service):
    result = prediction_service.predict({key: float(value) for key, value in RICE_INPUTS.items()})
    assert result.recommended_crop == "rice"
    assert 0 < result.confidence <= 1
    assert len(result.alternatives) <= 3
    probabilities = [alt.probability for alt in result.alternatives]
    assert probabilities == sorted(probabilities, reverse=True)
    assert all(p > 0 for p in probabilities)
    assert result.recommended_crop not in [alt.crop for alt in result.alternatives]


def test_prediction_does_not_depend_on_dict_order(prediction_service):
    forward = {key: float(RICE_INPUTS[key]) for key in RICE_INPUTS}
    backward = {key: float(RICE_INPUTS[key]) for key in reversed(list(RICE_INPUTS))}
    assert prediction_service.predict(forward) == prediction_service.predict(backward)


def test_inputs_reach_the_model_in_its_training_column_order(prediction_service):
    """Guards against silently feeding, say, pH into the temperature column."""
    assert list(prediction_service._model.feature_names_in_) == ["N", "P", "K", "temperature", "humidity", "ph", "rainfall"]
    assert prediction_service._feature_order == (
        "nitrogen", "phosphorus", "potassium", "temperature", "humidity", "ph", "rainfall",
    )


def test_service_matches_the_raw_model(prediction_service):
    row = np.array([[90, 42, 43, 20.8, 82, 6.5, 202.9]])
    expected = prediction_service._model.predict(row)[0]
    assert prediction_service.predict({key: float(v) for key, v in RICE_INPUTS.items()}).recommended_crop == expected


def test_every_crop_is_predicted_at_its_reference_midpoint(prediction_service, crop_midpoints):
    for label, inputs in crop_midpoints.items():
        result = prediction_service.predict(inputs)
        assert result.recommended_crop == label, label
        assert result.confidence >= 0.8, (label, result.confidence)


def test_model_split_thresholds_lie_inside_the_documented_reference_ranges(prediction_service):
    """Evidence that the 'model reference range' in the feature specs matches the model."""
    model = prediction_service._model
    names = list(model.feature_names_in_)
    for estimator in model.named_estimators_["et"].estimators_:
        tree = estimator.tree_
        for feature_index, threshold in zip(tree.feature, tree.threshold):
            if feature_index < 0:
                continue
            spec = next(s for s in FEATURES if s.model_name == names[feature_index])
            # the documented ranges are rounded to one decimal, hence the small tolerance
            assert spec.model_min - 0.05 <= threshold <= spec.model_max + 0.05, (spec.key, threshold)


def test_values_beyond_the_reference_range_behave_like_the_edge(prediction_service):
    """Why out-of-range inputs are flagged: the forest cannot extrapolate."""
    base = {key: float(value) for key, value in RICE_INPUTS.items()}
    edge = dict(base, rainfall=FEATURES_BY_KEY["rainfall"].model_max)
    beyond = dict(base, rainfall=2500.0)
    assert prediction_service.predict(edge) == prediction_service.predict(beyond)


def test_unusual_combinations_are_detected(prediction_service, crop_midpoints):
    # every crop's typical conditions are usual ...
    assert not any(prediction_service.is_unusual_combination(v) for v in crop_midpoints.values())
    # ... but each value being possible alone does not make a combination possible
    odd = {"nitrogen": 5.0, "phosphorus": 140.0, "potassium": 200.0, "temperature": 10.0,
           "humidity": 15.0, "ph": 9.5, "rainfall": 290.0}
    assert prediction_service.is_unusual_combination(odd)


def test_random_inputs_are_mostly_flagged_and_real_rows_are_not(prediction_service):
    import csv, random
    rng = random.Random(7)
    specs = [FEATURES_BY_KEY[k] for k in prediction_service._feature_order]
    random_flags = [
        prediction_service.is_unusual_combination(
            {s.key: rng.uniform(s.model_min, s.model_max) for s in specs}
        )
        for _ in range(300)
    ]
    assert sum(random_flags) / 300 > 0.9
    path = BASE_DIR / "data" / "training" / "crop_recommendation.csv"
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))[1:]
    names = ["nitrogen", "phosphorus", "potassium", "temperature", "humidity", "ph", "rainfall"]
    real = [prediction_service.is_unusual_combination(dict(zip(names, map(float, r[:7])))) for r in rows[::4]]
    assert sum(real) / len(real) < 0.05


def test_confidence_is_better_calibrated_than_a_plain_forest(prediction_service):
    """Guards the property that motivated the blend: probabilities that mean something."""
    import csv, random
    rng = random.Random(11)
    path = BASE_DIR / "data" / "training" / "crop_recommendation.csv"
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))[1:]
    names = ["nitrogen", "phosphorus", "potassium", "temperature", "humidity", "ph", "rainfall"]
    spread = [FEATURES_BY_KEY[k].model_max - FEATURES_BY_KEY[k].model_min for k in names]
    confident_hits, confident_total = 0, 0
    for r in rng.sample(rows, 400):
        values = {n: float(v) + rng.gauss(0, 0.05 * s) for n, v, s in zip(names, r[:7], spread)}
        result = prediction_service.predict(values)
        if result.confidence >= 0.9:
            confident_total += 1
            confident_hits += result.recommended_crop == r[7].strip().lower()
    assert confident_total > 50
    assert confident_hits / confident_total > 0.95


def test_feature_importances_cover_all_inputs(prediction_service):
    importances = prediction_service.feature_importances()
    assert set(importances) == {spec.key for spec in FEATURES}
    assert abs(sum(importances.values()) - 1) < 0.01


def test_tampered_model_file_is_refused(crop_service, caplog):
    service = PredictionService(MODEL_PATH, "0" * 64, crop_service.labels)
    assert service.load() is False
    assert not service.is_available
    assert "checksum" in service.load_error
    with pytest.raises(ModelUnavailableError):
        service.predict({key: float(v) for key, v in RICE_INPUTS.items()})


def test_missing_model_file_means_unavailable(crop_service, tmp_path):
    service = PredictionService(tmp_path / "missing.pkl", BUNDLED_MODEL_SHA256, crop_service.labels)
    assert service.load() is False
    assert "not found" in service.load_error


def test_model_with_unexpected_classes_is_refused(crop_service):
    labels = tuple(label for label in crop_service.labels if label != "rice") + ("wheat",)
    service = PredictionService(MODEL_PATH, BUNDLED_MODEL_SHA256, labels)
    assert service.load() is False
    assert "do not match" in service.load_error


# =============================================================== weather
def make_weather(result, **kwargs):
    session = FakeSession(result)
    return WeatherService(API_KEY, BASE_URL, session=session, **kwargs), session


def test_weather_valid_city():
    service, session = make_weather(FakeResponse(200, weather_payload()))
    reading = service.get_current("Pune")
    assert reading.location == "Pune, IN"
    assert reading.temperature == 27.3
    assert reading.humidity == 64.0
    assert reading.rain_last_hour_mm == 0.4
    assert reading.conditions == "light rain"
    assert reading.provider == "openweathermap"
    assert reading.observed_at.tzinfo is not None

    call = session.calls[0]
    assert call["url"] == BASE_URL
    assert call["params"] == {"q": "Pune", "appid": API_KEY, "units": "metric"}
    assert call["timeout"] == (3.05, 6.0)
    assert call["allow_redirects"] is False


def test_weather_city_is_sent_as_a_parameter_not_spliced_into_the_url():
    service, session = make_weather(FakeResponse(200, weather_payload()))
    service.get_current("Pune&appid=attacker")
    assert session.calls[0]["url"] == BASE_URL
    assert session.calls[0]["params"]["q"] == "Pune&appid=attacker"


def test_weather_results_are_cached_per_city():
    service, session = make_weather(FakeResponse(200, weather_payload()))
    first = service.get_current("Pune")
    assert service.get_current("PUNE") is first
    assert len(session.calls) == 1
    service.get_current("Mumbai")
    assert len(session.calls) == 2


def test_weather_cache_expires():
    service, session = make_weather(FakeResponse(200, weather_payload()), cache_ttl_seconds=-1)
    service.get_current("Pune")
    service.get_current("Pune")
    assert len(session.calls) == 2


def test_weather_missing_rain_block_means_no_rain_reading():
    payload = weather_payload()
    del payload["rain"]
    service, _ = make_weather(FakeResponse(200, payload))
    assert service.get_current("Pune").rain_last_hour_mm is None


@pytest.mark.parametrize("rain", [{}, {"3h": 2}, {"1h": "wet"}, {"1h": -3}, "raining", None, [1]])
def test_weather_unusable_rain_values_are_ignored(rain):
    service, _ = make_weather(FakeResponse(200, weather_payload(rain=rain)))
    assert service.get_current("Pune").rain_last_hour_mm is None


def test_weather_invalid_city():
    service, _ = make_weather(FakeResponse(404, {"cod": "404", "message": "city not found"}))
    with pytest.raises(CityNotFoundError) as excinfo:
        service.get_current("Atlantis")
    assert excinfo.value.status_code == 404
    assert excinfo.value.code == "WEATHER_CITY_NOT_FOUND"


@pytest.mark.parametrize("status", [401, 403])
def test_weather_bad_api_key_is_reported_as_unavailable(status):
    service, _ = make_weather(FakeResponse(status, {"cod": status, "message": "Invalid API key"}))
    with pytest.raises(WeatherUnavailableError) as excinfo:
        service.get_current("Pune")
    assert excinfo.value.status_code == 503
    assert "API key" not in excinfo.value.message


def test_weather_provider_rate_limit():
    service, _ = make_weather(FakeResponse(429, {}))
    with pytest.raises(WeatherUnavailableError, match="busy"):
        service.get_current("Pune")


@pytest.mark.parametrize("status", [500, 502, 503, 301, 204])
def test_weather_provider_failure(status):
    service, _ = make_weather(FakeResponse(status, {}))
    with pytest.raises(WeatherProviderError) as excinfo:
        service.get_current("Pune")
    assert excinfo.value.status_code == 502


@pytest.mark.parametrize("exception", [requests.Timeout("slow"), requests.ConnectTimeout("slow"), requests.ReadTimeout("slow")])
def test_weather_timeout(exception):
    service, _ = make_weather(exception)
    with pytest.raises(WeatherTimeoutError) as excinfo:
        service.get_current("Pune")
    assert excinfo.value.status_code == 504


@pytest.mark.parametrize("exception", [requests.ConnectionError("down"), requests.exceptions.SSLError("tls"), requests.RequestException("x")])
def test_weather_network_failure(exception):
    service, _ = make_weather(exception)
    with pytest.raises(WeatherUnavailableError):
        service.get_current("Pune")


@pytest.mark.parametrize(
    "response",
    [
        FakeResponse(200, text="<html>gateway error</html>"),
        FakeResponse(200, text=""),
        FakeResponse(200, payload=[1, 2, 3]),
        FakeResponse(200, payload={"cod": 200}),
        FakeResponse(200, payload={"main": "hot"}),
        FakeResponse(200, payload={"main": {"temp": 25}}),
        FakeResponse(200, payload={"main": {"humidity": 50}}),
        FakeResponse(200, payload={"main": {"temp": "warm", "humidity": 50}}),
        FakeResponse(200, payload={"main": {"temp": 25, "humidity": 140}}),
        FakeResponse(200, payload={"main": {"temp": 999, "humidity": 50}}),
        FakeResponse(200, payload={"main": {"temp": True, "humidity": 50}}),
        FakeResponse(200, text='{"main": {"temp": NaN, "humidity": 50}}'),
        FakeResponse(200, text="x" * 70000),
    ],
)
def test_weather_malformed_responses(response):
    service, _ = make_weather(response)
    with pytest.raises(WeatherProviderError):
        service.get_current("Pune")


def test_weather_odd_but_usable_payload_falls_back_sensibly():
    payload = {"main": {"temp": 21, "humidity": 40}, "name": "", "sys": "x", "weather": "x", "dt": "x"}
    service, _ = make_weather(FakeResponse(200, payload))
    reading = service.get_current("Nashik")
    assert reading.location == "Nashik"
    assert reading.conditions is None


def test_weather_failures_are_not_cached():
    service, session = make_weather(FakeResponse(500, {}))
    for _ in range(2):
        with pytest.raises(WeatherProviderError):
            service.get_current("Pune")
    assert len(session.calls) == 2


def test_weather_without_api_key_makes_no_request():
    session = FakeSession(FakeResponse(200, weather_payload()))
    service = WeatherService(None, BASE_URL, session=session)
    assert not service.is_configured
    with pytest.raises(WeatherNotConfiguredError) as excinfo:
        service.get_current("Pune")
    assert excinfo.value.status_code == 503
    assert session.calls == []


def test_weather_api_key_never_reaches_logs_or_error_messages(caplog):
    """requests puts the full URL (with the key) in exception text; it must not be logged."""
    leaky = requests.ConnectionError(f"HTTPSConnectionPool: Max retries exceeded with url: /weather?q=Pune&appid={API_KEY}")
    service, _ = make_weather(leaky)
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(WeatherUnavailableError) as excinfo:
            service.get_current("Pune")
    assert API_KEY not in caplog.text
    assert API_KEY not in excinfo.value.message
    assert excinfo.value.__cause__ is None and excinfo.value.__suppress_context__
    record = next(r for r in caplog.records if getattr(r, "event", "") == "weather_provider_failure")
    assert API_KEY not in JsonFormatter().format(record)


# ================================================================== mail
MESSAGE = OutgoingEmail(to="asha@example.test", subject="Reset", body="link")


@pytest.mark.parametrize(
    "backend,expected",
    [("disabled", DisabledMailer), ("console", ConsoleMailer), ("memory", MemoryMailer), ("unknown", DisabledMailer)],
)
def test_build_mailer(backend, expected):
    assert isinstance(build_mailer({"MAIL_BACKEND": backend}), expected)


def test_disabled_mailer_cannot_send():
    mailer = DisabledMailer()
    assert mailer.can_send is False
    with pytest.raises(MailDeliveryError):
        mailer.send(MESSAGE)


def test_memory_mailer_records_messages():
    mailer = MemoryMailer()
    mailer.send(MESSAGE)
    assert mailer.outbox == [MESSAGE]


class FakeSmtp:
    instances: list = []

    def __init__(self, host, port, timeout):
        self.host, self.port, self.timeout = host, port, timeout
        self.calls = []
        FakeSmtp.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context=None):
        self.calls.append(("starttls", context is not None))

    def login(self, username, password):
        self.calls.append(("login", username))

    def send_message(self, email):
        self.calls.append(("send", email["To"], email["From"], email["Subject"]))


def _smtp_mailer():
    return build_mailer(
        {
            "MAIL_BACKEND": "smtp",
            "SMTP_HOST": "smtp.example.org",
            "SMTP_PORT": 587,
            "SMTP_USERNAME": "mailer",
            "SMTP_PASSWORD": "smtp-password",
            "SMTP_USE_TLS": True,
            "MAIL_FROM": "no-reply@example.org",
        }
    )


def test_smtp_mailer_uses_tls_then_authenticates_then_sends(monkeypatch):
    FakeSmtp.instances.clear()
    monkeypatch.setattr(smtplib, "SMTP", FakeSmtp)
    mailer = _smtp_mailer()
    assert isinstance(mailer, SmtpMailer) and mailer.can_send
    mailer.send(MESSAGE)
    server = FakeSmtp.instances[0]
    assert (server.host, server.port) == ("smtp.example.org", 587)
    assert server.calls == [
        ("starttls", True),
        ("login", "mailer"),
        ("send", "asha@example.test", "no-reply@example.org", "Reset"),
    ]


def test_smtp_failure_is_reported_without_leaking_details(monkeypatch, caplog):
    def broken(*args, **kwargs):
        raise smtplib.SMTPAuthenticationError(535, b"bad credentials for mailer / smtp-password")

    monkeypatch.setattr(smtplib, "SMTP", broken)
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(MailDeliveryError):
            _smtp_mailer().send(MESSAGE)
    assert "smtp-password" not in caplog.text


# =============================================================== logging
@pytest.mark.parametrize(
    "key",
    ["password", "Password", "new_password", "confirm_password", "secret_key", "api_key", "WEATHER_API_KEY", "appid",
     "token", "csrf_token", "reset_token", "session", "cookie", "Authorization", "govt_id", "government_id", "aadhaar_number"],
)
def test_sensitive_keys_are_recognised(key):
    assert is_sensitive_key(key)


@pytest.mark.parametrize("key", ["email_hash", "user_id", "endpoint", "status", "duration_ms", "recommended_crop", "request_id"])
def test_ordinary_keys_are_not_redacted(key):
    assert not is_sensitive_key(key)


def _format(**extra) -> dict:
    record = logging.LogRecord("app.test", logging.INFO, __file__, 1, "something happened", None, None)
    for key, value in extra.items():
        setattr(record, key, value)
    return json.loads(JsonFormatter().format(record))


def test_log_records_are_structured_json():
    payload = _format(event="unit_test", status=200)
    assert payload["level"] == "INFO"
    assert payload["logger"] == "app.test"
    assert payload["message"] == "something happened"
    assert payload["event"] == "unit_test" and payload["status"] == 200
    assert payload["ts"].endswith("+00:00")


def test_sensitive_fields_are_redacted_at_any_depth():
    payload = _format(
        password="hunter2-plain",
        details={"api_key": "abc123", "nested": {"session": "cookie-value", "ok": 1}, "items": [{"token": "t0ken"}]},
    )
    text = json.dumps(payload)
    for secret in ("hunter2-plain", "abc123", "cookie-value", "t0ken"):
        assert secret not in text
    assert payload["password"] == REDACTED
    assert payload["details"]["nested"]["ok"] == 1
