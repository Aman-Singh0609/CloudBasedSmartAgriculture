"""Creating, listing, reading and deleting crop analyses."""

from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from app.errors import ModelUnavailableError
from app.models.features import FEATURES
from tests.conftest import RICE_INPUTS, assert_error, assert_success, predict_json, register_and_login

CROP_LABELS = [
    "rice", "maize", "chickpea", "kidneybeans", "pigeonpeas", "mothbeans", "mungbean", "blackgram",
    "lentil", "pomegranate", "banana", "mango", "grapes", "watermelon", "muskmelon", "apple",
    "orange", "papaya", "coconut", "cotton", "jute", "coffee",
]


# --------------------------------------------------------------------- create
def test_valid_prediction_returns_the_full_result(auth_client):
    response = predict_json(auth_client)
    data = assert_success(response, 201)

    assert data["recommended_crop"] == "rice"
    assert data["crop_name"] == "Rice"
    assert 0.5 < data["confidence"] <= 1
    assert data["confidence_band"] == "high"
    assert "model agreement" in data["confidence_label"].lower()
    assert data["inputs"] == {key: float(value) for key, value in RICE_INPUTS.items()}
    assert data["reliability"] == "normal"
    assert data["out_of_range_fields"] == []
    assert set(data["input_sources"].values()) == {"manual"}
    assert data["weather"] is None
    assert data["created_at"].endswith("Z")
    assert data["crop"]["label"] == "rice" and data["crop"]["description"]
    assert data["model"]["model_type"] == "VotingClassifier"
    assert len(data["limitations"]) >= 3
    assert response.headers["Location"] == f"/api/predictions/{data['id']}"


def test_alternatives_are_ranked_and_capped_at_three(auth_client):
    # A point between rice and jute, where the model splits its vote.
    data = assert_success(predict_json(auth_client, nitrogen=80, potassium=40, temperature=25, humidity=82, ph=6.8, rainfall=190), 201)
    alternatives = data["alternatives"]
    assert 1 <= len(alternatives) <= 3
    probabilities = [alt["probability"] for alt in alternatives]
    assert probabilities == sorted(probabilities, reverse=True)
    assert all(0 < p <= data["confidence"] for p in probabilities)
    assert data["confidence"] + sum(probabilities) <= 1.0001
    for alt in alternatives:
        assert alt["crop"] in CROP_LABELS and alt["crop"] != data["recommended_crop"]
        assert alt["crop_name"] and alt["summary"]


def test_soil_analysis_is_reported_separately_and_labelled_rule_based(auth_client):
    data = assert_success(predict_json(auth_client, phosphorus=20), 201)
    analysis = data["soil_analysis"]
    assert analysis["method"] == "rule_based"
    assert analysis["compared_with"] == data["recommended_crop"]
    assert analysis["total_readings"] == 7
    readings = {reading["key"]: reading for reading in analysis["readings"]}
    assert set(readings) == {spec.key for spec in FEATURES}
    assert readings["phosphorus"]["status"] == "below"
    assert readings["phosphorus"]["status_label"] == "Below typical range"
    assert readings["nitrogen"]["status"] == "within"
    assert any("Phosphorus is below" in tip for tip in analysis["suggestions"])
    assert analysis["ph_class"] == "Neutral"
    for reading in readings.values():
        assert 0 <= reading["chart"]["marker"] <= 100


def test_prediction_is_saved_for_the_user(auth_client, db, user_id):
    data = assert_success(predict_json(auth_client), 201)
    stored = db["predictions"].find_one({"_id": ObjectId(data["id"])})
    assert stored["user_id"] == user_id
    assert isinstance(stored["user_id"], ObjectId)
    assert stored["schema_version"] == 2
    assert stored["recommended_crop"] == "rice"
    assert stored["confidence"] == data["confidence"]
    assert stored["inputs"]["ph"] == 6.5
    assert stored["created_at"].tzinfo is not None
    assert abs(datetime.now(timezone.utc) - stored["created_at"]) < timedelta(seconds=30)
    assert db["predictions"].count_documents({}) == 1


def test_client_cannot_choose_the_owner_or_the_result(auth_client, db, user_id):
    data = assert_success(
        predict_json(auth_client, user_id="000000000000000000000000", recommended_crop="coffee", confidence=1, input_sources={"rainfall": "weather_api"}),
        201,
    )
    stored = db["predictions"].find_one({})
    assert stored["user_id"] == user_id
    assert data["recommended_crop"] == "rice"
    assert stored["input_sources"]["rainfall"] == "manual"


@pytest.mark.parametrize("field", [spec.key for spec in FEATURES])
def test_missing_field_is_rejected(auth_client, db, field):
    payload = dict(RICE_INPUTS)
    del payload[field]
    error = assert_error(auth_client.post("/api/predictions", json=payload), 422, "INVALID_INPUT")
    assert list(error["details"]) == [field]
    assert db["predictions"].count_documents({}) == 0


