"""Optionally serve the built React application from the same origin.

In development the React dev server (Vite) serves the pages and proxies
``/api`` to this backend. In production the two can be put behind one reverse
proxy, or - the simplest deployment - this backend can serve the compiled
files in ``frontend/dist`` itself. Either way the browser sees one origin, so
no CORS configuration is needed and the session cookie stays ``SameSite=Lax``.

Any path that is not a real file falls back to ``index.html`` so that
client-side routes such as ``/history`` work when opened directly.
"""

from __future__ import annotations

import logging
from pathlib import Path

from flask import Flask, send_from_directory
from werkzeug.exceptions import NotFound
from werkzeug.security import safe_join

from app.extensions import limiter

logger = logging.getLogger(__name__)

_RESERVED_PREFIXES = ("api/", "health")
_IMMUTABLE_PREFIX = "assets/"  # Vite writes content-hashed files here


def register_frontend(app: Flask) -> bool:
    """Register the catch-all route when a front-end build is available."""
    dist = app.config.get("FRONTEND_DIST")
    if not dist or not Path(dist, "index.html").is_file():
        return False
    dist = str(Path(dist).resolve())

    @app.get("/", defaults={"path": ""})
    @app.get("/<path:path>")
    @limiter.exempt
    def frontend(path: str):
        if path == "api" or path.startswith(_RESERVED_PREFIXES):
            raise NotFound()
        # safe_join returns None for anything that would escape the directory.
        candidate = safe_join(dist, path) if path else None
        if candidate and Path(candidate).is_file():
            response = send_from_directory(dist, path)
            if path.startswith(_IMMUTABLE_PREFIX):
                response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            return response
        response = send_from_directory(dist, "index.html")
        # The entry page names the current hashed assets, so it is never cached.
        response.headers["Cache-Control"] = "no-store"
        return response

    logger.info("serving front-end build", extra={"event": "frontend_enabled"})
    return True
