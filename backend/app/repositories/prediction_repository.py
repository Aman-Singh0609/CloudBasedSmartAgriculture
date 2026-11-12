"""Data access for saved crop analyses.

Every query is scoped by ``user_id`` so one user can never read or delete
another user's records, even with a valid record ID.

Queries and the index that serves them:

* history page / API list  ``{user_id} sort created_at desc``
                           -> compound index ``(user_id, created_at desc)``
* count for pagination     ``{user_id}``                     -> same index (prefix)
* open / delete one        ``{_id, user_id}``                -> ``_id`` index
* trend series             ``{user_id} sort created_at desc limit n``
                           -> same compound index
"""

from __future__ import annotations

from bson import ObjectId
from pymongo import DESCENDING

from app.db import PREDICTIONS
from app.repositories.base import Repository, utcnow

PREDICTION_SCHEMA_VERSION = 2

_LIST_PROJECTION = {
    "recommended_crop": 1,
    "confidence": 1,
    "inputs": 1,
    "out_of_range_fields": 1,
    "created_at": 1,
}


class PredictionRepository(Repository):
    collection_name = PREDICTIONS

    def create(
        self,
        *,
        user_id: ObjectId,
        inputs: dict[str, float],
        input_sources: dict[str, str],
        weather: dict | None,
        recommended_crop: str,
        confidence: float,
        alternatives: list[dict],
        out_of_range_fields: list[str],
        model: dict,
    ) -> dict:
        document = {
            "schema_version": PREDICTION_SCHEMA_VERSION,
            "user_id": user_id,
            "inputs": inputs,
            "input_sources": input_sources,
            "weather": weather,
            "recommended_crop": recommended_crop,
            "confidence": confidence,
            "alternatives": alternatives,
            "out_of_range_fields": out_of_range_fields,
            "model": model,
            "created_at": utcnow(),
        }
        result = self.collection.insert_one(document)
        document["_id"] = result.inserted_id
        return document

    def find_for_user(self, prediction_id: ObjectId, user_id: ObjectId) -> dict | None:
        return self.collection.find_one({"_id": prediction_id, "user_id": user_id})

    def list_for_user(self, user_id: ObjectId, *, page: int, per_page: int) -> tuple[list[dict], int]:
        """Return one page of a user's analyses (newest first) and the total count."""
        query = {"user_id": user_id}
        total = self.collection.count_documents(query)
        cursor = (
            self.collection.find(query, _LIST_PROJECTION)
            .sort([("created_at", DESCENDING), ("_id", DESCENDING)])
            .skip((page - 1) * per_page)
            .limit(per_page)
        )
        return list(cursor), total

    def recent_inputs_for_user(self, user_id: ObjectId, *, limit: int) -> list[dict]:
        """Newest ``limit`` analyses, oldest first, for the trend charts."""
        cursor = (
            self.collection.find({"user_id": user_id}, {"inputs": 1, "created_at": 1, "recommended_crop": 1})
            .sort([("created_at", DESCENDING), ("_id", DESCENDING)])
            .limit(limit)
        )
        return list(cursor)[::-1]

    def delete_for_user(self, prediction_id: ObjectId, user_id: ObjectId) -> bool:
        result = self.collection.delete_one({"_id": prediction_id, "user_id": user_id})
        return result.deleted_count == 1
