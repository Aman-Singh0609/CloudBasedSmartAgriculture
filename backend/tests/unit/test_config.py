"""Configuration loading and startup validation."""

import pytest

from app.config import ConfigError, database_name_from_uri, load_config, looks_like_placeholder

GOOD_SECRET = "k" * 40
PROD = {"SECRET_KEY": GOOD_SECRET, "MONGODB_URI": "mongodb://db.internal:27017/agri"}


def test_defaults_to_development():
    config = load_config(environ={})
    assert config["ENV_NAME"] == "development"
    assert config["DEBUG"] is True
    assert config["MONGODB_DB_NAME"] == "agriculture_db"


def test_unknown_environment_is_rejected():
    with pytest.raises(ConfigError, match="Unknown environment"):
        load_config(environ={"FLASK_ENV": "staging"})


@pytest.mark.parametrize("value", ["", "   ", "your_secret_key_here", "<replace-with-a-long-random-value>", "changeme"])
def test_placeholder_detection(value):
    assert looks_like_placeholder(value)


def test_real_value_is_not_a_placeholder():
    assert not looks_like_placeholder(GOOD_SECRET)


# ------------------------------------------------------------------ secret key
@pytest.mark.parametrize("secret", [None, "", "short", "your_super_secret_key_here_12345"])
def test_production_requires_a_strong_secret_key(secret):
    environ = dict(PROD)
    if secret is None:
        environ.pop("SECRET_KEY")
    else:
        environ["SECRET_KEY"] = secret
    with pytest.raises(ConfigError, match="SECRET_KEY"):
        load_config("production", environ)


def test_development_generates_a_temporary_secret_and_warns():
    first = load_config("development", {})
    second = load_config("development", {})
    assert len(first["SECRET_KEY"]) >= 32
    assert first["SECRET_KEY"] != second["SECRET_KEY"]
    assert any("SECRET_KEY" in warning for warning in first["CONFIG_WARNINGS"])


def test_no_hardcoded_default_secret():
    """The old application fell back to the literal 'dev_secret_key'."""
    assert load_config("development", {})["SECRET_KEY"] != "dev_secret_key"


# -------------------------------------------------------------------- database
def test_production_requires_mongodb_uri():
    with pytest.raises(ConfigError, match="MONGODB_URI is required"):
        load_config("production", {"SECRET_KEY": GOOD_SECRET})


def test_placeholder_mongodb_uri_fails_clearly():
    """This is the exact value that made the original application crash on import."""
    uri = "mongodb+srv://<username>:<password>@cluster.mongodb.net/agriculture_db"
    with pytest.raises(ConfigError, match="placeholder"):
        load_config("development", {"MONGODB_URI": uri})


def test_mongodb_uri_must_use_a_mongodb_scheme():
    with pytest.raises(ConfigError, match="must start with"):
        load_config("development", {"MONGODB_URI": "http://example.org/db"})


@pytest.mark.parametrize(
    "uri,expected",
    [
        ("mongodb://localhost:27017/farm", "farm"),
        ("mongodb+srv://u:p@cluster0.abc.mongodb.net/agri?retryWrites=true", "agri"),
        ("mongodb://localhost:27017", None),
        ("mongodb://h1:1,h2:2/multi?replicaSet=rs", "multi"),
    ],
)
def test_database_name_from_uri(uri, expected):
    assert database_name_from_uri(uri) == expected


def test_explicit_database_name_wins():
    config = load_config("development", {"MONGODB_URI": "mongodb://localhost/a", "MONGODB_DB_NAME": "b"})
    assert config["MONGODB_DB_NAME"] == "b"


def test_testing_never_reads_the_real_database_uri():
    config = load_config("testing", {"MONGODB_URI": "mongodb://production-host/agri"})
    assert config["MONGODB_URI"] is None
    assert config["TESTING"] is True


# --------------------------------------------------------------------- weather
@pytest.mark.parametrize("key", ["", "your_openweathermap_api_key_here"])
def test_missing_weather_key_degrades_gracefully(key):
    config = load_config("development", {"WEATHER_API_KEY": key})
    assert config["WEATHER_API_KEY"] is None
    assert any("WEATHER_API_KEY" in warning for warning in config["CONFIG_WARNINGS"])


def test_weather_key_is_loaded_when_present():
    assert load_config("development", {"WEATHER_API_KEY": "abc123"})["WEATHER_API_KEY"] == "abc123"


def test_weather_base_url_must_be_https():
    with pytest.raises(ConfigError, match="https"):
        load_config("development", {"WEATHER_API_BASE_URL": "http://api.openweathermap.org/data/2.5/weather"})


def test_weather_base_url_may_be_plain_http_on_localhost():
    config = load_config("development", {"WEATHER_API_BASE_URL": "http://127.0.0.1:9000/weather"})
    assert config["WEATHER_API_BASE_URL"].startswith("http://127.0.0.1")


# ------------------------------------------------------------------ production
def test_production_hardening_defaults():
    config = load_config("production", PROD)
    assert config["DEBUG"] is False
    assert config["SESSION_COOKIE_SECURE"] is True
    assert config["SESSION_COOKIE_HTTPONLY"] is True
    assert config["SESSION_COOKIE_SAMESITE"] == "Lax"
    assert config["AUTO_MIGRATE"] is False
    assert config["MAIL_BACKEND"] == "disabled"
    assert config["WTF_CSRF_ENABLED"] is True
    assert config["RATELIMIT_ENABLED"] is True


@pytest.mark.parametrize("backend", ["console", "memory"])
def test_production_rejects_mail_backends_that_leak_reset_links(backend):
    with pytest.raises(ConfigError, match="not allowed in production"):
        load_config("production", {**PROD, "MAIL_BACKEND": backend})


def test_smtp_backend_requires_host_and_sender():
    with pytest.raises(ConfigError, match="SMTP_HOST"):
        load_config("development", {"MAIL_BACKEND": "smtp"})


def test_smtp_in_production_requires_https_base_url():
    environ = {**PROD, "MAIL_BACKEND": "smtp", "SMTP_HOST": "smtp.example.org", "MAIL_FROM": "a@example.org"}
    with pytest.raises(ConfigError, match="APP_BASE_URL"):
        load_config("production", environ)
    environ["APP_BASE_URL"] = "https://agri.example.org"
    assert load_config("production", environ)["MAIL_BACKEND"] == "smtp"


def test_unknown_mail_backend_is_rejected():
    with pytest.raises(ConfigError, match="MAIL_BACKEND"):
        load_config("development", {"MAIL_BACKEND": "carrier-pigeon"})


def test_allowed_hosts_become_trusted_hosts():
    config = load_config("production", {**PROD, "ALLOWED_HOSTS": "agri.example.org, www.agri.example.org"})
    assert config["TRUSTED_HOSTS"] == ["agri.example.org", "www.agri.example.org"]


def test_invalid_integer_setting_is_reported():
    with pytest.raises(ConfigError, match="TRUSTED_PROXY_COUNT"):
        load_config("development", {"TRUSTED_PROXY_COUNT": "many"})
