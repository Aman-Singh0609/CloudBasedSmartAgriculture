"""Data access for user accounts.

Queries:

* ``find_by_email``   -> unique index ``users.email``
* ``find_by_id``      -> default ``_id`` index

The password hash is only returned by ``find_by_email``, which is needed to
verify a login. ``find_by_id`` (used on every authenticated request) applies a
projection that leaves the hash out.
"""

from __future__ import annotations

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from app.db import USERS
from app.repositories.base import Repository, utcnow

_PUBLIC_PROJECTION = {"name": 1, "email": 1, "session_version": 1, "created_at": 1}


class DuplicateEmailError(Exception):
    """An account with this email address already exists."""


class UserRepository(Repository):
    collection_name = USERS

    def find_by_email(self, email: str) -> dict | None:
        return self.collection.find_one({"email": email})

    def find_by_id(self, user_id: ObjectId) -> dict | None:
        return self.collection.find_one({"_id": user_id}, _PUBLIC_PROJECTION)

    def create(self, *, name: str, email: str, password_hash: str) -> dict:
        now = utcnow()
        document = {
            "name": name,
            "email": email,
            "password_hash": password_hash,
            "session_version": 1,
            "created_at": now,
            "updated_at": now,
            "last_login_at": None,
        }
        try:
            result = self.collection.insert_one(document)
        except DuplicateKeyError as exc:
            # The unique index is the real guarantee: it also covers two
            # simultaneous signups that both passed an existence check.
            raise DuplicateEmailError(email) from exc
        document["_id"] = result.inserted_id
        return document

    def record_login(self, user_id: ObjectId) -> None:
        self.collection.update_one({"_id": user_id}, {"$set": {"last_login_at": utcnow()}})

    def update_password(self, user_id: ObjectId, password_hash: str) -> bool:
        """Store a new password hash and invalidate every existing session."""
        result = self.collection.update_one(
            {"_id": user_id},
            {
                "$set": {"password_hash": password_hash, "updated_at": utcnow()},
                "$inc": {"session_version": 1},
            },
        )
        return result.matched_count == 1
