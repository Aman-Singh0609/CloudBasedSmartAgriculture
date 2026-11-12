"""Request correlation IDs and per-request timing.

For every request (static files excluded) the application records the
endpoint, method, status code, duration and a request ID. The record goes to
the structured log and, best effort, to the ``api_logs`` collection, where a
TTL index expires it. Request bodies, query strings and headers are never
recorded, so nothing sensitive can end up in telemetry.
"""

from __future__ import annotations

import logging
import re
import time
import uuid

from flask import Flask, g, request, session
from pymongo.errors import PyMongoError

from app.errors import DatabaseUnavailableError
from app.utils.security import SESSION_USER_ID

logger = logging.getLogger("app.request")

REQUEST_ID_HEADER = "X-Request-ID"
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
_UNLOGGED_ENDPOINTS = frozenset({"static"})


def _new_request_id() -> str:
    return uuid.uuid4().hex


def register_observability(app: Flask) -> None:
    @app.before_request
    def start_request() -> None:
        g.request_started = time.perf_counter()
        # Honour an upstream ID only when it is short and plainly formatted, so
        # a client cannot inject arbitrary text into the logs.
        inbound = request.headers.get(REQUEST_ID_HEADER, "")
        g.request_id = inbound if _SAFE_REQUEST_ID.match(inbound) else _new_request_id()

    @app.after_request
    def finish_request(response):
        request_id = getattr(g, "request_id", None) or _new_request_id()
        response.headers[REQUEST_ID_HEADER] = request_id

        if request.endpoint in _UNLOGGED_ENDPOINTS:
            return response

        started = getattr(g, "request_started", None)
        duration_ms = round((time.perf_counter() - started) * 1000, 2) if started is not None else None
        current_user = g.get("current_user")
        user_id = str(current_user.id) if current_user is not None else session.get(SESSION_USER_ID)

        entry = {
            "endpoint": request.endpoint or "unmatched",
            "route": request.url_rule.rule if request.url_rule is not None else None,
            "method": request.method,
            "status": response.status_code,
            "duration_ms": duration_ms,
            "request_id": request_id,
            "user_id": user_id,
        }
        logger.info(
            "%s %s -> %s",
            request.method,
            entry["route"] or "unmatched",
            response.status_code,
            extra={"event": "http_request", **entry},
        )
        _persist(app, entry)
        return response


def _persist(app: Flask, entry: dict) -> None:
    if not app.config.get("API_LOG_PERSIST"):
        return
    database = app.extensions["database"]
    if database.recently_failed():
        return
    try:
        app.extensions["repositories"].api_logs.insert(dict(entry))
    except (PyMongoError, DatabaseUnavailableError):
        database.mark_failure()
