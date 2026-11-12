"""Crop reference data and the rule-based soil interpretation."""

import json

import pytest

from app.models.features import FEATURE_KEYS, FEATURES, FEATURES_BY_KEY
from app.services.crop_service import CropDataError, CropService
from app.services.soil_service import (
    STATUS_ABOVE,
    STATUS_BELOW,
    STATUS_WITHIN,
    SoilService,
    classify_ph,
    confidence_band,
)

MODEL_LABELS = [
    "rice", "maize", "chickpea", "kidneybeans", "pigeonpeas", "mothbeans", "mungbean", "blackgram",
    "lentil", "pomegranate", "banana", "mango", "grapes", "watermelon", "muskmelon", "apple",
    "orange", "papaya", "coconut", "cotton", "jute", "coffee",
]


# ------------------------------------------------------------- feature specs
def test_there_are_exactly_seven_features_in_a_stable_order():
    assert FEATURE_KEYS == ("nitrogen", "phosphorus", "potassium", "ph", "temperature", "humidity", "rainfall")


@pytest.mark.parametrize("spec", FEATURES, ids=lambda spec: spec.key)
def test_feature_ranges_are_consistent(spec):
    assert spec.hard_min <= spec.model_min < spec.model_max <= spec.hard_max
    assert spec.model_min <= spec.example <= spec.model_max
    assert spec.label and spec.help and spec.step > 0


# ----------------------------------------------------------------- crop data
def test_all_22_model_crops_have_details(crop_service):
    assert sorted(crop_service.labels) == sorted(MODEL_LABELS)
    assert len(crop_service.labels) == 22


def test_wheat_is_not_listed(crop_service):
    """Wheat is not an output class of the model, so it must not appear."""
    assert "wheat" not in {label.lower() for label in crop_service.labels}
    assert "Wheat" not in crop_service.names.values()


@pytest.mark.parametrize("label", MODEL_LABELS)
def test_each_crop_is_fully_described(crop_service, label):
    crop = crop_service.get(label)
    assert crop.label == label
    assert crop.name and crop.name[0].isupper()
    assert len(crop.summary) > 20 and len(crop.description) > 60
    assert crop.scientific_name and crop.category and crop.season
    assert set(crop.growing_conditions) == {"climate", "soil", "water"}
    assert all(len(text) > 15 for text in crop.growing_conditions.values())
    assert len(crop.cautions) >= 1
    assert "not available" not in (crop.description + crop.summary).lower()


@pytest.mark.parametrize("label", MODEL_LABELS)
def test_typical_ranges_are_inside_the_model_reference_range(crop_service, label):
    for key, (low, high) in crop_service.get(label).typical_ranges.items():
        spec = FEATURES_BY_KEY[key]
        assert low <= high
        assert spec.model_min - 0.5 <= low and high <= spec.model_max + 0.5, (label, key)


def test_display_names_are_human_readable(crop_service):
    names = crop_service.names
    assert names["kidneybeans"] == "Kidney beans"
    assert names["pigeonpeas"] == "Pigeon peas"
    assert names["mothbeans"] == "Moth beans"
    assert names["mungbean"] == "Mung bean"
    assert names["blackgram"] == "Black gram"


def test_unknown_label_raises_instead_of_returning_a_generic_fallback(crop_service):
    with pytest.raises(CropDataError):
        crop_service.get("wheat")


def test_validate_against_model_detects_missing_and_extra_crops(crop_service):
    crop_service.validate_against_model(MODEL_LABELS)
    with pytest.raises(CropDataError, match="missing details for: quinoa"):
        crop_service.validate_against_model(MODEL_LABELS + ["quinoa"])
    with pytest.raises(CropDataError, match="cannot output: rice"):
        crop_service.validate_against_model([label for label in MODEL_LABELS if label != "rice"])


def _write(tmp_path, document):
    path = tmp_path / "crops.json"
    path.write_text(json.dumps(document) if not isinstance(document, str) else document, encoding="utf-8")
    return path


def _valid_crop():
    return {
        "name": "Rice",
        "scientific_name": "Oryza sativa",
        "category": "Cereal",
        "summary": "s",
        "description": "d",
        "season": "kharif",
        "growing_conditions": {"climate": "c", "soil": "s", "water": "w"},
        "cautions": ["x"],
        "typical_ranges": {key: [1, 2] for key in FEATURE_KEYS},
    }


@pytest.mark.parametrize(
    "mutate",
    [
        lambda crop: crop.pop("description"),
        lambda crop: crop.__setitem__("name", ""),
        lambda crop: crop.pop("growing_conditions"),
        lambda crop: crop["growing_conditions"].pop("water"),
        lambda crop: crop["typical_ranges"].pop("ph"),
        lambda crop: crop["typical_ranges"].__setitem__("ph", [9, 3]),
        lambda crop: crop["typical_ranges"].__setitem__("ph", [1]),
        lambda crop: crop["typical_ranges"].__setitem__("ph", ["a", "b"]),
        lambda crop: crop.__setitem__("cautions", "not a list"),
    ],
)
def test_malformed_crop_entries_are_rejected_at_load(tmp_path, mutate):
    crop = _valid_crop()
    mutate(crop)
    with pytest.raises(CropDataError):
        CropService(_write(tmp_path, {"crops": {"rice": crop}}))


