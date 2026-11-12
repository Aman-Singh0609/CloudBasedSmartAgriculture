"""003 - Upgrade legacy prediction documents to schema version 2.

Legacy shape (original application)::

    {user_id: "<string>", inputs: {N, P, K, temp, hum, ph, rain},
     recommended_crop, timestamp}

Current shape::

    {schema_version: 2, user_id: ObjectId, inputs: {nitrogen, phosphorus,
     potassium, temperature, humidity, ph, rainfall}, input_sources, weather,
     recommended_crop, confidence, alternatives, out_of_range_fields, model,
     created_at}

Legacy predictions were made before confidence and alternatives were recorded,
so those are stored as ``None`` / ``[]`` rather than being invented. Migrated
documents are tagged ``_legacy_v1: true`` so ``down()`` reverts exactly those
and leaves predictions created by the current application alone.
"""

from bson import ObjectId
from bson.errors import InvalidId

VERSION = 3
DESCRIPTION = "Upgrade legacy predictions to schema v2 (named inputs, ObjectId user, metadata fields)"
REVERSIBLE = True

_INPUT_RENAMES = {
    "N": "nitrogen",
    "P": "phosphorus",
    "K": "potassium",
    "temp": "temperature",
    "hum": "humidity",
    "ph": "ph",
    "rain": "rainfall",
}
_LEGACY_QUERY = {"schema_version": {"$exists": False}}


def _to_object_id(value):
    if isinstance(value, ObjectId):
        return value
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        return None


def plan(db) -> dict:
    return {"affected_documents": db["predictions"].count_documents(_LEGACY_QUERY)}


def up(db) -> dict:
    upgraded = unlinked = 0
    for doc in db["predictions"].find(_LEGACY_QUERY):
        legacy_inputs = doc.get("inputs") or {}
        inputs = {new: legacy_inputs.get(old) for old, new in _INPUT_RENAMES.items()}

        user_id = _to_object_id(doc.get("user_id"))
        if user_id is None:
            # Cannot be linked to an account; keep the original value so no
            # information is lost. The record simply stays invisible to users.
            user_id = doc.get("user_id")
            unlinked += 1

        update = {
            "$set": {
                "schema_version": 2,
                "user_id": user_id,
                "inputs": inputs,
                "input_sources": {key: "manual" for key in inputs},
                "weather": None,
                "confidence": None,
                "alternatives": [],
                "out_of_range_fields": [],
                "model": None,
                "created_at": doc.get("timestamp"),
                "_legacy_v1": True,
            },
            "$unset": {"timestamp": ""},
        }
        db["predictions"].update_one({"_id": doc["_id"]}, update)
        upgraded += 1
    return {"predictions_upgraded": upgraded, "predictions_without_valid_user": unlinked}


def down(db) -> dict:
    reverted = 0
    for doc in db["predictions"].find({"_legacy_v1": True}):
        inputs = doc.get("inputs") or {}
        legacy_inputs = {old: inputs.get(new) for old, new in _INPUT_RENAMES.items()}
        user_id = doc.get("user_id")
        update = {
            "$set": {
                "user_id": str(user_id) if isinstance(user_id, ObjectId) else user_id,
                "inputs": legacy_inputs,
                "timestamp": doc.get("created_at"),
            },
            "$unset": {
                "schema_version": "",
                "input_sources": "",
                "weather": "",
                "confidence": "",
                "alternatives": "",
                "out_of_range_fields": "",
                "model": "",
                "created_at": "",
                "_legacy_v1": "",
            },
        }
        db["predictions"].update_one({"_id": doc["_id"]}, update)
        reverted += 1
    return {"predictions_reverted": reverted}
