"""Server-side client for the OpenWeatherMap "current weather" API.

The browser never talks to the provider and never sees the API key::

    browser -> AgricultureCloud backend -> weather provider

Every failure mode is mapped to a specific, user-safe error so the crop
advisor can fall back to manual entry. Because ``requests`` exceptions include
the full request URL (and therefore the API key) in their text, exception
messages are never logged or returned; only the exception class name is.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

import requests

from app.errors import ExternalServiceError, NotFoundError, ServiceUnavailableError
from app.models.schemas import WeatherReading

logger = logging.getLogger(__name__)

PROVIDER_NAME = "openweathermap"
_MAX_CACHE_ENTRIES = 256
_MAX_RESPONSE_BYTES = 64 * 1024

# Plausibility bounds for values coming back from the provider.
_TEMPERATURE_BOUNDS = (-90.0, 60.0)
_HUMIDITY_BOUNDS = (0.0, 100.0)


class WeatherNotConfiguredError(ServiceUnavailableError):
    code = "WEATHER_NOT_CONFIGURED"
    default_message = (
        "Weather lookup is not set up on this server. Please enter temperature and humidity manually."
    )


class WeatherUnavailableError(ServiceUnavailableError):
    code = "WEATHER_UNAVAILABLE"
    default_message = (
        "The weather service is not available right now. Please enter temperature and humidity manually."
    )


class WeatherTimeoutError(ExternalServiceError):
    status_code = 504
    code = "WEATHER_TIMEOUT"
    default_message = (
        "The weather service took too long to respond. Please try again or enter the values manually."
    )


class WeatherProviderError(ExternalServiceError):
    status_code = 502
    code = "WEATHER_PROVIDER_ERROR"
    default_message = (
        "The weather service returned an unexpected response. Please enter temperature and humidity manually."
    )


class CityNotFoundError(NotFoundError):
    code = "WEATHER_CITY_NOT_FOUND"
    default_message = "We could not find that location. Check the spelling or try a nearby larger town."


def _number_in(value: object, bounds: tuple[float, float]) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number != number or not (bounds[0] <= number <= bounds[1]):  # NaN or out of bounds
        return None
    return number


class WeatherService:
    def __init__(
        self,
        api_key: str | None,
        base_url: str,
        *,
        connect_timeout: float = 3.05,
        read_timeout: float = 6.0,
        cache_ttl_seconds: int = 600,
        session: requests.Session | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url
        self._timeout = (connect_timeout, read_timeout)
        self._cache_ttl = cache_ttl_seconds
        self._session = session or requests.Session()
        self._cache: dict[str, tuple[float, WeatherReading]] = {}
        self._cache_lock = threading.Lock()

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    def get_current(self, city: str) -> WeatherReading:
        """Return current conditions for ``city`` (already validated by the caller)."""
        if not self._api_key:
            raise WeatherNotConfiguredError()

        cache_key = city.casefold()
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        payload = self._request(city)
        reading = self._parse(payload, fallback_location=city)
        self._cache_put(cache_key, reading)
        return reading

    # ----------------------------------------------------------------- HTTP
    def _request(self, city: str) -> dict:
        try:
            # ``params`` is URL-encoded by requests, so the city value cannot
            # inject additional query parameters.
            response = self._session.get(
                self._base_url,
                params={"q": city, "appid": self._api_key, "units": "metric"},
                timeout=self._timeout,
                allow_redirects=False,
                headers={"Accept": "application/json"},
            )
        except requests.Timeout as exc:
            self._log_failure("timeout", exc)
            raise WeatherTimeoutError() from None
        except requests.RequestException as exc:
            self._log_failure("network_error", exc)
            raise WeatherUnavailableError() from None

        status = response.status_code
        if status == 404:
            raise CityNotFoundError()
        if status in (401, 403):
            # Our key is wrong, expired or not yet activated: a server
            # configuration problem, not something the user can fix.
            self._log_failure("auth_rejected", status=status)
            raise WeatherUnavailableError()
        if status == 429:
            self._log_failure("provider_rate_limited", status=status)
            raise WeatherUnavailableError(
                "The weather service is busy right now. Please try again in a minute or enter the values manually."
            )
        if status != 200:
            self._log_failure("provider_error", status=status)
            raise WeatherProviderError()

        if len(response.content) > _MAX_RESPONSE_BYTES:
            self._log_failure("oversized_response", status=status)
            raise WeatherProviderError()
        try:
            payload = response.json()
        except ValueError:
            self._log_failure("invalid_json", status=status)
            raise WeatherProviderError() from None
        if not isinstance(payload, dict):
            self._log_failure("unexpected_shape", status=status)
            raise WeatherProviderError()
        return payload

    # ---------------------------------------------------------------- parse
    def _parse(self, payload: dict, *, fallback_location: str) -> WeatherReading:
        main = payload.get("main")
        if not isinstance(main, dict):
            self._log_failure("missing_main_block")
            raise WeatherProviderError()

        temperature = _number_in(main.get("temp"), _TEMPERATURE_BOUNDS)
        humidity = _number_in(main.get("humidity"), _HUMIDITY_BOUNDS)
        if temperature is None or humidity is None:
            self._log_failure("missing_or_implausible_values")
            raise WeatherProviderError()

        # Rain is optional: the provider omits the block when it is not raining.
        rain_block = payload.get("rain")
        rain_last_hour = None
        if isinstance(rain_block, dict):
            rain_last_hour = _number_in(rain_block.get("1h"), (0.0, 500.0))

        name = payload.get("name")
        sys_block = payload.get("sys")
        country = sys_block.get("country") if isinstance(sys_block, dict) else None
        location = name.strip() if isinstance(name, str) and name.strip() else fallback_location
        if isinstance(country, str) and 0 < len(country) <= 3 and country.isalpha():
            location = f"{location}, {country.upper()}"

        conditions = None
        weather_list = payload.get("weather")
        if isinstance(weather_list, list) and weather_list and isinstance(weather_list[0], dict):
            description = weather_list[0].get("description")
            if isinstance(description, str):
                conditions = description.strip()[:60] or None

        observed = payload.get("dt")
        if isinstance(observed, (int, float)) and not isinstance(observed, bool) and 0 < observed < 4102444800:
            observed_at = datetime.fromtimestamp(observed, tz=timezone.utc)
        else:
            observed_at = datetime.now(timezone.utc)

        return WeatherReading(
            location=location[:100],
            temperature=round(temperature, 1),
            humidity=round(humidity, 1),
            rain_last_hour_mm=rain_last_hour,
            conditions=conditions,
            observed_at=observed_at.replace(microsecond=0),
        )

    # ---------------------------------------------------------------- cache
    def _cache_get(self, key: str) -> WeatherReading | None:
        with self._cache_lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            stored_at, reading = entry
            if time.monotonic() - stored_at > self._cache_ttl:
                del self._cache[key]
                return None
            return reading

    def _cache_put(self, key: str, reading: WeatherReading) -> None:
        with self._cache_lock:
            if len(self._cache) >= _MAX_CACHE_ENTRIES:
                oldest = min(self._cache, key=lambda k: self._cache[k][0])
                del self._cache[oldest]
            self._cache[key] = (time.monotonic(), reading)

    @staticmethod
    def _log_failure(reason: str, exc: Exception | None = None, *, status: int | None = None) -> None:
        # Never log str(exc): it contains the request URL including the API key.
        logger.warning(
            "weather provider failure: %s",
            reason,
            extra={
                "event": "weather_provider_failure",
                "reason": reason,
                "provider_status": status,
                "error_type": type(exc).__name__ if exc is not None else None,
            },
        )
