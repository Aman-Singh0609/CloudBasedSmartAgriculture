"""Migration runner.

A migration is a module in this package named ``NNN_description.py`` that
defines::

    VERSION = 3                      # must match the NNN prefix
    DESCRIPTION = "What it does"
    REVERSIBLE = True                # False when down() cannot restore the data

    def plan(db) -> dict:            # read-only; {"affected_documents": n, ...}
    def up(db) -> dict:              # apply; returns a summary of what changed
    def down(db) -> dict:            # revert (required when REVERSIBLE is True)

Rules enforced here:

* Migrations run in version order, one at a time, never automatically skipped.
* Every run is recorded in the ``migration_history`` collection (status,
  duration, summary, and an event list), and written to the log.
* A lock document prevents two processes from migrating at the same time.
* ``dry_run`` only calls ``plan`` and changes nothing.
* A migration that destroys data (``REVERSIBLE = False``) and would affect at
  least one document is not applied unless the operator passes
  ``allow_irreversible=True``. Data is never removed silently.
"""

from __future__ import annotations

import importlib
import logging
import os
import re
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from pymongo import ASCENDING
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

logger = logging.getLogger("app.migrations")

HISTORY_COLLECTION = "migration_history"
_LOCK_ID = "lock"
_LOCK_VERSION = 0  # reserved; real migrations start at 1
_FILENAME_RE = re.compile(r"^(\d{3})_([a-z0-9_]+)\.py$")

STATUS_APPLIED = "applied"
STATUS_FAILED = "failed"
STATUS_ROLLED_BACK = "rolled_back"
STATUS_PENDING = "pending"


class MigrationError(RuntimeError):
    """A migration could not be applied or the request was not allowed."""


class MigrationLockedError(MigrationError):
    """Another migration run holds the lock."""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    description: str
    reversible: bool
    plan: Callable[[Database], dict]
    up: Callable[[Database], dict]
    down: Callable[[Database], dict] | None


