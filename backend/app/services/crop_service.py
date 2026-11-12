"""Crop reference information, loaded once from ``app/data/crop_details.json``.

The data file is validated when it is loaded and again against the model's
output classes at startup, so a crop the model can recommend can never be
missing its details (and stale entries such as "wheat" cannot linger).
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from app.models.features import FEATURE_KEYS

DEFAULT_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "crop_details.json"

_REQUIRED_TEXT_FIELDS = ("name", "scientific_name", "category", "summary", "description", "season")
_GROWING_CONDITION_FIELDS = ("climate", "soil", "water")


class CropDataError(RuntimeError):
    """The crop data file is malformed or out of step with the model."""


@dataclass(frozen=True)
class CropInfo:
    label: str
    name: str
    also_known_as: str
    scientific_name: str
    category: str
    perennial: bool
    summary: str
    description: str
    growing_conditions: dict[str, str]
    season: str
    cautions: tuple[str, ...]
    typical_ranges: dict[str, tuple[float, float]]

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "name": self.name,
            "also_known_as": self.also_known_as,
            "scientific_name": self.scientific_name,
            "category": self.category,
            "perennial": self.perennial,
            "summary": self.summary,
            "description": self.description,
            "growing_conditions": dict(self.growing_conditions),
            "season": self.season,
            "cautions": list(self.cautions),
            "typical_ranges": {key: list(bounds) for key, bounds in self.typical_ranges.items()},
        }


def _parse_crop(label: str, raw: dict) -> CropInfo:
    if not isinstance(raw, dict):
        raise CropDataError(f"Crop '{label}' must be an object.")
    for field in _REQUIRED_TEXT_FIELDS:
        if not isinstance(raw.get(field), str) or not raw[field].strip():
            raise CropDataError(f"Crop '{label}' is missing the text field '{field}'.")

    conditions = raw.get("growing_conditions")
    if not isinstance(conditions, dict) or any(
        not isinstance(conditions.get(field), str) or not conditions[field].strip()
        for field in _GROWING_CONDITION_FIELDS
    ):
        raise CropDataError(f"Crop '{label}' needs growing_conditions with climate, soil and water.")

    ranges_raw = raw.get("typical_ranges")
    if not isinstance(ranges_raw, dict) or set(ranges_raw) != set(FEATURE_KEYS):
        raise CropDataError(f"Crop '{label}' must define typical_ranges for exactly: {', '.join(FEATURE_KEYS)}.")
    ranges: dict[str, tuple[float, float]] = {}
    for key, bounds in ranges_raw.items():
        if (
            not isinstance(bounds, list)
            or len(bounds) != 2
            or any(isinstance(b, bool) or not isinstance(b, (int, float)) for b in bounds)
            or bounds[0] > bounds[1]
        ):
            raise CropDataError(f"Crop '{label}' has an invalid typical range for '{key}'.")
        ranges[key] = (float(bounds[0]), float(bounds[1]))

    cautions = raw.get("cautions", [])
    if not isinstance(cautions, list) or any(not isinstance(c, str) for c in cautions):
        raise CropDataError(f"Crop '{label}' has invalid cautions.")

    return CropInfo(
        label=label,
        name=raw["name"],
        also_known_as=raw.get("also_known_as", ""),
        scientific_name=raw["scientific_name"],
        category=raw["category"],
        perennial=bool(raw.get("perennial", False)),
        summary=raw["summary"],
        description=raw["description"],
        growing_conditions={field: conditions[field] for field in _GROWING_CONDITION_FIELDS},
        season=raw["season"],
        cautions=tuple(cautions),
        typical_ranges=ranges,
    )


class CropService:
    def __init__(self, data_path: Path | str = DEFAULT_DATA_PATH) -> None:
        try:
            with open(data_path, encoding="utf-8") as handle:
                document = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise CropDataError(f"Could not read crop data file: {exc}") from exc

        crops = document.get("crops") if isinstance(document, dict) else None
        if not isinstance(crops, dict) or not crops:
            raise CropDataError("Crop data file must contain a non-empty 'crops' object.")
        self._crops: dict[str, CropInfo] = {label: _parse_crop(label, raw) for label, raw in crops.items()}

    @property
    def labels(self) -> tuple[str, ...]:
        return tuple(self._crops)

    @property
    def names(self) -> dict[str, str]:
        """Mapping of model label -> display name."""
        return {label: crop.name for label, crop in self._crops.items()}

    def get(self, label: str) -> CropInfo:
        try:
            return self._crops[label]
        except KeyError as exc:
            raise CropDataError(f"No crop details exist for model label '{label}'.") from exc

    def all(self) -> list[CropInfo]:
        return sorted(self._crops.values(), key=lambda crop: crop.name)

    def validate_against_model(self, model_labels: Iterable[str]) -> None:
        """Fail loudly if the data file and the model disagree about the crop list."""
        model_set = set(model_labels)
        data_set = set(self._crops)
        missing = sorted(model_set - data_set)
        extra = sorted(data_set - model_set)
        if missing or extra:
            problems = []
            if missing:
                problems.append(f"missing details for: {', '.join(missing)}")
            if extra:
                problems.append(f"details for crops the model cannot output: {', '.join(extra)}")
            raise CropDataError("Crop data does not match the model (" + "; ".join(problems) + ").")
