"""Data access for audit events and per-request API timing records."""

from __future__ import annotations

from datetime import datetime

from pymongo import DESCENDING
from pymongo.write_concern import WriteConcern

from app.db import API_LOGS, AUDIT_LOGS
from app.repositories.base import Repository, utcnow


class AuditLogRepository(Repository):
    """Security and business events. Queried by time and by event name."""

    collection_name = AUDIT_LOGS

    def insert(self, entry: dict) -> None:
        entry.setdefault("created_at", utcnow())
        self.collection.insert_one(entry)

    def recent(self, *, event: str | None = None, limit: int = 50) -> list[dict]:
        query = {"event": event} if event else {}
        return list(self.collection.find(query).sort("created_at", DESCENDING).limit(limit))


class ApiLogRepository(Repository):
    """Request timing records. A TTL index removes them automatically."""

    collection_name = API_LOGS

    def insert(self, entry: dict) -> None:
        # Unacknowledged write: telemetry must not add a round trip to requests.
        entry.setdefault("created_at", utcnow())
        self.collection.with_options(write_concern=WriteConcern(w=0)).insert_one(entry)

    def latency_by_endpoint(self, *, since: datetime | None = None) -> list[dict]:
        """Request count and latency statistics per endpoint, slowest first."""
        pipeline: list[dict] = []
        if since is not None:
            pipeline.append({"$match": {"created_at": {"$gte": since}}})
        pipeline += [
            {
                "$group": {
                    "_id": {"endpoint": "$endpoint", "method": "$method"},
                    "count": {"$sum": 1},
                    "avg_ms": {"$avg": "$duration_ms"},
                    "max_ms": {"$max": "$duration_ms"},
                    "errors": {"$sum": {"$cond": [{"$gte": ["$status", 500]}, 1, 0]}},
                }
            },
            {"$sort": {"avg_ms": -1}},
        ]
        return list(self.collection.aggregate(pipeline))
