"""Typed value objects passed between layers, and their API representations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from app.models.features import FEATURE_KEYS, SOURCE_MANUAL


@dataclass(frozen=True)
class CropProbability:
    crop: str
    probability: float

    def to_dict(self) -> dict:
        return {"crop": self.crop, "probability": self.probability}


@dataclass(frozen=True)
class ModelPrediction:
    """What the ML model said for one set of inputs."""

    recommended_crop: str
    confidence: float
    alternatives: tuple[CropProbability, ...]


@dataclass(frozen=True)
class WeatherReading:
    """Current conditions reported by the weather provider for a location."""

    location: str
    temperature: float
    humidity: float
    rain_last_hour_mm: float | None
    conditions: str | None
    observed_at: datetime
    provider: str = "openweathermap"


@dataclass(frozen=True)
class CurrentUser:
    """The authenticated user for the current request (never includes the hash)."""

    id: object
    name: str
    email: str


def isoformat_utc(value: datetime | None) -> str | None:
    """ISO 8601 timestamp with an explicit UTC designator."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def serialize_prediction_summary(document: dict, crop_names: dict[str, str]) -> dict:
    """Compact representation used in history listings."""
    crop = document["recommended_crop"]
    return {
        "id": str(document["_id"]),
        "recommended_crop": crop,
        "crop_name": crop_names.get(crop, crop),
        "confidence": document.get("confidence"),
        "inputs": {key: document["inputs"].get(key) for key in FEATURE_KEYS},
        "reliability": "reduced" if document.get("out_of_range_fields") else "normal",
        "created_at": isoformat_utc(document["created_at"]),
    }


def serialize_prediction(document: dict, crop_names: dict[str, str]) -> dict:
    """Full representation of one saved analysis (ML output and provenance)."""
    summary = serialize_prediction_summary(document, crop_names)
    weather = document.get("weather")
    summary.update(
        {
            "alternatives": [
                {
                    "crop": alt["crop"],
                    "crop_name": crop_names.get(alt["crop"], alt["crop"]),
                    "probability": alt["probability"],
                }
                for alt in document.get("alternatives", [])
            ],
            "input_sources": {
                key: document.get("input_sources", {}).get(key, SOURCE_MANUAL) for key in FEATURE_KEYS
            },
            "weather": (
                {
                    "location": weather.get("location"),
                    "provider": weather.get("provider"),
                    "observed_at": isoformat_utc(weather.get("observed_at")),
                }
                if weather
                else None
            ),
            "out_of_range_fields": list(document.get("out_of_range_fields", [])),
            "model": document.get("model"),
        }
    )
    return summary
