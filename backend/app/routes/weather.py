"""Weather lookup proxy. The provider API key never leaves the server."""

from __future__ import annotations

from datetime import datetime, timezone

from flask import Blueprint, current_app, request, session

from app.errors import AppError
from app.extensions import limiter
from app.models.features import SOURCE_WEATHER_API
from app.models.schemas import isoformat_utc
from app.utils.responses import success_response
from app.utils.security import SESSION_WEATHER, login_required, rate_limit_user_key
from app.utils.validators import validate_city

bp = Blueprint("weather", __name__)

RAINFALL_NOTE = (
    "Rainfall is not filled in automatically. The weather service reports rain for the last hour "
    "only, while the model expects typical rainfall for the whole growing period. Please enter it yourself."
)


@bp.get("/api/weather")
@login_required
@limiter.limit("20 per minute", key_func=rate_limit_user_key)
def current_weather():
    """Return current temperature and humidity for a location.

    Query: ``city`` (required). Requires login so that anonymous traffic
    cannot spend the server's weather-provider quota.
    """
    city = validate_city(request.args.get("city"))
    services = current_app.extensions["services"]

    try:
        reading = services.weather.get_current(city)
    except AppError as exc:
        if exc.status_code >= 500:
            services.audit.record(
                "weather.provider_failure", outcome="failure", details={"error_code": exc.code}
            )
        raise

    observed_at = isoformat_utc(reading.observed_at)
    # Remembered in the signed session so the server itself can later confirm
    # which submitted readings came from this lookup (see RecommendationService).
    session[SESSION_WEATHER] = {
        "location": reading.location,
        "provider": reading.provider,
        "temperature": reading.temperature,
        "humidity": reading.humidity,
        "observed_at": observed_at,
        "fetched_at": isoformat_utc(datetime.now(timezone.utc)),
    }

    return success_response(
        {
            "location": reading.location,
            "temperature": reading.temperature,
            "humidity": reading.humidity,
            "conditions": reading.conditions,
            "observed_at": observed_at,
            "provider": reading.provider,
            "source": SOURCE_WEATHER_API,
            "rainfall": None,
            "observed_rain_last_hour_mm": reading.rain_last_hour_mm,
            "rainfall_note": RAINFALL_NOTE,
        },
        "Weather fetched successfully",
    )
