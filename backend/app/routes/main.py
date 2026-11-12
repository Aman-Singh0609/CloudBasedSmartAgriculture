"""Public bootstrap endpoints used by the front end."""

from __future__ import annotations

from flask import Blueprint, current_app
from flask_wtf.csrf import generate_csrf

from app.models.features import GROUP_CLIMATE, GROUP_CONDITION, GROUP_NUTRIENTS
from app.utils.responses import success_response
from app.version import __version__

bp = Blueprint("main", __name__, url_prefix="/api")

FIELD_GROUPS = (
    {
        "key": GROUP_NUTRIENTS,
        "title": "Soil nutrients",
        "hint": "From a soil test report. Enter the values as printed on the report.",
    },
    {
        "key": GROUP_CONDITION,
        "title": "Soil condition",
        "hint": "Also on a soil test report, or from a pH meter or test kit.",
    },
    {
        "key": GROUP_CLIMATE,
        "title": "Climate",
        "hint": "Typical conditions for the season you plan to grow in.",
    },
)


@bp.get("/csrf-token")
def csrf_token():
    """Issue a CSRF token bound to the caller's session cookie.

    It must be sent back in the ``X-CSRFToken`` header on every POST and
    DELETE request. Logging in or out starts a new session, so clients fetch
    a fresh token afterwards.
    """
    return success_response({"csrf_token": generate_csrf()}, "CSRF token issued")


@bp.get("/config")
def config():
    """Everything the front end needs to render its forms.

    The input limits here are the same objects the backend validates against,
    so browser-side and server-side validation cannot drift apart.
    """
    services = current_app.extensions["services"]
    return success_response(
        {
            "version": __version__,
            "features": current_app.extensions["feature_specs"],
            "field_groups": list(FIELD_GROUPS),
            "weather_enabled": services.weather.is_configured,
            "model_available": services.prediction.is_available,
            "password_reset_enabled": services.auth.password_reset_enabled,
            "supported_crops": [{"label": crop.label, "name": crop.name} for crop in services.crops.all()],
        },
        "Configuration retrieved",
    )
