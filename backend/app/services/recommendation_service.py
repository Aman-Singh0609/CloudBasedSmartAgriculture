"""Orchestrates one crop analysis: model prediction, provenance, persistence
and the view model shown to the user.

Routes call this service; it in turn uses the ML prediction service, the crop
reference data, the rule-based soil interpretation and the prediction
repository. It keeps HTTP concerns out of the domain logic and gives future
modules (fertiliser advice, yield estimates, ...) one place to plug into.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from bson import ObjectId

from app.errors import NotFoundError
from app.models.features import (
    FEATURE_KEYS,
    FEATURES_BY_KEY,
    SOURCE_MANUAL,
    SOURCE_WEATHER_API,
    FeatureSpec,
    format_number,
)
from app.models.schemas import serialize_prediction, serialize_prediction_summary
from app.repositories.prediction_repository import PredictionRepository
from app.services.audit_service import AuditService
from app.services.crop_service import CropService
from app.services.prediction_service import PredictionService
from app.services import agronomy_service
from app.services.soil_service import CONFIDENCE_MODERATE, SoilService, confidence_band

# Fields the weather lookup can supply, and how close a submitted value must
# be to the fetched one to count as "from the weather service".
WEATHER_SUPPLIED_FIELDS = ("temperature", "humidity")
_WEATHER_MATCH_TOLERANCE = 0.05
_WEATHER_CONTEXT_MAX_AGE = timedelta(hours=6)

TREND_POINTS = 30
TREND_FEATURES = ("nitrogen", "phosphorus", "potassium", "ph")

LIMITATIONS = (
    "The recommendation uses only the seven readings you entered and patterns in the model's training data.",
    "It does not know about market prices, seed or labour availability, irrigation capacity, pests and "
    "diseases, soil texture, crop rotation, local regulations or future weather.",
    "Model confidence describes how strongly the model's decision trees agree. It is not the chance "
    "that the crop will succeed on your farm.",
    "Treat this as one input to your decision and confirm it with a local agronomist or extension officer.",
)


def _trend_card(spec: FeatureSpec, values: list[float]) -> dict:
    """Describe one reading's history as a sparkline in a 100 x 30 viewBox."""
    low, high = min(values), max(values)
    span = high - low
    last_index = len(values) - 1
    points = []
    for index, value in enumerate(values):
        x = index / last_index * 100
        # 2..28 leaves room for the stroke; a flat series sits on the middle line.
        y = 15.0 if span == 0 else 28 - (value - low) / span * 26
        points.append(f"{x:.2f},{y:.2f}")

    first, latest = values[0], values[-1]
    change = "same" if latest == first else ("up" if latest > first else "down")
    return {
        "key": spec.key,
        "label": spec.label,
        "count": len(values),
        "latest_text": format_number(latest),
        "first_text": format_number(first),
        "min_text": format_number(low),
        "max_text": format_number(high),
        "change": change,
        "points": " ".join(points),
        "values_text": ", ".join(format_number(value) for value in values),
    }