def _now() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(microsecond=(now.microsecond // 1000) * 1000)


def discover(directory: Path | None = None, package: str = "migrations") -> list[Migration]:
    """Load every migration module, ordered by version, and validate it."""
    directory = directory or Path(__file__).resolve().parent
    found: list[Migration] = []
    for path in sorted(directory.iterdir()):
        match = _FILENAME_RE.match(path.name)
        if not match:
            continue
        module = importlib.import_module(f"{package}.{path.stem}")
        version = int(match.group(1))
        if getattr(module, "VERSION", None) != version:
            raise MigrationError(f"{path.name}: VERSION must equal the file prefix {version}.")
        for attribute in ("DESCRIPTION", "REVERSIBLE", "plan", "up"):
            if not hasattr(module, attribute):
                raise MigrationError(f"{path.name}: missing required attribute '{attribute}'.")
        down = getattr(module, "down", None)
        if module.REVERSIBLE and down is None:
            raise MigrationError(f"{path.name}: REVERSIBLE migrations must define down().")
        found.append(
            Migration(
                version=version,
                name=path.stem,
                description=module.DESCRIPTION,
                reversible=bool(module.REVERSIBLE),
                plan=module.plan,
                up=module.up,
                down=down,
            )
        )
    versions = [migration.version for migration in found]
    if len(versions) != len(set(versions)):
        raise MigrationError("Two migrations share the same version number.")
    if versions != list(range(1, len(versions) + 1)):
        raise MigrationError("Migration versions must be consecutive, starting at 001.")
    return found


class MigrationRunner:
    def __init__(self, db: Database, migrations: list[Migration] | None = None) -> None:
        self._db = db
        self._history = db[HISTORY_COLLECTION]
        self._migrations = migrations if migrations is not None else discover()

    # ----------------------------------------------------------- inspection
    def _ensure_history_index(self) -> None:
        self._history.create_index([("version", ASCENDING)], name="uniq_version", unique=True)

    def applied_versions(self) -> set[int]:
        cursor = self._history.find(
            {"version": {"$gt": _LOCK_VERSION}, "status": STATUS_APPLIED}, {"version": 1}
        )
        return {doc["version"] for doc in cursor}

    def pending(self) -> list[Migration]:
        applied = self.applied_versions()
        return [migration for migration in self._migrations if migration.version not in applied]

    def status(self) -> list[dict]:
        records = {
            doc["version"]: doc for doc in self._history.find({"version": {"$gt": _LOCK_VERSION}})
        }
        rows = []
        for migration in self._migrations:
            record = records.get(migration.version, {})
            rows.append(
                {
                    "version": migration.version,
                    "name": migration.name,
                    "description": migration.description,
                    "reversible": migration.reversible,
                    "status": record.get("status", STATUS_PENDING),
                    "applied_at": record.get("applied_at"),
                    "duration_ms": record.get("duration_ms"),
                }
            )
        return rows

    # ----------------------------------------------------------------- lock
    def _acquire_lock(self) -> None:
        self._ensure_history_index()
        try:
            self._history.insert_one(
                {
                    "_id": _LOCK_ID,
                    "version": _LOCK_VERSION,
                    "locked_at": _now(),
                    "host": socket.gethostname(),
                    "pid": os.getpid(),
                }
            )
        except DuplicateKeyError as exc:
            holder = self._history.find_one({"_id": _LOCK_ID}) or {}
            raise MigrationLockedError(
                "Another migration run is in progress "
                f"(started {holder.get('locked_at')}, host {holder.get('host')}, pid {holder.get('pid')}). "
                "If that process has died, clear the lock with: python scripts/migrate.py unlock"
            ) from exc

    def _release_lock(self) -> None:
        self._history.delete_one({"_id": _LOCK_ID})

    def force_unlock(self) -> bool:
        return self._history.delete_one({"_id": _LOCK_ID}).deleted_count == 1

    # -------------------------------------------------------------- upgrade
    def upgrade(
        self,
        *,
        target: int | None = None,
        dry_run: bool = False,
        allow_irreversible: bool = False,
    ) -> list[dict]:
        """Apply pending migrations up to ``target`` (inclusive). Returns one result per migration."""
        todo = [m for m in self.pending() if target is None or m.version <= target]
        if dry_run:
            return [self._describe(migration) for migration in todo]
        if not todo:
            return []

        results: list[dict] = []
        self._acquire_lock()
        try:
            for migration in todo:
                plan = migration.plan(self._db)
                affected = int(plan.get("affected_documents", 0))
                if not migration.reversible and affected > 0 and not allow_irreversible:
                    results.append(
                        {
                            "version": migration.version,
                            "name": migration.name,
                            "status": "blocked",
                            "plan": plan,
                            "reason": (
                                f"{migration.name} permanently changes {affected} document(s) and cannot be "
                                "undone. Take a backup, then re-run with --allow-irreversible."
                            ),
                        }
                    )
                    logger.warning(
                        "irreversible migration needs confirmation",
                        extra={"event": "migration_blocked", "version": migration.version, "affected": affected},
                    )
                    break
                results.append(self._run(migration, direction="up"))
        finally:
            self._release_lock()
        return results

    # ------------------------------------------------------------ downgrade
    def downgrade(self, *, target: int) -> list[dict]:
        """Revert applied migrations with a version greater than ``target``, newest first."""
        applied = self.applied_versions()
        todo = [m for m in reversed(self._migrations) if m.version in applied and m.version > target]
        for migration in todo:
            if not migration.reversible or migration.down is None:
                raise MigrationError(
                    f"{migration.name} is irreversible and cannot be rolled back. "
                    "Restore from a backup taken before it was applied instead."
                )
        if not todo:
            return []

        results: list[dict] = []
        self._acquire_lock()
        try:
            for migration in todo:
                results.append(self._run(migration, direction="down"))
        finally:
            self._release_lock()
        return results

    # ------------------------------------------------------------ internals
    def _describe(self, migration: Migration) -> dict:
        return {
            "version": migration.version,
            "name": migration.name,
            "description": migration.description,
            "reversible": migration.reversible,
            "status": "would_apply",
            "plan": migration.plan(self._db),
        }

    def _run(self, migration: Migration, *, direction: str) -> dict:
        operation = migration.up if direction == "up" else migration.down
        success_status = STATUS_APPLIED if direction == "up" else STATUS_ROLLED_BACK
        started_at = _now()
        timer = time.perf_counter()
        logger.info(
            "migration starting",
            extra={"event": "migration_started", "version": migration.version, "direction": direction},
        )
        try:
            summary = operation(self._db) or {}
        except Exception as exc:
            duration_ms = round((time.perf_counter() - timer) * 1000, 2)
            self._record(migration, direction, STATUS_FAILED, started_at, duration_ms, {}, error=str(exc)[:500])
            logger.error(
                "migration failed: %s",
                exc,
                extra={"event": "migration_failed", "version": migration.version, "direction": direction},
            )
            raise MigrationError(f"{migration.name} ({direction}) failed: {exc}") from exc

        duration_ms = round((time.perf_counter() - timer) * 1000, 2)
        self._record(migration, direction, success_status, started_at, duration_ms, summary)
        logger.info(
            "migration finished",
            extra={
                "event": "migration_finished",
                "version": migration.version,
                "direction": direction,
                "duration_ms": duration_ms,
                "summary": summary,
            },
        )
        return {
            "version": migration.version,
            "name": migration.name,
            "status": success_status,
            "duration_ms": duration_ms,
            "summary": summary,
        }

    def _record(
        self,
        migration: Migration,
        direction: str,
        status: str,
        started_at: datetime,
        duration_ms: float,
        summary: dict,
        *,
        error: str | None = None,
    ) -> None:
        finished_at = _now()
        event = {
            "direction": direction,
            "status": status,
            "started_at": started_at,
            "finished_at": finished_at,
            "duration_ms": duration_ms,
            "summary": summary,
        }
        if error:
            event["error"] = error
        self._history.update_one(
            {"version": migration.version},
            {
                "$set": {
                    "name": migration.name,
                    "description": migration.description,
                    "reversible": migration.reversible,
                    "status": status,
                    "applied_at": finished_at if status == STATUS_APPLIED else None,
                    "duration_ms": duration_ms,
                    "summary": summary,
                    "updated_at": finished_at,
                },
                "$push": {"events": event},
            },
            upsert=True,
        )
