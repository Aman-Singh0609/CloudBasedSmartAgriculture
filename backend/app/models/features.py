"""The seven model inputs and every limit that applies to them.

This module is the single source of truth for input boundaries. The backend
validators, the HTML form and the browser-side validation all read from
``FEATURES``, so a limit is never repeated as a magic number elsewhere.

Two ranges are kept per input:

``hard_min`` / ``hard_max``
    Values outside this range are physically implausible or absurdly large
    and are rejected with a validation error.

``model_min`` / ``model_max``
    The span of values present in the public "Crop Recommendation" dataset
    whose 22 labels the bundled model predicts. A random forest cannot
    extrapolate: beyond this span it behaves as if the value sat on the edge.
    Such inputs are accepted but flagged, and the result is shown with a
    reduced-reliability warning. (The model's own split thresholds all fall
    inside these spans, which is checked by the test suite.)
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

GROUP_NUTRIENTS = "nutrients"
GROUP_CONDITION = "condition"
GROUP_CLIMATE = "climate"


@dataclass(frozen=True)
class FeatureSpec:
    key: str  # canonical name used in the API, database and forms
    model_name: str  # column name the model was trained with
    label: str
    short_label: str
    unit: str
    group: str
    hard_min: float
    hard_max: float
    model_min: float
    model_max: float
    step: float
    example: float
    help: str

    def to_dict(self) -> dict:
        return asdict(self)


FEATURES: tuple[FeatureSpec, ...] = (
    FeatureSpec(
        key="nitrogen",
        model_name="N",
        label="Nitrogen (N)",
        short_label="N",
        unit="",
        group=GROUP_NUTRIENTS,
        hard_min=0,
        hard_max=300,
        model_min=0,
        model_max=140,
        step=1,
        example=90,
        help="Nitrogen content of your soil, from a soil test report. Drives leafy growth.",
    ),
    FeatureSpec(
        key="phosphorus",
        model_name="P",
        label="Phosphorus (P)",
        short_label="P",
        unit="",
        group=GROUP_NUTRIENTS,
        hard_min=0,
        hard_max=300,
        model_min=5,
        model_max=145,
        step=1,
        example=42,
        help="Phosphorus content of your soil, from a soil test report. Supports roots and flowering.",
    ),
    FeatureSpec(
        key="potassium",
        model_name="K",
        label="Potassium (K)",
        short_label="K",
        unit="",
        group=GROUP_NUTRIENTS,
        hard_min=0,
        hard_max=400,
        model_min=5,
        model_max=205,
        step=1,
        example=43,
        help="Potassium content of your soil, from a soil test report. Supports plant strength and fruit quality.",
    ),
    FeatureSpec(
        key="ph",
        model_name="ph",
        label="Soil pH",
        short_label="pH",
        unit="",
        group=GROUP_CONDITION,
        hard_min=0,
        hard_max=14,
        model_min=3.5,
        model_max=9.9,
        step=0.1,
        example=6.5,
        help="How acidic or alkaline your soil is. 7 is neutral; below 7 is acidic, above 7 is alkaline.",
    ),
    FeatureSpec(
        key="temperature",
        model_name="temperature",
        label="Temperature",
        short_label="Temp",
        unit="°C",
        group=GROUP_CLIMATE,
        hard_min=-10,
        hard_max=60,
        model_min=8.8,
        model_max=43.7,
        step=0.1,
        example=20.8,
        help="Typical air temperature during the growing period.",
    ),
    FeatureSpec(
        key="humidity",
        model_name="humidity",
        label="Humidity",
        short_label="Humidity",
        unit="%",
        group=GROUP_CLIMATE,
        hard_min=0,
        hard_max=100,
        model_min=14.3,
        model_max=100,
        step=0.1,
        example=82,
        help="Typical relative humidity of the air during the growing period.",
    ),
    FeatureSpec(
        key="rainfall",
        model_name="rainfall",
        label="Rainfall",
        short_label="Rain",
        unit="mm",
        group=GROUP_CLIMATE,
        hard_min=0,
        hard_max=3000,
        model_min=20.2,
        model_max=298.6,
        step=0.1,
        example=202.9,
        help="Typical rainfall for the growing period. This is not a last-hour or single-day reading.",
    ),
)

FEATURE_KEYS: tuple[str, ...] = tuple(spec.key for spec in FEATURES)
FEATURES_BY_KEY: dict[str, FeatureSpec] = {spec.key: spec for spec in FEATURES}
MODEL_NAME_TO_KEY: dict[str, str] = {spec.model_name: spec.key for spec in FEATURES}

SOURCE_MANUAL = "manual"
SOURCE_WEATHER_API = "weather_api"


def format_number(value: float) -> str:
    """Render a number without a trailing ``.0`` (90.0 -> '90', 6.5 -> '6.5')."""
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.2f}".rstrip("0").rstrip(".")