def _parse_iso(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class RecommendationService:
    def __init__(
        self,
        *,
        prediction: PredictionService,
        crops: CropService,
        soil: SoilService,
        predictions: PredictionRepository,
        audit: AuditService,
    ) -> None:
        self._prediction = prediction
        self._crops = crops
        self._soil = soil
        self._predictions = predictions
        self._audit = audit

    # --------------------------------------------------------------- create
    def create(
        self,
        *,
        user_id: ObjectId,
        inputs: dict[str, float],
        out_of_range_fields: list[str],
        weather_context: dict | None,
    ) -> dict:
        """Run the model for validated ``inputs`` and save the analysis."""
        result = self._prediction.predict(inputs)
        input_sources, weather = self._resolve_sources(inputs, weather_context)

        document = self._predictions.create(
            user_id=user_id,
            inputs={key: inputs[key] for key in FEATURE_KEYS},
            input_sources=input_sources,
            weather=weather,
            recommended_crop=result.recommended_crop,
            confidence=result.confidence,
            alternatives=[alternative.to_dict() for alternative in result.alternatives],
            out_of_range_fields=list(out_of_range_fields),
            model=self._prediction.info,
        )
        self._audit.record(
            "prediction.created",
            user_id=user_id,
            details={
                "prediction_id": str(document["_id"]),
                "recommended_crop": result.recommended_crop,
                "confidence": result.confidence,
                "out_of_range_fields": list(out_of_range_fields),
            },
        )
        return document

    @staticmethod
    def _resolve_sources(inputs: dict[str, float], weather_context: dict | None) -> tuple[dict[str, str], dict | None]:
        """Decide, on the server, which readings came from the weather lookup.

        The browser's claim is not trusted. A reading is attributed to the
        weather service only when it equals the value this server fetched for
        the user recently (kept in their signed session).
        """
        sources = {key: SOURCE_MANUAL for key in FEATURE_KEYS}
        if not isinstance(weather_context, dict):
            return sources, None

        observed_at = _parse_iso(weather_context.get("observed_at"))
        fetched_at = _parse_iso(weather_context.get("fetched_at"))
        if fetched_at is None or datetime.now(timezone.utc) - fetched_at > _WEATHER_CONTEXT_MAX_AGE:
            return sources, None

        matched = False
        for key in WEATHER_SUPPLIED_FIELDS:
            fetched = weather_context.get(key)
            if isinstance(fetched, (int, float)) and abs(inputs[key] - float(fetched)) <= _WEATHER_MATCH_TOLERANCE:
                sources[key] = SOURCE_WEATHER_API
                matched = True
        if not matched:
            return sources, None
        return sources, {
            "location": str(weather_context.get("location", ""))[:100],
            "provider": str(weather_context.get("provider", ""))[:40],
            "observed_at": observed_at,
        }

    # ----------------------------------------------------------------- read
    def get_for_user(self, prediction_id: ObjectId, user_id: ObjectId) -> dict:
        """Fetch one analysis. A record owned by someone else is reported as
        not found, so its existence is not revealed."""
        document = self._predictions.find_for_user(prediction_id, user_id)
        if document is None:
            raise NotFoundError("That analysis could not be found.")
        return document

    def list_for_user(self, user_id: ObjectId, *, page: int, per_page: int) -> tuple[list[dict], int]:
        documents, total = self._predictions.list_for_user(user_id, page=page, per_page=per_page)
        names = self._crops.names
        return [serialize_prediction_summary(document, names) for document in documents], total

    def trend_for_user(self, user_id: ObjectId) -> list[dict]:
        """Small trend charts of the soil readings across a user's recent analyses.

        Returns one entry per soil reading (N, P, K, pH) with the points of an
        SVG polyline already computed, so the page needs no charting script.
        An empty list means there are not yet two analyses to compare.
        """
        documents = self._predictions.recent_inputs_for_user(user_id, limit=TREND_POINTS)
        cards = []
        for key in TREND_FEATURES:
            values = [
                float(value)
                for value in ((document.get("inputs") or {}).get(key) for document in documents)
                if isinstance(value, (int, float))
            ]
            if len(values) < 2:
                continue
            cards.append(_trend_card(FEATURES_BY_KEY[key], values))
        return cards

    def delete_for_user(self, prediction_id: ObjectId, user_id: ObjectId) -> None:
        if not self._predictions.delete_for_user(prediction_id, user_id):
            raise NotFoundError("That analysis could not be found.")
        self._audit.record(
            "prediction.deleted", user_id=user_id, details={"prediction_id": str(prediction_id)}
        )

    # ------------------------------------------------------------- describe
    def describe(self, document: dict) -> dict:
        """Build the full, JSON-serialisable description of one analysis.

        The ML output (crop, confidence, alternatives) is read back exactly as
        stored. The rule-based interpretation is recomputed from the stored
        inputs, and is always reported under its own ``soil_analysis`` key so
        the two are never confused.
        """
        crop = self._crops.get(document["recommended_crop"])
        inputs = document["inputs"]
        complete = all(isinstance(inputs.get(key), (int, float)) for key in FEATURE_KEYS)
        assessment = self._soil.assess(inputs, crop) if complete else None
        band_key, band_label = confidence_band(document.get("confidence"))
        unusual = complete and self._prediction.is_available and self._prediction.is_unusual_combination(inputs)
        if unusual:
            # A confident-looking probability on readings that resemble nothing the
            # model has seen would mislead, so the displayed band is never better than low.
            band_key, band_label = "low", "Unreliable: unusual readings"

        violations = agronomy_service.check(document["recommended_crop"], inputs) if complete else []
        if violations and not unusual:
            band_key, band_label = "low", "Unreliable: outside the crop's limits"

        payload = serialize_prediction(document, self._crops.names)
        for alternative in payload["alternatives"]:
            alternative["summary"] = self._crops.get(alternative["crop"]).summary
        payload.update(
            {
                "confidence_band": band_key,
                "confidence_label": band_label,
                "crop": crop.to_dict(),
                "soil_analysis": assessment.to_dict() if assessment else None,
                "input_check": {"unusual_combination": bool(unusual)},
                "agronomic_check": {"source": "FAO EcoCrop", "violations": violations},
                "warnings": self._warnings(document, crop, assessment, bool(unusual), violations),
                "limitations": list(LIMITATIONS),
            }
        )
        return payload

    @staticmethod
    def _warnings(document: dict, crop, assessment, unusual: bool = False, violations: list | None = None) -> list[str]:
        warnings: list[str] = []
        inputs = document["inputs"]

        if unusual:
            warnings.append(
                "This combination of readings is unlike anything the model was trained on, even though each "
                "value may be possible on its own. Check the readings for entry mistakes (for example units "
                "or a swapped field); treat this recommendation as unreliable until you have."
            )

        for item in violations or []:
            unit = f" {item['unit']}" if item["unit"] else ""
            warnings.append(
                f"{item['label']} ({format_number(item['value'])}{unit}) is {item['direction']} the "
                f"{format_number(item['limit_low'])}–{format_number(item['limit_high'])}{unit} range in which FAO's "
                f"EcoCrop database says {crop.name.lower()} can grow at all. The model suggested it anyway; "
                "treat this recommendation as unreliable and compare the alternatives."
            )

        for key in document.get("out_of_range_fields", []):
            spec = FEATURES_BY_KEY.get(key)
            if spec is None or not isinstance(inputs.get(key), (int, float)):
                continue
            unit = f" {spec.unit}" if spec.unit else ""
            warnings.append(
                f"{spec.label} ({format_number(inputs[key])}{unit}) is outside the range the model was "
                f"trained on ({format_number(spec.model_min)}–{format_number(spec.model_max)}{unit}). The "
                "model treats it as if it sat at the edge of that range, so this recommendation is less reliable."
            )

        confidence = document.get("confidence")
        if confidence is None:
            warnings.append(
                "This analysis was saved before model confidence and alternative crops were recorded."
            )
        elif confidence < CONFIDENCE_MODERATE:
            warnings.append(
                "The model does not strongly favour any single crop for these readings. Compare the "
                "alternatives carefully before deciding."
            )

        if assessment is not None and assessment.within_count < (assessment.total_count + 1) // 2:
            warnings.append(
                f"Only {assessment.within_count} of {assessment.total_count} readings fall within the typical "
                f"range for {crop.name.lower()}. It is the closest match the model found, but your conditions "
                "differ noticeably from where this crop usually does well."
            )

        if crop.perennial:
            warnings.append(
                f"{crop.name} is a multi-year crop. The model does not consider establishment cost or the "
                "time before the first harvest."
            )
        return warnings
