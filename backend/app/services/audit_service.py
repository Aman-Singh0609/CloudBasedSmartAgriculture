"""Audit trail for security-relevant and business-relevant events.

Each event is written to the structured application log and, best effort, to
the ``audit_logs`` collection. Recording an audit event must never break the
request that triggered it, so database failures here are logged and swallowed.

Nothing sensitive is stored: no passwords, tokens, cookies or API keys. Login
identifiers are stored only as a keyed hash, because people occasionally type
their password into the email field.
"""

from __future__ import annotations

import logging
from typing import Any

from flask import g, has_request_context
from pymongo.errors import PyMongoError

from app.db import Database
from app.errors import DatabaseUnavailableError
from app.repositories.log_repository import AuditLogRepository
from app.utils.security import is_sensitive_key

logger = logging.getLogger("app.audit")

_MAX_DETAIL_STRING = 200


def _clean_details(details: dict[str, Any] | None) -> dict[str, Any]:
    """Keep only simple, non-sensitive values."""
    cleaned: dict[str, Any] = {}
    for key, value in (details or {}).items():
        if is_sensitive_key(key):
            continue
        if isinstance(value, str):
            cleaned[key] = value[:_MAX_DETAIL_STRING]
        elif isinstance(value, (bool, int, float)) or value is None:
            cleaned[key] = value
        elif isinstance(value, (list, tuple)) and all(isinstance(item, (str, int, float)) for item in value):
            cleaned[key] = [item[:_MAX_DETAIL_STRING] if isinstance(item, str) else item for item in value][:50]
        elif isinstance(value, dict):
            cleaned[key] = _clean_details(value)
    return cleaned


class AuditService:
    def __init__(self, repository: AuditLogRepository, database: Database) -> None:
        self._repository = repository
        self._database = database

    def record(
        self,
        event: str,
        *,
        outcome: str = "success",
        user_id: Any = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        request_id = getattr(g, "request_id", None) if has_request_context() else None
        if user_id is None and has_request_context():
            current = getattr(g, "current_user", None)
            user_id = current.id if current is not None else None
        clean = _clean_details(details)

        logger.info(
            "audit: %s (%s)",
            event,
            outcome,
            extra={
                "event": "audit",
                "audit_event": event,
                "outcome": outcome,
                "user_id": str(user_id) if user_id is not None else None,
                "details": clean,
            },
        )

        if self._database.recently_failed():
            return
        try:
            self._repository.insert(
                {
                    "event": event,
                    "outcome": outcome,
                    "user_id": user_id,
                    "request_id": request_id,
                    "details": clean,
                }
            )
        except (PyMongoError, DatabaseUnavailableError) as exc:
            self._database.mark_failure()
            logger.error(
                "audit event could not be stored",
                extra={"event": "audit_store_failed", "audit_event": event, "error_type": type(exc).__name__},
            )
