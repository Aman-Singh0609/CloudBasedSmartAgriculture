"""001 - Create the indexes the application's queries rely on.

Each index exists for a specific query (see docs in the repositories):

users
    uniq_email (unique)             login / signup lookup by email; guarantees
                                    one account per address even under races.
predictions
    user_created                    a user's history, newest first, and the
    (user_id, created_at desc)      count used for pagination.
password_reset_tokens
    uniq_token_hash (unique)        look up a presented reset token.
    ttl_expires_at (TTL)            MongoDB deletes expired tokens itself.
    user_id                         replace/remove a user's outstanding token.
audit_logs
    created_desc                    "most recent events" admin queries.
    event_created                   recent events of one type.
api_logs
    ttl_created (TTL, 14 days)      bounds the size of request telemetry.
    endpoint_created                per-endpoint latency reports.
backups
    uniq_backup_id (unique)         look up one backup's metadata.
    created_desc                    list backups, newest first.

Creating an index that already exists with the same definition is a no-op, so
this migration is safe to run repeatedly.
"""

from pymongo import ASCENDING, DESCENDING

VERSION = 1
DESCRIPTION = "Create indexes for users, predictions, reset tokens, logs and backups"
REVERSIBLE = True

API_LOG_TTL_SECONDS = 14 * 24 * 60 * 60

# collection -> list of (keys, options)
INDEXES = {
    "users": [
        ([("email", ASCENDING)], {"name": "uniq_email", "unique": True}),
    ],
    "predictions": [
        ([("user_id", ASCENDING), ("created_at", DESCENDING)], {"name": "user_created"}),
    ],
    "password_reset_tokens": [
        ([("token_hash", ASCENDING)], {"name": "uniq_token_hash", "unique": True}),
        ([("expires_at", ASCENDING)], {"name": "ttl_expires_at", "expireAfterSeconds": 0}),
        ([("user_id", ASCENDING)], {"name": "user_id"}),
    ],
    "audit_logs": [
        ([("created_at", DESCENDING)], {"name": "created_desc"}),
        ([("event", ASCENDING), ("created_at", DESCENDING)], {"name": "event_created"}),
    ],
    "api_logs": [
        ([("created_at", ASCENDING)], {"name": "ttl_created", "expireAfterSeconds": API_LOG_TTL_SECONDS}),
        ([("endpoint", ASCENDING), ("created_at", DESCENDING)], {"name": "endpoint_created"}),
    ],
    "backups": [
        ([("backup_id", ASCENDING)], {"name": "uniq_backup_id", "unique": True}),
        ([("created_at", DESCENDING)], {"name": "created_desc"}),
    ],
}


def _duplicate_email_groups(db) -> int:
    pipeline = [
        {"$group": {"_id": "$email", "count": {"$sum": 1}}},
        {"$match": {"count": {"$gt": 1}}},
        {"$count": "groups"},
    ]
    result = list(db["users"].aggregate(pipeline))
    return result[0]["groups"] if result else 0


def plan(db) -> dict:
    missing = []
    for collection, definitions in INDEXES.items():
        existing = set(db[collection].index_information())
        missing += [f"{collection}.{options['name']}" for _keys, options in definitions if options["name"] not in existing]
    return {
        "affected_documents": 0,
        "indexes_to_create": missing,
        "duplicate_email_groups": _duplicate_email_groups(db),
    }


def up(db) -> dict:
    duplicates = _duplicate_email_groups(db)
    if duplicates:
        raise RuntimeError(
            f"{duplicates} email address(es) are used by more than one account, so the unique "
            "index cannot be created. Resolve the duplicate accounts manually, then run again."
        )
    created = []
    for collection, definitions in INDEXES.items():
        for keys, options in definitions:
            db[collection].create_index(keys, **options)
            created.append(f"{collection}.{options['name']}")
    return {"indexes_ensured": created}


def down(db) -> dict:
    dropped = []
    for collection, definitions in INDEXES.items():
        existing = set(db[collection].index_information())
        for _keys, options in definitions:
            if options["name"] in existing:
                db[collection].drop_index(options["name"])
                dropped.append(f"{collection}.{options['name']}")
    return {"indexes_dropped": dropped}
