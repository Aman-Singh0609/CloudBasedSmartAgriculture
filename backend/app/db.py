"""MongoDB connection handling.

The client is created lazily and connection failures are converted into
:class:`~app.errors.DatabaseUnavailableError`, so an unreachable database never
prevents the application from starting or from serving pages that do not need
it.
"""

from __future__ import annotations

import logging
import threading
import time

from flask import current_app
from pymongo import MongoClient
from pymongo.database import Database as MongoDatabase
from pymongo.errors import PyMongoError

from app.errors import DatabaseUnavailableError

logger = logging.getLogger(__name__)

# Collection names. Every collection has a concrete purpose:
USERS = "users"  # accounts
PREDICTIONS = "predictions"  # each crop analysis a user runs
PASSWORD_RESET_TOKENS = "password_reset_tokens"  # hashed, expiring reset tokens
AUDIT_LOGS = "audit_logs"  # security / business events
API_LOGS = "api_logs"  # per-request timing, expires automatically
MIGRATION_HISTORY = "migration_history"  # which migrations ran, and when
BACKUPS = "backups"  # metadata for backups stored on disk

# Collections that hold application data and are included in backups.
# api_logs is operational telemetry with a short TTL and is skipped by default.
BACKUP_COLLECTIONS = (USERS, PREDICTIONS, AUDIT_LOGS, MIGRATION_HISTORY)

# After a failure, best-effort writers (request logs, audit logs) stop trying
# for this long so an outage does not add a connection timeout to every request.
FAILURE_BACKOFF_SECONDS = 30.0


class Database:
    """Lazily connected handle on the application's MongoDB database."""

    def __init__(
        self,
        uri: str | None,
        db_name: str,
        *,
        server_selection_timeout_ms: int = 3000,
        client: MongoClient | None = None,
    ) -> None:
        self._uri = uri
        self.db_name = db_name
        self._timeout_ms = server_selection_timeout_ms
        self._client = client
        self._lock = threading.Lock()
        self._last_failure = 0.0

    @property
    def client(self) -> MongoClient:
        if self._client is None:
            with self._lock:
                if self._client is None:
                    self._client = self._connect()
        return self._client

    def _connect(self) -> MongoClient:
        if not self._uri:
            raise DatabaseUnavailableError()
        try:
            return MongoClient(
                self._uri,
                serverSelectionTimeoutMS=self._timeout_ms,
                connectTimeoutMS=self._timeout_ms,
                tz_aware=True,
                appname="agriculturecloud",
            )
        except PyMongoError as exc:
            # e.g. DNS failure for a mongodb+srv:// URI. The message may contain
            # host names, so only the exception type is logged.
            self.mark_failure()
            logger.error(
                "could not create MongoDB client",
                extra={"event": "database_connect_failed", "error_type": type(exc).__name__},
            )
            raise DatabaseUnavailableError() from exc

    @property
    def db(self) -> MongoDatabase:
        return self.client[self.db_name]

    def ping(self) -> bool:
        """Return True when the server answers a ping."""
        try:
            self.client.admin.command("ping")
        except (PyMongoError, DatabaseUnavailableError):
            self.mark_failure()
            return False
        self._last_failure = 0.0
        return True

    def mark_failure(self) -> None:
        self._last_failure = time.monotonic()

    def recently_failed(self) -> bool:
        """True while the post-failure back-off window is open."""
        return self._last_failure > 0 and (time.monotonic() - self._last_failure) < FAILURE_BACKOFF_SECONDS

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None


def get_database() -> Database:
    return current_app.extensions["database"]


def get_db() -> MongoDatabase:
    """Return the PyMongo database for the current application."""
    return get_database().db
