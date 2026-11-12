"""Shared pytest fixtures.

Database backend
----------------
By default every test runs against an in-memory ``mongomock`` database, so the
suite needs no services. Set ``TEST_MONGODB_URI`` to run the same tests against
a real MongoDB server (and to enable the ``real_mongo`` tests that check query
plans and TTL indexes)::

    TEST_MONGODB_URI=mongodb://127.0.0.1:27017 pytest

Each test gets its own database named ``agri_test_<random>``, dropped
afterwards. The suite never reads ``MONGODB_URI``, so it cannot touch a
development or production database.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import mongomock
import pytest
from pymongo import MongoClient

from app import create_app
from app.config import BASE_DIR, BUNDLED_MODEL_SHA256
from app.extensions import limiter
from app.services.crop_service import CropService
from app.services.prediction_service import PredictionService

REAL_MONGO_URI = os.environ.get("TEST_MONGODB_URI", "").strip()
TEST_DB_PREFIX = "agri_test_"

TEST_PASSWORD = "Tr4ctor-Lane-Meadow"
OTHER_PASSWORD = "Orch4rd-Gate-Pasture"

# A set of readings the model confidently maps to rice.
RICE_INPUTS = {
    "nitrogen": 90,
    "phosphorus": 42,
    "potassium": 43,
    "temperature": 20.8,
    "humidity": 82,
    "ph": 6.5,
    "rainfall": 202.9,
}


def pytest_collection_modifyitems(config, items):
    if REAL_MONGO_URI:
        return
    skip = pytest.mark.skip(reason="BLOCKED: needs a real MongoDB (set TEST_MONGODB_URI)")
    for item in items:
        if "real_mongo" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def mongo_client():
    if REAL_MONGO_URI:
        client = MongoClient(REAL_MONGO_URI, serverSelectionTimeoutMS=3000, tz_aware=True)
        client.admin.command("ping")
    else:
        client = mongomock.MongoClient(tz_aware=True)
    yield client
    client.close()


@pytest.fixture(scope="session")
def using_real_mongo() -> bool:
    return bool(REAL_MONGO_URI)


@pytest.fixture(scope="session")
def crop_service() -> CropService:
    return CropService()


@pytest.fixture(scope="session")
def prediction_service(crop_service) -> PredictionService:
    """The real bundled model, loaded once for the whole test session."""
    service = PredictionService(BASE_DIR / "crop_recommendation_model.pkl", BUNDLED_MODEL_SHA256, crop_service.labels)
    assert service.load(), service.load_error
    return service


@pytest.fixture
def db_name() -> str:
    return f"{TEST_DB_PREFIX}{uuid.uuid4().hex[:12]}"


@pytest.fixture
def raw_db(mongo_client, db_name):
    """An empty database with no migrations applied."""
    assert db_name.startswith(TEST_DB_PREFIX)
    yield mongo_client[db_name]
    mongo_client.drop_database(db_name)


def _build_app(mongo_client, db_name, prediction_service, tmp_path, **overrides):
    assert db_name.startswith(TEST_DB_PREFIX)
    config = {
        "MONGO_CLIENT": mongo_client,
        "MONGODB_DB_NAME": db_name,
        "PREDICTION_SERVICE": prediction_service,
        "BACKUP_DIR": str(tmp_path / "backups"),
        "WEATHER_API_KEY": "unit-test-weather-key",
        "APP_BASE_URL": "http://localhost",
    }
    config.update(overrides)
    return create_app("testing", overrides=config)


def reset_rate_limits() -> None:
    """Clear rate-limit counters (the limiter only has storage when enabled)."""
    if getattr(limiter, "_storage", None) is not None:
        limiter.reset()


@pytest.fixture
def app_factory(mongo_client, prediction_service, tmp_path):
    """Build extra application instances with configuration overrides."""
    created: list[str] = []

    def factory(**overrides):
        name = overrides.pop("db_name", None) or f"{TEST_DB_PREFIX}{uuid.uuid4().hex[:12]}"
        created.append(name)
        return _build_app(mongo_client, name, prediction_service, tmp_path, **overrides)

    yield factory
    reset_rate_limits()
    for name in created:
        mongo_client.drop_database(name)


@pytest.fixture
def app(mongo_client, db_name, prediction_service, tmp_path):
    application = _build_app(mongo_client, db_name, prediction_service, tmp_path)
    yield application
    mongo_client.drop_database(db_name)


@pytest.fixture
def db(app):
    """The migrated application database."""
    return app.extensions["database"].db


@pytest.fixture
def client(app):
    return app.test_client()


def signup(client, *, name="Asha Patel", email="asha@example.test", password=TEST_PASSWORD, **extra):
    data = {"name": name, "email": email, "password": password, "confirm_password": password}
    data.update(extra)
    return client.post("/api/auth/signup", json=data)


def login(client, *, email="asha@example.test", password=TEST_PASSWORD, **extra):
    data = {"email": email, "password": password}
    data.update(extra)
    return client.post("/api/auth/login", json=data)


def register_and_login(app, *, name="Asha Patel", email="asha@example.test", password=TEST_PASSWORD):
    """Return a test client that is logged in as a freshly created user."""
    test_client = app.test_client()
    assert signup(test_client, name=name, email=email, password=password).status_code == 201
    assert login(test_client, email=email, password=password).status_code == 200
    return test_client


@pytest.fixture
def auth_client(app):
    return register_and_login(app)


@pytest.fixture
def other_client(app):
    """A second, different logged-in user."""
    return register_and_login(app, name="Ravi Kumar", email="ravi@example.test", password=OTHER_PASSWORD)


@pytest.fixture
def user_id(db, auth_client):
    return db["users"].find_one({"email": "asha@example.test"})["_id"]


def predict_json(client, inputs=None, **overrides):
    payload = dict(RICE_INPUTS if inputs is None else inputs)
    payload.update(overrides)
    return client.post("/api/predictions", json=payload)


@pytest.fixture
def crop_midpoints(crop_service) -> dict[str, dict[str, float]]:
    """For each crop, the midpoint of its typical ranges (a point the model maps to that crop)."""
    return {
        label: {key: round((low + high) / 2, 2) for key, (low, high) in crop_service.get(label).typical_ranges.items()}
        for label in crop_service.labels
    }


@pytest.fixture(scope="session")
def demo_user() -> dict:
    return json.loads((Path(__file__).parent / "fixtures" / "demo_user.json").read_text(encoding="utf-8"))


class FakeResponse:
    """Minimal stand-in for ``requests.Response``."""

    def __init__(self, status_code=200, payload=None, text=None):
        self.status_code = status_code
        self._payload = payload
        body = text if text is not None else json.dumps(payload if payload is not None else {})
        self.content = body.encode("utf-8")
        self._text = body

    def json(self):
        return json.loads(self._text)


class FakeSession:
    """Stand-in for ``requests.Session`` that records calls and replays a result."""

    def __init__(self, result=None):
        self.result = result
        self.calls: list[dict] = []

    def get(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def weather_payload(**overrides) -> dict:
    payload = {
        "name": "Pune",
        "sys": {"country": "IN"},
        "main": {"temp": 27.34, "humidity": 64},
        "weather": [{"description": "light rain"}],
        "rain": {"1h": 0.4},
        "dt": 1794300000,
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def fake_weather(app):
    """Replace the weather provider's HTTP session with a controllable fake."""
    service = app.extensions["services"].weather
    session = FakeSession(FakeResponse(200, weather_payload()))
    service._session = session
    service._cache.clear()
    return session


def assert_error(response, status: int, code: str) -> dict:
    """Check the standard error envelope and return the error object."""
    assert response.status_code == status, response.get_data(as_text=True)
    body = response.get_json()
    assert body["success"] is False
    assert body["error"]["code"] == code
    assert isinstance(body["error"]["message"], str) and body["error"]["message"]
    assert body["request_id"]
    assert "data" not in body
    return body["error"]


def assert_success(response, status: int = 200) -> dict:
    """Check the standard success envelope and return its data."""
    assert response.status_code == status, response.get_data(as_text=True)
    body = response.get_json()
    assert body["success"] is True
    assert isinstance(body["message"], str) and body["message"]
    assert "error" not in body
    return body["data"]
