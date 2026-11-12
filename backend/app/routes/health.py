"""Health endpoints for load balancers and monitoring.

``GET /health/live``  process is up (never touches dependencies).
``GET /health``       readiness: database and ML model must both be usable.

Only coarse status words are returned. No host names, versions of
infrastructure, connection strings or error text are exposed.
"""

from __future__ import annotations

from datetime import datetime, timezone

from flask import Blueprint, current_app
from pymongo.errors import PyMongoError

from app.errors import DatabaseUnavailableError
from app.models.schemas import isoformat_utc
from app.utils.responses import success_response
from app.version import __version__

bp = Blueprint("health", __name__)

OK = "ok"
UNAVAILABLE = "unavailable"


def _migration_status(database) -> str:
    from migrations.runner import MigrationRunner

    try:
        runner = MigrationRunner(database.db, current_app.extensions["migrations"])
        return "pending" if runner.pending() else "up_to_date"
    except (PyMongoError, DatabaseUnavailableError):
        return "unknown"


@bp.get("/health/live")
def live():
    return success_response({"status": OK}, "Application is running")


@bp.get("/health")
def ready():
    database = current_app.extensions["database"]
    services = current_app.extensions["services"]

    database_ok = database.ping()
    model_ok = services.prediction.is_available
    migrations = _migration_status(database) if database_ok else "unknown"

    healthy = database_ok and model_ok
    if not healthy:
        status = "unhealthy"
    elif migrations == "pending":
        status = "degraded"
    else:
        status = OK

    data = {
        "status": status,
        "checks": {
            "application": OK,
            "database": OK if database_ok else UNAVAILABLE,
            "model": OK if model_ok else UNAVAILABLE,
            "migrations": migrations,
            # Reported for information only: the advisor works without it.
            "weather": "configured" if services.weather.is_configured else "not_configured",
        },
        "version": __version__,
        "timestamp": isoformat_utc(datetime.now(timezone.utc)),
    }
    message = "All required services are available" if healthy else "One or more required services are unavailable"
    return success_response(data, message, 200 if healthy else 503)
