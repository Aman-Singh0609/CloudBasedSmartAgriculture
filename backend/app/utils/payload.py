"""Reading request bodies."""

from __future__ import annotations

from flask import request

from app.errors import BadRequestError, UnsupportedMediaTypeError


def request_payload() -> dict:
    """Return the JSON object sent in the request body.

    The API accepts JSON only. Besides keeping one input format, this is a
    second line of defence against cross-site request forgery: a plain HTML
    form on another site cannot send ``application/json``.
    """
    if not request.is_json:
        raise UnsupportedMediaTypeError()
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise BadRequestError("The request body must be a valid JSON object.")
    return data
