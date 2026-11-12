"""Independent agronomic check against FAO EcoCrop temperature and pH limits.

The machine-learning model learns patterns from a curated table; this check
uses published crop limits instead, so the two can disagree, and when they do
the user is told. Only temperature and pH are checked: EcoCrop's rainfall is an
annual total and the model's rainfall input is on a different scale.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "ecocrop_limits.json"

_CHECKS = (
    ("temperature", "temperature_absolute", "°C", "Temperature"),
    ("ph", "ph_absolute", "", "Soil pH"),
)


@lru_cache(maxsize=1)
def _limits() -> dict:
    with DATA_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)["crops"]


def check(crop_key: str, inputs: dict) -> list[dict]:
    """Return the readings that fall outside the crop's absolute limits (empty if none)."""
    limits = _limits().get(crop_key)
    if limits is None:
        return []
    violations = []
    for field, limit_key, unit, label in _CHECKS:
        value = inputs.get(field)
        low, high = limits[limit_key]
        if isinstance(value, (int, float)) and not low <= value <= high:
            violations.append(
                {
                    "field": field,
                    "label": label,
                    "value": float(value),
                    "limit_low": low,
                    "limit_high": high,
                    "unit": unit,
                    "direction": "below" if value < low else "above",
                }
            )
    return violations
