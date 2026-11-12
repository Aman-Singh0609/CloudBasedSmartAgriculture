"""002 - Bring legacy user documents into the current shape.

The original application stored emails exactly as typed and kept the password
hash in a field called ``password``. This migration:

* trims and lower-cases ``email`` (the original value is kept in
  ``_legacy.email`` so the change can be reverted);
* renames ``password`` to ``password_hash``;
* adds ``session_version`` (used to invalidate sessions) where missing.

It refuses to run if lower-casing would make two accounts share one email,
because merging accounts is a decision for a person, not a script. Documents
that are already in the current shape are left untouched, so re-running is safe.
"""

from datetime import datetime, timezone

VERSION = 2
DESCRIPTION = "Normalize user emails, rename password -> password_hash, add session_version"
REVERSIBLE = True


def _normalized(email) -> str | None:
    return email.strip().lower() if isinstance(email, str) else None


def _needs_change(doc: dict) -> bool:
    email = doc.get("email")
    return (
        (isinstance(email, str) and email != _normalized(email))
        or ("password" in doc and "password_hash" not in doc)
        or "session_version" not in doc
    )


def _collisions(db) -> int:
    seen: dict[str, int] = {}
    for doc in db["users"].find({}, {"email": 1}):
        normalized = _normalized(doc.get("email"))
        if normalized is not None:
            seen[normalized] = seen.get(normalized, 0) + 1
    return sum(1 for count in seen.values() if count > 1)


def plan(db) -> dict:
    projection = {"email": 1, "password": 1, "password_hash": 1, "session_version": 1}
    affected = sum(1 for doc in db["users"].find({}, projection) if _needs_change(doc))
    return {"affected_documents": affected, "email_collisions_after_normalizing": _collisions(db)}


def up(db) -> dict:
    collisions = _collisions(db)
    if collisions:
        raise RuntimeError(
            f"{collisions} email address(es) would be shared by more than one account after "
            "lower-casing. Resolve those accounts manually, then run again."
        )

    now = datetime.now(timezone.utc)
    emails_normalized = hashes_renamed = versions_added = 0
    for doc in db["users"].find({}):
        if not _needs_change(doc):
            continue
        update_set: dict = {"updated_at": now}
        update: dict = {"$set": update_set}

        email = doc.get("email")
        normalized = _normalized(email)
        if isinstance(email, str) and email != normalized:
            update_set["email"] = normalized
            update_set["_legacy.email"] = email
            emails_normalized += 1
        if "password" in doc and "password_hash" not in doc:
            update["$rename"] = {"password": "password_hash"}
            hashes_renamed += 1
        if "session_version" not in doc:
            update_set["session_version"] = 1
            versions_added += 1
        db["users"].update_one({"_id": doc["_id"]}, update)

    # Key names avoid the words the log redactor treats as sensitive.
    return {
        "emails_normalized": emails_normalized,
        "hash_fields_renamed": hashes_renamed,
        "version_fields_added": versions_added,
    }


def down(db) -> dict:
    emails_restored = hashes_renamed = 0
    for doc in db["users"].find({}):
        update: dict = {}
        legacy_email = (doc.get("_legacy") or {}).get("email")
        if isinstance(legacy_email, str):
            update["$set"] = {"email": legacy_email}
            update["$unset"] = {"_legacy.email": ""}
            emails_restored += 1
        if "password_hash" in doc and "password" not in doc:
            update["$rename"] = {"password_hash": "password"}
            hashes_renamed += 1
        if update:
            db["users"].update_one({"_id": doc["_id"]}, update)
    db["users"].update_many({"_legacy": {}}, {"$unset": {"_legacy": ""}})
    return {"emails_restored": emails_restored, "hash_fields_renamed": hashes_renamed}
