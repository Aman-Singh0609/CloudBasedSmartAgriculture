"""Standard JSON envelopes used by every endpoint.

Success::

    {"success": true, "data": {...}, "message": "..."}

Failure::

    {"success": false, "error": {"code": "...", "message": "..."}, "request_id": "..."}
"""

from __future__ import annotations

from flask import g, jsonify


def success_response(data: dict | list | None = None, message: str = "OK", status: int = 200):
    payload = {"success": True, "data": data if data is not None else {}, "message": message}
    return jsonify(payload), status


def error_response(code: str, message: str, status: int, *, details: dict | None = None):
    error: dict = {"code": code, "message": message}
    if details:
        error["details"] = details
    payload = {"success": False, "error": error, "request_id": getattr(g, "request_id", None)}
    return jsonify(payload), status
