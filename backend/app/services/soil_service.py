"""Rule-based interpretation of the readings a user entered.

IMPORTANT: nothing in this module is a machine-learning output. It compares
each reading with the typical range for the recommended crop (reference data
in ``crop_details.json``) and classifies soil pH on a conventional scale. The
UI presents these results under a "rule-based" heading, separately from the
model's recommendation.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.features import (
    FEATURES,
    GROUP_CLIMATE,
    FeatureSpec,
    format_number,
)
from app.services.crop_service import CropInfo

STATUS_BELOW = "below"
STATUS_WITHIN = "within"
STATUS_ABOVE = "above"

_STATUS_LABELS = {
    STATUS_BELOW: "Below typical range",
    STATUS_WITHIN: "Within typical range",
    STATUS_ABOVE: "Above typical range",
}

# Conventional descriptive pH classes: (upper bound exclusive, label, description).
_PH_CLASSES = (
    (4.5, "Extremely acidic", "Very few crops tolerate soil this acidic; nutrients such as phosphorus become unavailable."),
    (5.5, "Strongly acidic", "Many crops struggle; liming is commonly used to raise pH."),
    (6.5, "Slightly acidic", "Suitable for most crops; many nutrients are readily available."),
    (7.5, "Neutral", "Suitable for most crops."),
    (8.5, "Slightly alkaline", "Some nutrients, such as iron and zinc, become less available."),
    (14.01, "Strongly alkaline", "Many crops struggle; soil amendments are usually needed."),
)

# Confidence bands for presenting the model's probability. These describe how
# strongly the model's trees agree, not the likelihood of a successful harvest.
CONFIDENCE_HIGH = 0.80
CONFIDENCE_MODERATE = 0.50

_TIPS = {
    ("nitrogen", STATUS_BELOW): (
        "Nitrogen is below the typical range for {crop}. It is commonly built up with well-rotted "
        "manure or compost, green manuring, legumes in the rotation, or nitrogen fertiliser."
    ),
    ("nitrogen", STATUS_ABOVE): (
        "Nitrogen is above the typical range for {crop}. Excess nitrogen can push leafy growth at "
        "the expense of grain or fruit, so avoid adding more until the soil is retested."
    ),
    ("phosphorus", STATUS_BELOW): (
        "Phosphorus is below the typical range for {crop}. Phosphate fertiliser, rock phosphate, "
        "bone meal or compost are commonly used to raise it."
    ),
    ("phosphorus", STATUS_ABOVE): (
        "Phosphorus is above the typical range for {crop}. Further phosphate is unlikely to help "
        "and very high levels can limit the uptake of zinc and iron."
    ),
    ("potassium", STATUS_BELOW): (
        "Potassium is below the typical range for {crop}. Potash fertiliser, wood ash or compost "
        "are commonly used to raise it."
    ),
    ("potassium", STATUS_ABOVE): (
        "Potassium is above the typical range for {crop}. Additional potash is unlikely to help; "
        "very high levels can interfere with magnesium and calcium uptake."
    ),
    ("ph", STATUS_BELOW): (
        "The soil is more acidic than is typical for {crop}. Agricultural lime is commonly used "
        "to raise soil pH."
    ),
    ("ph", STATUS_ABOVE): (
        "The soil is more alkaline than is typical for {crop}. Gypsum, elemental sulphur or added "
        "organic matter are commonly used to lower soil pH."
    ),
    ("temperature", STATUS_BELOW): (
        "Temperature is cooler than is typical for {crop}. Sowing date and variety choice are the "
        "usual ways to manage this."
    ),
    ("temperature", STATUS_ABOVE): (
        "Temperature is warmer than is typical for {crop}. Sowing date, mulching, shade or a "
        "heat-tolerant variety are the usual ways to manage this."
    ),
    ("humidity", STATUS_BELOW): (
        "Humidity is lower than is typical for {crop}. The crop may need more frequent irrigation."
    ),
    ("humidity", STATUS_ABOVE): (
        "Humidity is higher than is typical for {crop}. Watch for fungal disease and allow good "
        "air movement between plants."
    ),
    ("rainfall", STATUS_BELOW): (
        "Rainfall is lower than is typical for {crop}. The crop would depend on irrigation to make "
        "up the difference."
    ),
    ("rainfall", STATUS_ABOVE): (
        "Rainfall is higher than is typical for {crop}. Good field drainage becomes important."
    ),
}

_AMOUNT_ADVICE = (
    "Amounts depend on your field. Confirm them with a laboratory soil test or your local "
    "agricultural extension office before applying anything."
)


@dataclass(frozen=True)
class ReadingAssessment:
    key: str
    label: str
    short_label: str
    unit: str
    group: str
    value: float
    value_text: str
    typical_min: float
    typical_max: float
    typical_text: str
    status: str
    status_label: str
    # Positions (0-100) for drawing the value against the typical band.
    band_start_pct: float
    band_width_pct: float
    marker_pct: float
    scale_min_text: str
    scale_max_text: str

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "unit": self.unit,
            "group": self.group,
            "value": self.value,
            "value_text": self.value_text,
            "typical_range": [self.typical_min, self.typical_max],
            "typical_text": self.typical_text,
            "status": self.status,
            "status_label": self.status_label,
            # Positions (0-100) for drawing the value against the typical band.
            "chart": {
                "band_start": self.band_start_pct,
                "band_width": self.band_width_pct,
                "marker": self.marker_pct,
                "scale_min": self.scale_min_text,
                "scale_max": self.scale_max_text,
            },
        }


@dataclass(frozen=True)
class SoilAssessment:
    crop_label: str
    crop_name: str
    readings: tuple[ReadingAssessment, ...]
    ph_class: str
    ph_description: str
    within_count: int
    total_count: int
    tips: tuple[str, ...]
    amount_advice: str

    @property
    def soil_readings(self) -> tuple[ReadingAssessment, ...]:
        return tuple(r for r in self.readings if r.group != GROUP_CLIMATE)

    @property
    def climate_readings(self) -> tuple[ReadingAssessment, ...]:
        return tuple(r for r in self.readings if r.group == GROUP_CLIMATE)

    def to_dict(self) -> dict:
        return {
            "method": "rule_based",
            "compared_with": self.crop_label,
            "readings": [reading.to_dict() for reading in self.readings],
            "ph_class": self.ph_class,
            "ph_description": self.ph_description,
            "within_typical_range": self.within_count,
            "total_readings": self.total_count,
            "suggestions": list(self.tips),
            "amount_advice": self.amount_advice,
        }


def classify_ph(value: float) -> tuple[str, str]:
    """Return the descriptive class and a one-line explanation for a soil pH."""
    for upper, label, description in _PH_CLASSES:
        if value < upper:
            return label, description
    return _PH_CLASSES[-1][1], _PH_CLASSES[-1][2]


def confidence_band(confidence: float | None) -> tuple[str, str]:
    """Return ``(band key, label)`` describing how strongly the model agrees."""
    if confidence is None:
        return "unknown", "Not recorded"
    if confidence >= CONFIDENCE_HIGH:
        return "high", "Strong model agreement"
    if confidence >= CONFIDENCE_MODERATE:
        return "moderate", "Moderate model agreement"
    return "low", "Weak model agreement"


def _status_for(value: float, low: float, high: float) -> str:
    if value < low:
        return STATUS_BELOW
    if value > high:
        return STATUS_ABOVE
    return STATUS_WITHIN


def _range_text(low: float, high: float, unit: str) -> str:
    suffix = f" {unit}" if unit else ""
    return f"{format_number(low)}–{format_number(high)}{suffix}"


def _assess_reading(spec: FeatureSpec, value: float, low: float, high: float) -> ReadingAssessment:
    # The chart scale is the model's reference range, widened if the value or
    # the crop's typical band happens to fall outside it.
    scale_min = min(spec.model_min, value, low)
    scale_max = max(spec.model_max, value, high)
    span = (scale_max - scale_min) or 1.0
    status = _status_for(value, low, high)
    unit_suffix = f" {spec.unit}" if spec.unit else ""
    return ReadingAssessment(
        key=spec.key,
        label=spec.label,
        short_label=spec.short_label,
        unit=spec.unit,
        group=spec.group,
        value=value,
        value_text=f"{format_number(value)}{unit_suffix}",
        typical_min=low,
        typical_max=high,
        typical_text=_range_text(low, high, spec.unit),
        status=status,
        status_label=_STATUS_LABELS[status],
        band_start_pct=round((low - scale_min) / span * 100, 2),
        band_width_pct=round(max((high - low) / span * 100, 1.0), 2),
        marker_pct=round((value - scale_min) / span * 100, 2),
        scale_min_text=format_number(scale_min),
        scale_max_text=format_number(scale_max),
    )


class SoilService:
    """Compares a set of readings with the typical conditions for one crop."""

    def assess(self, inputs: dict[str, float], crop: CropInfo) -> SoilAssessment:
        readings = tuple(
            _assess_reading(spec, inputs[spec.key], *crop.typical_ranges[spec.key]) for spec in FEATURES
        )
        tips = tuple(
            _TIPS[(reading.key, reading.status)].format(crop=crop.name.lower())
            for reading in readings
            if reading.status != STATUS_WITHIN
        )
        ph_class, ph_description = classify_ph(inputs["ph"])
        return SoilAssessment(
            crop_label=crop.label,
            crop_name=crop.name,
            readings=readings,
            ph_class=ph_class,
            ph_description=ph_description,
            within_count=sum(1 for reading in readings if reading.status == STATUS_WITHIN),
            total_count=len(readings),
            tips=tips,
            amount_advice=_AMOUNT_ADVICE,
        )
