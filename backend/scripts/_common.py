"""Shared setup for the command-line administration scripts."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from dotenv import load_dotenv
from pymongo.database import Database as MongoDatabase
from pymongo.errors import PyMongoError

from app.config import BASE_DIR, ConfigError, load_config
from app.db import AUDIT_LOGS, Database
from app.errors import DatabaseUnavailableError
from app.logging_config import configure_logging

logger = logging.getLogger("app.admin")


class ScriptError(RuntimeError):
    """A problem that should end the script with a readable message."""


def load_settings() -> dict:
    """Load configuration the same way the web application does."""
    load_dotenv(BASE_DIR / ".env", override=False)
    try:
        config = load_config()
    except ConfigError as exc:
        raise ScriptError(f"Configuration error: {exc}") from exc
    configure_logging(config["LOG_LEVEL"])
    return config


def open_database(config: dict) -> MongoDatabase:
    """Connect to the configured database and confirm it is reachable."""
    database = Database(
        config.get("MONGODB_URI"),
        config["MONGODB_DB_NAME"],
        server_selection_timeout_ms=config["MONGO_SERVER_SELECTION_TIMEOUT_MS"],
    )
    try:
        database.client.admin.command("ping")
    except (PyMongoError, DatabaseUnavailableError) as exc:
        raise ScriptError(
            "Could not connect to MongoDB. Check MONGODB_URI and that the server is running "
            f"({type(exc).__name__})."
        ) from exc
    return database.db


def record_admin_event(db: MongoDatabase, event: str, *, outcome: str = "success", details: dict | None = None) -> None:
    """Write an administrative action to the audit log (best effort) and the application log."""
    logger.info(
        "admin: %s (%s)", event, outcome, extra={"event": "audit", "audit_event": event, "outcome": outcome}
    )
    try:
        db[AUDIT_LOGS].insert_one(
            {
                "event": event,
                "outcome": outcome,
                "user_id": None,
                "request_id": None,
                "details": details or {},
                "created_at": datetime.now(timezone.utc),
            }
        )
    except PyMongoError:
        logger.error("admin event could not be stored", extra={"event": "audit_store_failed", "audit_event": event})