@pytest.mark.parametrize("bad", ["abc", "", None, True, [], {"$gt": 0}, "1e309", "1_000", "nan"])
def test_non_numeric_field_is_rejected(auth_client, db, bad):
    error = assert_error(predict_json(auth_client, ph=bad), 422, "INVALID_INPUT")
    assert "ph" in error["details"]
    assert db["predictions"].count_documents({}) == 0


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
def test_json_nan_and_infinity_literals_are_rejected(auth_client, db, literal):
    """Python's JSON parser accepts these non-standard literals; the validator must not."""
    body = '{"nitrogen": %s, "phosphorus": 42, "potassium": 43, "temperature": 20.8, "humidity": 82, "ph": 6.5, "rainfall": 202.9}' % literal
    response = auth_client.post("/api/predictions", data=body, content_type="application/json")
    assert response.status_code in (400, 422)
    assert response.get_json()["success"] is False
    assert db["predictions"].count_documents({}) == 0


@pytest.mark.parametrize(
    "field,value",
    [("nitrogen", -1), ("phosphorus", -0.5), ("potassium", -10), ("humidity", -1), ("rainfall", -5), ("ph", -1)],
)
def test_negative_values_are_rejected(auth_client, field, value):
    error = assert_error(predict_json(auth_client, **{field: value}), 422, "INVALID_INPUT")
    assert "must be between" in error["details"][field]


@pytest.mark.parametrize(
    "field,value",
    [("ph", 14.5), ("humidity", 101), ("temperature", 75), ("temperature", -40), ("nitrogen", 5000), ("rainfall", 1e12), ("potassium", 401)],
)
def test_out_of_range_values_are_rejected(auth_client, field, value):
    error = assert_error(predict_json(auth_client, **{field: value}), 422, "INVALID_INPUT")
    assert field in error["details"]


def test_ph_error_is_human_readable(auth_client):
    error = assert_error(predict_json(auth_client, ph=15), 422, "INVALID_INPUT")
    assert error["details"]["ph"] == "Soil pH must be between 0 and 14."


def test_values_outside_the_model_range_are_flagged_not_hidden(auth_client):
    data = assert_success(predict_json(auth_client, rainfall=900, nitrogen=250), 201)
    assert data["reliability"] == "reduced"
    assert sorted(data["out_of_range_fields"]) == ["nitrogen", "rainfall"]
    warnings = " ".join(data["warnings"])
    assert "Rainfall (900 mm) is outside the range the model was trained on" in warnings
    assert "Nitrogen (N) (250)" in warnings
    assert "less reliable" in warnings


def test_low_confidence_results_carry_a_warning(auth_client):
    # Readings chosen to sit between several crops.
    data = assert_success(predict_json(auth_client, nitrogen=50, phosphorus=50, potassium=50, temperature=25, humidity=60, ph=6.5, rainfall=100), 201)
    if data["confidence"] < 0.5:
        assert data["confidence_band"] == "low"
        assert any("does not strongly favour" in warning for warning in data["warnings"])
    else:
        assert data["confidence_band"] in ("moderate", "high")


def test_perennial_crops_carry_a_long_term_warning(auth_client, crop_midpoints):
    data = assert_success(predict_json(auth_client, crop_midpoints["mango"]), 201)
    assert data["recommended_crop"] == "mango"
    assert any("multi-year crop" in warning for warning in data["warnings"])


def test_wording_never_promises_success(auth_client):
    body = predict_json(auth_client).get_data(as_text=True).lower()
    for phrase in ("chance your crop will succeed", "guarantee", "will succeed", "probability of success"):
        assert phrase not in body or phrase == "guarantee" and "does not guarantee" in body or "not the chance" in body


def test_model_unavailable_returns_503_and_saves_nothing(app, auth_client, db, monkeypatch):
    def unavailable(_inputs):
        raise ModelUnavailableError()

    monkeypatch.setattr(app.extensions["services"].prediction, "predict", unavailable)
    error = assert_error(predict_json(auth_client), 503, "MODEL_UNAVAILABLE")
    assert "not available" in error["message"]
    assert db["predictions"].count_documents({}) == 0


def test_prediction_requires_json_body(auth_client):
    assert_error(auth_client.post("/api/predictions", data=dict(RICE_INPUTS)), 415, "UNSUPPORTED_MEDIA_TYPE")
    assert_error(auth_client.post("/api/predictions", data="[1,2,3]", content_type="application/json"), 400, "BAD_REQUEST")
    assert_error(auth_client.post("/api/predictions", data="{oops", content_type="application/json"), 400, "BAD_REQUEST")


