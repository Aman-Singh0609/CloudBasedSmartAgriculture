"""AgricultureCloud application factory."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from dotenv import load_dotenv
from flask import Flask
from pymongo.errors import PyMongoError
from werkzeug.middleware.proxy_fix import ProxyFix

from app.config import BASE_DIR, load_config
from app.db import Database
from app.errors import DatabaseUnavailableError, register_error_handlers
from app.extensions import csrf, limiter
from app.logging_config import configure_logging
from app.models.features import FEATURES
from app.observability import register_observability
from app.repositories.log_repository import ApiLogRepository, AuditLogRepository
from app.repositories.password_reset_repository import PasswordResetRepository
from app.repositories.prediction_repository import PredictionRepository
from app.repositories.user_repository import UserRepository
from app.services.audit_service import AuditService
from app.services.auth_service import AuthService
from app.services.crop_service import CropService
from app.services.mail_service import Mailer, build_mailer
from app.services.prediction_service import PredictionService
from app.services.recommendation_service import RecommendationService
from app.services.soil_service import SoilService
from app.services.weather_service import WeatherService
from app.utils.security import register_security_headers
from app.version import __version__

logger = logging.getLogger(__name__)

__all__ = ["create_app", "__version__"]


@dataclass(frozen=True)
class Repositories:
    users: UserRepository
    predictions: PredictionRepository
    reset_tokens: PasswordResetRepository
    audit_logs: AuditLogRepository
    api_logs: ApiLogRepository


@dataclass(frozen=True)
class Services:
    crops: CropService
    soil: SoilService
    prediction: PredictionService
    recommendation: RecommendationService
    weather: WeatherService
    auth: AuthService
    audit: AuditService
    mailer: Mailer


def create_app(env_name: str | None = None, overrides: dict | None = None) -> Flask:
    """Build a configured application instance.

    ``overrides`` replaces configuration values after they are loaded; tests
    use it to inject an in-memory database client (``MONGO_CLIENT``).
    """
    if env_name != "testing":
        # Real environment variables win over the .env file.
        load_dotenv(BASE_DIR / ".env", override=False)

    config = load_config(env_name)
    config.update(overrides or {})

    configure_logging(config["LOG_LEVEL"])
    for warning in config.get("CONFIG_WARNINGS", []):
        logger.warning(warning, extra={"event": "config_warning"})

    # The backend is a JSON API: it has no templates or static folder of its own.
    app = Flask(__name__, static_folder=None, template_folder=None)
    app.config.update(config)

    proxies = app.config["TRUSTED_PROXY_COUNT"]
    if proxies:
        # Only trust forwarding headers when told how many proxies sit in front.
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=proxies, x_proto=proxies, x_host=proxies)

    # Registered first so every later hook, including the CSRF and rate-limit
    # checks that can reject a request early, already has a request ID.
    register_observability(app)
    csrf.init_app(app)
    limiter.init_app(app)

    _init_data_layer(app)
    _init_services(app)
    _register_blueprints(app)
    register_security_headers(app)
    register_error_handlers(app)
    _run_startup_checks(app)
    return app


def _init_data_layer(app: Flask) -> None:
    database = Database(
        app.config.get("MONGODB_URI"),
        app.config["MONGODB_DB_NAME"],
        server_selection_timeout_ms=app.config["MONGO_SERVER_SELECTION_TIMEOUT_MS"],
        client=app.config.get("MONGO_CLIENT"),
    )
    app.extensions["database"] = database

    def provider():
        return database.db

    app.extensions["repositories"] = Repositories(
        users=UserRepository(provider),
        predictions=PredictionRepository(provider),
        reset_tokens=PasswordResetRepository(provider),
        audit_logs=AuditLogRepository(provider),
        api_logs=ApiLogRepository(provider),
    )


def _init_services(app: Flask) -> None:
    repositories: Repositories = app.extensions["repositories"]
    database: Database = app.extensions["database"]

    crops = CropService()
    soil = SoilService()
    prediction = app.config.get("PREDICTION_SERVICE") or PredictionService(
        app.config["MODEL_PATH"], app.config["MODEL_SHA256"], crops.labels
    )
    audit = AuditService(repositories.audit_logs, database)
    mailer = build_mailer(app.config)
    weather = WeatherService(
        app.config.get("WEATHER_API_KEY"),
        app.config["WEATHER_API_BASE_URL"],
        connect_timeout=app.config["WEATHER_CONNECT_TIMEOUT_SECONDS"],
        read_timeout=app.config["WEATHER_READ_TIMEOUT_SECONDS"],
        cache_ttl_seconds=app.config["WEATHER_CACHE_TTL_SECONDS"],
    )
    auth = AuthService(
        repositories.users,
        repositories.reset_tokens,
        mailer,
        app_base_url=app.config["APP_BASE_URL"],
        reset_token_ttl_minutes=app.config["PASSWORD_RESET_TOKEN_TTL_MINUTES"],
    )
    recommendation = RecommendationService(
        prediction=prediction, crops=crops, soil=soil, predictions=repositories.predictions, audit=audit
    )

    app.extensions["audit"] = audit
    app.extensions["services"] = Services(
        crops=crops,
        soil=soil,
        prediction=prediction,
        recommendation=recommendation,
        weather=weather,
        auth=auth,
        audit=audit,
        mailer=mailer,
    )


def _register_blueprints(app: Flask) -> None:
    from app.routes import auth, health, main, prediction, weather
    from app.routes.frontend import register_frontend

    for module in (main, auth, prediction, weather, health):
        app.register_blueprint(module.bp)

    # Health probes must stay reachable under load and carry no session state.
    limiter.exempt(health.bp)
    csrf.exempt(health.bp)

    register_frontend(app)


def _run_startup_checks(app: Flask) -> None:
    """Verify the ML model and crop data, then bring the database schema up to date."""
    services: Services = app.extensions["services"]

    if services.prediction.load():
        # Raises CropDataError if the model can output a crop we cannot describe.
        services.crops.validate_against_model(services.prediction.labels)

    app.extensions["feature_specs"] = [spec.to_dict() for spec in FEATURES]
    _check_migrations(app)


def _check_migrations(app: Flask) -> None:
    from migrations.runner import MigrationError, MigrationRunner, discover

    migrations = discover()
    app.extensions["migrations"] = migrations
    database: Database = app.extensions["database"]
    try:
        runner = MigrationRunner(database.db, migrations)
        pending = runner.pending()
        if pending and app.config["AUTO_MIGRATE"]:
            # Never passes allow_irreversible: a migration that would destroy
            # existing data always needs an operator's explicit decision.
            runner.upgrade()
            pending = runner.pending()
    except (PyMongoError, DatabaseUnavailableError) as exc:
        database.mark_failure()
        logger.error(
            "database unavailable at startup; pages that need it will return 503 until it is reachable",
            extra={"event": "startup_database_unavailable", "error_type": type(exc).__name__},
        )
        return
    except MigrationError as exc:
        logger.error("automatic migration failed: %s", exc, extra={"event": "startup_migration_failed"})
        return

    if pending:
        logger.warning(
            "database migrations are pending; run: python scripts/migrate.py up",
            extra={"event": "migrations_pending", "pending": [migration.name for migration in pending]},
        )
