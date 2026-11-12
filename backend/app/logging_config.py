"""Structured (JSON lines) logging.

Every record is emitted as one JSON object with a timestamp, level, logger,
message and any structured fields passed through ``extra=``. The current
request ID is attached automatically. As defence in depth, any field whose
name looks sensitive (password, token, cookie, API key, ...) is redacted
before the record is written, and tracebacks are kept server-side only.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

from flask import g, has_request_context

from app.utils.security import is_sensitive_key

_STANDARD_ATTRIBUTES = frozenset(
    logging.LogRecord("x", logging.INFO, "x", 0, "x", None, None).__dict__
) | {"message", "asctime", "taskName"}

REDACTED = "[REDACTED]"


def _redact(value):
    if isinstance(value, dict):
        return {key: (REDACTED if is_sensitive_key(key) else _redact(item)) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if has_request_context():
            request_id = getattr(g, "request_id", None)
            if request_id:
                payload["request_id"] = request_id

        for key, value in record.__dict__.items():
            if key in _STANDARD_ATTRIBUTES or key.startswith("_"):
                continue
            payload[key] = REDACTED if is_sensitive_key(key) else _redact(value)

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    """Install the JSON handler on the root logger (idempotent)."""
    root = logging.getLogger()
    root.setLevel(getattr(logging, level, logging.INFO))

    for handler in root.handlers:
        if getattr(handler, "_agricloud", False):
            handler.setLevel(root.level)
            return

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    handler._agricloud = True  # marker so repeated create_app() calls do not stack handlers
    root.addHandler(handler)

    # The driver and HTTP client log connection details at DEBUG; keep them quiet.
    for noisy in ("pymongo", "urllib3", "requests"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