def test_oversized_body_is_rejected(auth_client):
    response = auth_client.post("/api/predictions", data="x" * (70 * 1024), content_type="application/json")
    assert_error(response, 413, "PAYLOAD_TOO_LARGE")


@pytest.mark.parametrize("label", CROP_LABELS)
def test_every_model_crop_produces_a_complete_result(auth_client, crop_midpoints, label):
    """All 22 output classes: predicted, saved, and described with real crop details."""
    data = assert_success(predict_json(auth_client, crop_midpoints[label]), 201)
    assert data["recommended_crop"] == label
    crop = data["crop"]
    assert crop["name"] and crop["description"] and crop["summary"] and crop["season"]
    assert set(crop["growing_conditions"]) == {"climate", "soil", "water"}
    assert "not available" not in str(crop).lower()
    assert data["soil_analysis"]["within_typical_range"] == 7

    fetched = assert_success(auth_client.get(f"/api/predictions/{data['id']}"))
    assert fetched["recommended_crop"] == label
    assert fetched["crop"] == crop


# ----------------------------------------------------------------------- read
def test_fetching_a_saved_prediction_returns_the_same_result(auth_client):
    created = assert_success(predict_json(auth_client), 201)
    fetched = assert_success(auth_client.get(f"/api/predictions/{created['id']}"))
    assert fetched == created


@pytest.mark.parametrize("bad_id", ["abc", "z" * 24, "1" * 23, "1" * 25, "aaaaaaaaaaaaaaaaaaaaaaaa"])
def test_unknown_or_malformed_ids_are_not_found(auth_client, bad_id):
    assert_error(auth_client.get(f"/api/predictions/{bad_id}"), 404, "NOT_FOUND")
    assert_error(auth_client.delete(f"/api/predictions/{bad_id}"), 404, "NOT_FOUND")


def test_users_cannot_read_or_delete_each_others_predictions(auth_client, other_client, db):
    mine = assert_success(predict_json(auth_client), 201)["id"]

    assert_error(other_client.get(f"/api/predictions/{mine}"), 404, "NOT_FOUND")
    assert_error(other_client.delete(f"/api/predictions/{mine}"), 404, "NOT_FOUND")
    assert assert_success(other_client.get("/api/predictions"))["items"] == []
    assert assert_success(other_client.get("/api/predictions/trends"))["trends"] == []

    # still there for the owner
    assert db["predictions"].count_documents({"_id": ObjectId(mine)}) == 1
    assert_success(auth_client.get(f"/api/predictions/{mine}"))


def test_legacy_predictions_without_confidence_are_described_honestly(auth_client, db, user_id):
    legacy_id = db["predictions"].insert_one(
        {
            "schema_version": 2,
            "user_id": user_id,
            "inputs": {key: float(value) for key, value in RICE_INPUTS.items()},
            "input_sources": {},
            "weather": None,
            "recommended_crop": "rice",
            "confidence": None,
            "alternatives": [],
            "out_of_range_fields": [],
            "model": None,
            "created_at": datetime.now(timezone.utc),
            "_legacy_v1": True,
        }
    ).inserted_id
    data = assert_success(auth_client.get(f"/api/predictions/{legacy_id}"))
    assert data["confidence"] is None
    assert data["confidence_band"] == "unknown"
    assert any("before model confidence" in warning for warning in data["warnings"])


# ----------------------------------------------------------------------- list
def test_empty_history(auth_client):
    data = assert_success(auth_client.get("/api/predictions"))
    assert data["items"] == []
    assert data["pagination"] == {"page": 1, "per_page": 10, "total": 0, "total_pages": 1, "has_previous": False, "has_next": False}
    assert assert_success(auth_client.get("/api/predictions/trends"))["trends"] == []


def test_history_lists_only_the_users_own_records_newest_first(auth_client, other_client):
    first = assert_success(predict_json(auth_client), 201)["id"]
    second = assert_success(predict_json(auth_client, nitrogen=85), 201)["id"]
    assert_success(predict_json(other_client), 201)

    data = assert_success(auth_client.get("/api/predictions"))
    assert [item["id"] for item in data["items"]] == [second, first]
    assert data["pagination"]["total"] == 2
    item = data["items"][0]
    assert set(item) == {"id", "recommended_crop", "crop_name", "confidence", "inputs", "reliability", "created_at"}
    assert set(item["inputs"]) == {spec.key for spec in FEATURES}


