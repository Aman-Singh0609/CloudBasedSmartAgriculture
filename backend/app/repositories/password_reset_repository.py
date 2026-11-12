"""Data access for password-reset tokens.

Only the SHA-256 digest of a token is stored, so a database leak does not
reveal usable reset links. A TTL index on ``expires_at`` deletes expired
tokens; ``find_valid`` also checks the expiry itself because TTL deletion runs
on a background schedule and can lag by a minute.
"""

from __future__ import annotations

from datetime import datetime

from bson import ObjectId

from app.db import PASSWORD_RESET_TOKENS
from app.repositories.base import Repository, utcnow


class PasswordResetRepository(Repository):
    collection_name = PASSWORD_RESET_TOKENS

    def create(self, *, user_id: ObjectId, token_hash: str, expires_at: datetime) -> None:
        # One live token per user: a new request replaces any earlier one.
        self.collection.delete_many({"user_id": user_id})
        self.collection.insert_one(
            {"user_id": user_id, "token_hash": token_hash, "expires_at": expires_at, "created_at": utcnow()}
        )

    def find_valid(self, token_hash: str) -> dict | None:
        return self.collection.find_one({"token_hash": token_hash, "expires_at": {"$gt": utcnow()}})

    def consume(self, token_hash: str) -> dict | None:
        """Atomically fetch and delete a valid token so it can be used only once."""
        return self.collection.find_one_and_delete(
            {"token_hash": token_hash, "expires_at": {"$gt": utcnow()}}
        )

    def delete_for_user(self, user_id: ObjectId) -> None:
        self.collection.delete_many({"user_id": user_id})
