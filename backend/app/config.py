"""Configuration for the development, testing and production environments.

All secrets come from environment variables (optionally loaded from a local
``.env`` file by :func:`app.create_app`). Nothing sensitive is hard-coded here.
Configuration that is truly required is validated when the application starts
so that a misconfigured deployment fails immediately with a clear message
instead of producing an obscure runtime error later.
"""

from __future__ import annotations

import os
import secrets
from collections.abc import Mapping
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

BASE_DIR = Path(__file__).resolve().parent.parent

ENVIRONMENTS = ("development", "testing", "production")
DEFAULT_DB_NAME = "agriculture_db"
MIN_SECRET_KEY_LENGTH = 32

# SHA-256 of the bundled crop_recommendation_model.pkl. The model is a pickle,
# and unpickling executes code, so the file is only loaded when its digest
# matches. Override with MODEL_SHA256 when deliberately shipping a new model.
BUNDLED_MODEL_SHA256 = "f7928fa23ca95b0453c0ae86dc39917b9d6a71b549834955fe44c42c870c527e"

_PLACEHOLDER_MARKERS = (
    "<",
    ">",
    "your_",
    "your-",
    "changeme",
    "change-me",
    "change_me",
    "replace-with",
    "replace_with",
    "placeholder",
)
_MAIL_BACKENDS = ("disabled", "console", "smtp", "memory")


class ConfigError(RuntimeError):
    """Raised at startup when required configuration is missing or invalid."""


def looks_like_placeholder(value: str | None) -> bool:
    """Return True for empty values and for values copied from ``.env.example``."""
    if value is None or not value.strip():
        return True
    lowered = value.strip().lower()
    return any(marker in lowered for marker in _PLACEHOLDER_MARKERS)