def test_pagination(auth_client, db, user_id):
    base = datetime.now(timezone.utc) - timedelta(days=1)
    db["predictions"].insert_many(
        [
            {
                "schema_version": 2,
                "user_id": user_id,
                "inputs": {key: float(value) for key, value in RICE_INPUTS.items()},
                "recommended_crop": "rice",
                "confidence": 0.9,
                "alternatives": [],
                "out_of_range_fields": [],
                "created_at": base + timedelta(minutes=index),
            }
            for index in range(25)
        ]
    )
    page1 = assert_success(auth_client.get("/api/predictions"))
    assert len(page1["items"]) == 10
    assert page1["pagination"] == {"page": 1, "per_page": 10, "total": 25, "total_pages": 3, "has_previous": False, "has_next": True}

    page3 = assert_success(auth_client.get("/api/predictions?page=3"))
    assert len(page3["items"]) == 5
    assert page3["pagination"]["has_next"] is False and page3["pagination"]["has_previous"] is True

    all_ids = []
    for page in (1, 2, 3):
        all_ids += [item["id"] for item in assert_success(auth_client.get(f"/api/predictions?page={page}"))["items"]]
    assert len(set(all_ids)) == 25

    times = [item["created_at"] for item in page1["items"]]
    assert times == sorted(times, reverse=True)

    assert len(assert_success(auth_client.get("/api/predictions?per_page=50"))["items"]) == 25
    assert assert_success(auth_client.get("/api/predictions?page=9"))["items"] == []


@pytest.mark.parametrize("query,field", [("page=0", "page"), ("page=abc", "page"), ("page=-1", "page"), ("per_page=51", "per_page"), ("per_page=0", "per_page"), ("per_page=x", "per_page")])
def test_invalid_paging_parameters_are_rejected(auth_client, query, field):
    error = assert_error(auth_client.get(f"/api/predictions?{query}"), 422, "INVALID_INPUT")
    assert field in error["details"]


def test_trends_describe_how_soil_readings_changed(auth_client):
    for nitrogen in (60, 75, 90):
        predict_json(auth_client, nitrogen=nitrogen)
    trends = {card["key"]: card for card in assert_success(auth_client.get("/api/predictions/trends"))["trends"]}
    assert set(trends) == {"nitrogen", "phosphorus", "potassium", "ph"}
    nitrogen = trends["nitrogen"]
    assert nitrogen["count"] == 3
    assert nitrogen["values_text"] == "60, 75, 90"
    assert (nitrogen["first_text"], nitrogen["latest_text"], nitrogen["change"]) == ("60", "90", "up")
    assert len(nitrogen["points"].split()) == 3
    assert trends["ph"]["change"] == "same"


# --------------------------------------------------------------------- delete
def test_delete_own_prediction(auth_client, db):
    prediction_id = assert_success(predict_json(auth_client), 201)["id"]
    assert assert_success(auth_client.delete(f"/api/predictions/{prediction_id}")) == {"id": prediction_id}
    assert db["predictions"].count_documents({}) == 0
    assert_error(auth_client.get(f"/api/predictions/{prediction_id}"), 404, "NOT_FOUND")
    assert_error(auth_client.delete(f"/api/predictions/{prediction_id}"), 404, "NOT_FOUND")


def test_history_survives_logout_and_login(app, db):
    client = register_and_login(app)
    prediction_id = assert_success(predict_json(client), 201)["id"]
    client.post("/api/auth/logout")
    client.post("/api/auth/login", json={"email": "asha@example.test", "password": "Tr4ctor-Lane-Meadow"})
    assert assert_success(client.get("/api/predictions"))["items"][0]["id"] == prediction_id


def test_unusual_combination_is_flagged_and_shown_as_low_reliability(auth_client):
    odd = {"nitrogen": 101, "phosphorus": 66, "potassium": 129, "ph": 4.7,
           "temperature": 12.2, "humidity": 19.6, "rainfall": 173.9}
    data = assert_success(predict_json(auth_client, odd), 201)
    assert data["input_check"] == {"unusual_combination": True}
    assert data["confidence_band"] == "low"
    assert any("unlike anything the model was trained on" in w for w in data["warnings"])


def test_typical_readings_are_not_flagged_unusual(auth_client):
    data = assert_success(predict_json(auth_client), 201)
    assert data["input_check"] == {"unusual_combination": False}


def test_crop_outside_agronomic_limits_is_flagged(auth_client, prediction_service, monkeypatch):
    """Force the model to suggest rice at 45 C; the independent FAO check must object."""
    from app.models.schemas import CropProbability, ModelPrediction

    monkeypatch.setattr(
        prediction_service, "predict",
        lambda inputs: ModelPrediction("rice", 0.95, (CropProbability("maize", 0.03),)),
    )
    data = assert_success(predict_json(auth_client, temperature=43.0), 201)
    violations = data["agronomic_check"]["violations"]
    assert [v["field"] for v in violations] == ["temperature"]
    assert data["confidence_band"] == "low"
    assert any("EcoCrop" in w and "can grow at all" in w for w in data["warnings"])


def test_normal_result_has_no_agronomic_violations(auth_client):
    data = assert_success(predict_json(auth_client), 201)
    assert data["agronomic_check"] == {"source": "FAO EcoCrop", "violations": []}
