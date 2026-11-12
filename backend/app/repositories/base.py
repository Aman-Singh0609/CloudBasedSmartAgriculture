"""Shared plumbing for repositories."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

from pymongo.collection import Collection
from pymongo.database import Database as MongoDatabase

DbProvider = Callable[[], MongoDatabase]


def utcnow() -> datetime:
    """Timezone-aware current time in UTC, truncated to MongoDB's millisecond precision."""
    now = datetime.now(timezone.utc)
    return now.replace(microsecond=(now.microsecond // 1000) * 1000)


class Repository:
    """Base class: resolves its collection lazily through a database provider.

    Taking a provider rather than a database object keeps repositories usable
    before a connection exists and lets tests supply an in-memory database.
    """

    collection_name: str

    def __init__(self, db_provider: DbProvider) -> None:
        self._db_provider = db_provider

    @property
    def collection(self) -> Collection:
        return self._db_provider()[self.collection_name]