@pytest.mark.parametrize("document", ["{not json", {"crops": {}}, {"nothing": 1}, []])
def test_unreadable_crop_files_are_rejected(tmp_path, document):
    with pytest.raises(CropDataError):
        CropService(_write(tmp_path, document))


def test_missing_crop_file_is_rejected(tmp_path):
    with pytest.raises(CropDataError):
        CropService(tmp_path / "missing.json")


# ------------------------------------------------------- soil interpretation
@pytest.mark.parametrize(
    "ph,label",
    [
        (0, "Extremely acidic"),
        (4.49, "Extremely acidic"),
        (4.5, "Strongly acidic"),
        (5.49, "Strongly acidic"),
        (5.5, "Slightly acidic"),
        (6.49, "Slightly acidic"),
        (6.5, "Neutral"),
        (7.0, "Neutral"),
        (7.49, "Neutral"),
        (7.5, "Slightly alkaline"),
        (8.49, "Slightly alkaline"),
        (8.5, "Strongly alkaline"),
        (14, "Strongly alkaline"),
    ],
)
def test_ph_classification_boundaries(ph, label):
    assert classify_ph(ph)[0] == label
    assert classify_ph(ph)[1]


@pytest.mark.parametrize(
    "confidence,band",
    [(None, "unknown"), (0.0, "low"), (0.49, "low"), (0.5, "moderate"), (0.79, "moderate"), (0.8, "high"), (1.0, "high")],
)
def test_confidence_bands(confidence, band):
    assert confidence_band(confidence)[0] == band


def test_confidence_labels_do_not_promise_success():
    for confidence in (0.2, 0.6, 0.95):
        label = confidence_band(confidence)[1].lower()
        assert "model agreement" in label
        assert "success" not in label and "chance" not in label


def _rice_inputs(**overrides):
    inputs = {"nitrogen": 80, "phosphorus": 45, "potassium": 40, "temperature": 24, "humidity": 82, "ph": 6.5, "rainfall": 220}
    inputs.update(overrides)
    return inputs


def test_readings_inside_the_typical_range(crop_service):
    assessment = SoilService().assess(_rice_inputs(), crop_service.get("rice"))
    assert assessment.within_count == assessment.total_count == 7
    assert all(reading.status == STATUS_WITHIN for reading in assessment.readings)
    assert assessment.tips == ()
    assert assessment.ph_class == "Neutral"


def test_low_and_high_readings_are_classified_with_suggestions(crop_service):
    assessment = SoilService().assess(_rice_inputs(phosphorus=10, potassium=120, ph=8.4), crop_service.get("rice"))
    by_key = {reading.key: reading for reading in assessment.readings}
    assert by_key["phosphorus"].status == STATUS_BELOW
    assert by_key["phosphorus"].status_label == "Below typical range"
    assert by_key["potassium"].status == STATUS_ABOVE
    assert by_key["ph"].status == STATUS_ABOVE
    assert assessment.within_count == 4
    assert len(assessment.tips) == 3
    assert any("Phosphorus is below" in tip for tip in assessment.tips)
    assert any("more alkaline" in tip for tip in assessment.tips)
    assert "rice" in assessment.tips[0]


def test_range_edges_count_as_within(crop_service):
    low, high = crop_service.get("rice").typical_ranges["nitrogen"]
    for value in (low, high):
        assessment = SoilService().assess(_rice_inputs(nitrogen=value), crop_service.get("rice"))
        assert assessment.readings[0].status == STATUS_WITHIN


def test_suggestions_never_prescribe_quantities(crop_service):
    """Tips are general; amounts are deferred to a soil test / extension office."""
    assessment = SoilService().assess(
        _rice_inputs(nitrogen=0, phosphorus=0, potassium=0, ph=3.6, temperature=40, humidity=20, rainfall=30),
        crop_service.get("rice"),
    )
    assert len(assessment.tips) == 7
    for tip in assessment.tips:
        assert not any(unit in tip for unit in ("kg", "tonne", "g/", "per hectare", "per acre"))
    assert "soil test" in assessment.amount_advice


@pytest.mark.parametrize("label", MODEL_LABELS)
def test_gauge_positions_stay_on_the_chart(crop_service, label):
    crop = crop_service.get(label)
    extremes = [
        {spec.key: spec.hard_min for spec in FEATURES},
        {spec.key: spec.hard_max for spec in FEATURES},
        {spec.key: spec.example for spec in FEATURES},
    ]
    for inputs in extremes:
        for reading in SoilService().assess(inputs, crop).readings:
            assert 0 <= reading.marker_pct <= 100
            assert 0 <= reading.band_start_pct <= 100
            assert reading.band_start_pct + reading.band_width_pct <= 101


def test_assessment_is_labelled_as_rule_based(crop_service):
    payload = SoilService().assess(_rice_inputs(), crop_service.get("rice")).to_dict()
    assert payload["method"] == "rule_based"
    assert payload["compared_with"] == "rice"
    assert len(payload["readings"]) == 7


def test_soil_and_climate_readings_are_separated(crop_service):
    assessment = SoilService().assess(_rice_inputs(), crop_service.get("rice"))
    assert [reading.key for reading in assessment.soil_readings] == ["nitrogen", "phosphorus", "potassium", "ph"]
    assert [reading.key for reading in assessment.climate_readings] == ["temperature", "humidity", "rainfall"]