def _as_bool(value: str | None, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _as_int(name: str, value: str | None, default: int, *, minimum: int = 0) -> int:
    if value is None or value.strip() == "":
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer.") from exc
    if parsed < minimum:
        raise ConfigError(f"{name} must be at least {minimum}.")
    return parsed


def database_name_from_uri(uri: str) -> str | None:
    """Extract the database name from a MongoDB URI without any network access."""
    path = urlsplit(uri).path.lstrip("/")
    return path or None


def resolve_environment(env_name: str | None, environ: Mapping[str, str]) -> str:
    name = (env_name or environ.get("FLASK_ENV") or "development").strip().lower()
    if name not in ENVIRONMENTS:
        raise ConfigError(
            f"Unknown environment '{name}'. FLASK_ENV must be one of: {', '.join(ENVIRONMENTS)}."
        )
    return name


def load_config(env_name: str | None = None, environ: Mapping[str, str] | None = None) -> dict:
    """Build and validate the configuration mapping for one environment.

    Returns a dict suitable for ``app.config.update``. Non-fatal problems are
    collected under ``CONFIG_WARNINGS`` so the application can log them once
    logging has been configured.
    """
    environ = os.environ if environ is None else environ
    env = resolve_environment(env_name, environ)
    warnings: list[str] = []

    config: dict = {
        "ENV_NAME": env,
        "DEBUG": env == "development",
        "TESTING": env == "testing",
        # --- Sessions -----------------------------------------------------
        "SESSION_COOKIE_NAME": "agricloud_session",
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SAMESITE": "Lax",
        "SESSION_COOKIE_SECURE": env == "production",
        "PERMANENT_SESSION_LIFETIME": timedelta(
            hours=_as_int("SESSION_LIFETIME_HOURS", environ.get("SESSION_LIFETIME_HOURS"), 12, minimum=1)
        ),
        # --- Request limits -----------------------------------------------
        "MAX_CONTENT_LENGTH": 64 * 1024,
        "MAX_FORM_MEMORY_SIZE": 64 * 1024,
        # --- CSRF ---------------------------------------------------------
        "WTF_CSRF_ENABLED": True,
        "WTF_CSRF_TIME_LIMIT": 12 * 60 * 60,
        # --- Rate limiting ------------------------------------------------
        "RATELIMIT_ENABLED": _as_bool(environ.get("RATELIMIT_ENABLED"), True),
        "RATELIMIT_STORAGE_URI": environ.get("RATELIMIT_STORAGE_URI", "memory://"),
        "RATELIMIT_HEADERS_ENABLED": True,
        "RATELIMIT_STRATEGY": "fixed-window",
        # --- Database -----------------------------------------------------
        "MONGO_SERVER_SELECTION_TIMEOUT_MS": _as_int(
            "MONGO_SERVER_SELECTION_TIMEOUT_MS",
            environ.get("MONGO_SERVER_SELECTION_TIMEOUT_MS"),
            3000,
            minimum=100,
        ),
        "AUTO_MIGRATE": _as_bool(environ.get("AUTO_MIGRATE"), env == "development"),
        "API_LOG_PERSIST": _as_bool(environ.get("API_LOG_PERSIST"), True),
        "BACKUP_DIR": environ.get("BACKUP_DIR", str(BASE_DIR / "backups")),
        # --- ML model -----------------------------------------------------
        "MODEL_PATH": environ.get("MODEL_PATH", str(BASE_DIR / "crop_recommendation_model.pkl")),
        "MODEL_SHA256": environ.get("MODEL_SHA256", BUNDLED_MODEL_SHA256).strip().lower(),
        # --- Weather provider ---------------------------------------------
        "WEATHER_API_BASE_URL": environ.get(
            "WEATHER_API_BASE_URL", "https://api.openweathermap.org/data/2.5/weather"
        ),
        "WEATHER_CONNECT_TIMEOUT_SECONDS": 3.05,
        "WEATHER_READ_TIMEOUT_SECONDS": 6.0,
        "WEATHER_CACHE_TTL_SECONDS": 600,
        # --- Password reset / mail ----------------------------------------
        "PASSWORD_RESET_TOKEN_TTL_MINUTES": 30,
        "MAIL_FROM": environ.get("MAIL_FROM", ""),
        "SMTP_HOST": environ.get("SMTP_HOST", ""),
        "SMTP_PORT": _as_int("SMTP_PORT", environ.get("SMTP_PORT"), 587, minimum=1),
        "SMTP_USERNAME": environ.get("SMTP_USERNAME", ""),
        "SMTP_PASSWORD": environ.get("SMTP_PASSWORD", ""),
        "SMTP_USE_TLS": _as_bool(environ.get("SMTP_USE_TLS"), True),
        # --- Deployment ---------------------------------------------------
        "APP_BASE_URL": environ.get("APP_BASE_URL", "http://localhost:5173").rstrip("/"),
        "TRUSTED_PROXY_COUNT": _as_int(
            "TRUSTED_PROXY_COUNT", environ.get("TRUSTED_PROXY_COUNT"), 0, minimum=0
        ),
        "LOG_LEVEL": environ.get("LOG_LEVEL", "INFO").upper(),
        # Compiled React app. Served by this backend when the directory exists.
        "FRONTEND_DIST": environ.get("FRONTEND_DIST", str(BASE_DIR.parent / "frontend" / "dist")),
    }

    allowed_hosts = [h.strip() for h in environ.get("ALLOWED_HOSTS", "").split(",") if h.strip()]
    if allowed_hosts:
        config["TRUSTED_HOSTS"] = allowed_hosts

    _configure_secret_key(config, env, environ, warnings)
    _configure_database(config, env, environ)
    _configure_weather(config, environ, warnings)
    _configure_mail(config, env, environ, warnings)

    if env == "production":
        if not _as_bool(environ.get("SESSION_COOKIE_SECURE"), True):
            config["SESSION_COOKIE_SECURE"] = False
            warnings.append(
                "SESSION_COOKIE_SECURE is disabled in production; session cookies will be sent over "
                "plain HTTP. Only do this behind a trusted TLS-terminating setup you control."
            )
        if not allowed_hosts:
            warnings.append(
                "ALLOWED_HOSTS is not set; Host header validation is disabled. Set it to your "
                "public hostname(s) in production."
            )
        if config["RATELIMIT_STORAGE_URI"].startswith("memory://"):
            warnings.append(
                "RATELIMIT_STORAGE_URI is in-memory; rate limits are per process and reset on "
                "restart. Use a shared store (e.g. redis:// or mongodb://) when running several workers."
            )

    if env == "testing":
        config["WTF_CSRF_ENABLED"] = False
        config["RATELIMIT_ENABLED"] = False
        config["AUTO_MIGRATE"] = True
        config["API_LOG_PERSIST"] = True
        config["FRONTEND_DIST"] = None

    config["CONFIG_WARNINGS"] = warnings
    return config


def _configure_secret_key(config: dict, env: str, environ: Mapping[str, str], warnings: list[str]) -> None:
    secret = environ.get("SECRET_KEY", "")
    if env == "testing":
        # Tests never touch real data; a per-process random key is sufficient.
        config["SECRET_KEY"] = secret if not looks_like_placeholder(secret) else secrets.token_hex(32)
        return

    invalid = looks_like_placeholder(secret) or len(secret.strip()) < MIN_SECRET_KEY_LENGTH
    if invalid and env == "production":
        raise ConfigError(
            "SECRET_KEY is missing, too short or still a placeholder. Set a random value of at "
            f"least {MIN_SECRET_KEY_LENGTH} characters, for example the output of: "
            'python -c "import secrets; print(secrets.token_hex(32))"'
        )
    if invalid:
        config["SECRET_KEY"] = secrets.token_hex(32)
        warnings.append(
            "SECRET_KEY is missing or a placeholder; using a temporary random key. Sessions will "
            "be invalidated every time the server restarts. Set SECRET_KEY in .env to fix this."
        )
        return
    config["SECRET_KEY"] = secret.strip()


def _configure_database(config: dict, env: str, environ: Mapping[str, str]) -> None:
    if env == "testing":
        # Tests use an in-memory mongomock database unless TEST_MONGODB_URI
        # points at a disposable real server. They never read MONGODB_URI, so
        # a developer's real database cannot be touched by the test suite.
        uri = environ.get("TEST_MONGODB_URI", "").strip()
        config["MONGODB_URI"] = uri or None
        config["MONGODB_DB_NAME"] = environ.get("TEST_MONGODB_DB_NAME", "agri_test")
        return

    uri = environ.get("MONGODB_URI", "").strip()
    if not uri:
        if env == "production":
            raise ConfigError("MONGODB_URI is required in production.")
        uri = f"mongodb://127.0.0.1:27017/{DEFAULT_DB_NAME}"
    if "<" in uri or ">" in uri:
        raise ConfigError(
            "MONGODB_URI still contains placeholder text such as <username> or <password>. "
            "Replace it with your real MongoDB connection string in .env."
        )
    if not uri.startswith(("mongodb://", "mongodb+srv://")):
        raise ConfigError("MONGODB_URI must start with mongodb:// or mongodb+srv://.")

    config["MONGODB_URI"] = uri
    config["MONGODB_DB_NAME"] = (
        environ.get("MONGODB_DB_NAME", "").strip() or database_name_from_uri(uri) or DEFAULT_DB_NAME
    )


def _configure_weather(config: dict, environ: Mapping[str, str], warnings: list[str]) -> None:
    key = environ.get("WEATHER_API_KEY", "").strip()
    if looks_like_placeholder(key):
        # Weather is optional: the advisor keeps working with manual entry.
        config["WEATHER_API_KEY"] = None
        if config["ENV_NAME"] != "testing":
            warnings.append(
                "WEATHER_API_KEY is not configured; the weather lookup is disabled and users "
                "must enter temperature and humidity manually."
            )
    else:
        config["WEATHER_API_KEY"] = key

    base_url = config["WEATHER_API_BASE_URL"]
    is_local = urlsplit(base_url).hostname in ("127.0.0.1", "localhost", "::1")
    if not base_url.startswith("https://") and not is_local:
        raise ConfigError("WEATHER_API_BASE_URL must use https:// so the API key is never sent in clear text.")


def _configure_mail(config: dict, env: str, environ: Mapping[str, str], warnings: list[str]) -> None:
    default_backend = {"development": "console", "testing": "memory", "production": "disabled"}[env]
    backend = environ.get("MAIL_BACKEND", default_backend).strip().lower()
    if backend not in _MAIL_BACKENDS:
        raise ConfigError(f"MAIL_BACKEND must be one of: {', '.join(_MAIL_BACKENDS)}.")
    if env == "production" and backend in ("console", "memory"):
        raise ConfigError(
            f"MAIL_BACKEND={backend} writes password-reset links to the log or memory and is not "
            "allowed in production. Use 'smtp' or 'disabled'."
        )
    if backend == "smtp":
        missing = [name for name in ("SMTP_HOST", "MAIL_FROM") if not config[name]]
        if missing:
            raise ConfigError(f"MAIL_BACKEND=smtp requires: {', '.join(missing)}.")
        if env == "production" and not config["APP_BASE_URL"].startswith("https://"):
            raise ConfigError("APP_BASE_URL must be an https:// URL in production when password reset is enabled.")
    if backend == "disabled" and env == "production":
        warnings.append(
            "MAIL_BACKEND is disabled; password reset by email is unavailable and its pages return 404."
        )
    config["MAIL_BACKEND"] = backend
