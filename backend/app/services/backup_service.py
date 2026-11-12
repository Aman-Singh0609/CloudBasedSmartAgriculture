"""Database backup and restore.

A backup is a directory on disk::

    <BACKUP_DIR>/<backup_id>/
        manifest.json          what is in the backup, with a SHA-256 per file
        users.jsonl.gz         one MongoDB Extended JSON document per line
        predictions.jsonl.gz
        ...

Extended JSON (canonical mode) preserves BSON types such as ObjectId and
dates exactly. Index definitions are recorded in the manifest and recreated on
restore. Metadata about every backup attempt is stored in the ``backups``
collection of the application database.

Guarantees
----------
* A backup never overwrites another: IDs are unique and the directory is
  created exclusively. Data is written to ``<id>.partial`` and renamed only
  when complete, so a directory without that suffix is always a whole backup.
* Restore verifies every checksum before touching the database.
* Restore refuses to write into non-empty collections unless explicitly told
  to replace them, and then swaps each collection in atomically from a
  temporary copy, so a failure part-way leaves the original data in place.

Backups contain personal data (names, emails, password hashes). Keep the
backup directory access-controlled, out of version control, and encrypted at
rest when stored off the server.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import secrets
import shutil
from datetime import datetime, timezone
from pathlib import Path

from bson import json_util
from pymongo.database import Database as MongoDatabase

from app.db import BACKUP_COLLECTIONS, BACKUPS

logger = logging.getLogger("app.backup")

FORMAT_VERSION = 1
MANIFEST_NAME = "manifest.json"
_PARTIAL_SUFFIX = ".partial"
_TEMP_COLLECTION_SUFFIX = "__restore_tmp"
_INSERT_BATCH_SIZE = 1000
_JSON_OPTIONS = json_util.CANONICAL_JSON_OPTIONS.with_options(tz_aware=True, tzinfo=timezone.utc)
# Index options that are safe and meaningful to recreate.
_INDEX_OPTIONS = ("unique", "sparse", "expireAfterSeconds", "partialFilterExpression")

STATUS_IN_PROGRESS = "in_progress"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"


class BackupError(RuntimeError):
    """A backup or restore could not be completed safely."""


def _now() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(microsecond=(now.microsecond // 1000) * 1000)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _new_backup_id() -> str:
    return f"{_now().strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(3)}"


class BackupService:
    def __init__(self, db: MongoDatabase, backup_dir: str | Path) -> None:
        self._db = db
        self._backup_dir = Path(backup_dir)
        self._metadata = db[BACKUPS]

    # --------------------------------------------------------------- create
    def create_backup(self, collections: tuple[str, ...] = BACKUP_COLLECTIONS, *, note: str = "") -> dict:
        """Export ``collections`` to a new backup directory and record its metadata."""
        backup_id = _new_backup_id()
        final_dir = self._backup_dir / backup_id
        work_dir = self._backup_dir / f"{backup_id}{_PARTIAL_SUFFIX}"
        created_at = _now()

        self._backup_dir.mkdir(parents=True, exist_ok=True)
        if final_dir.exists() or work_dir.exists():
            raise BackupError(f"Backup location for {backup_id} already exists; refusing to overwrite.")

        self._metadata.insert_one(
            {
                "backup_id": backup_id,
                "created_at": created_at,
                "completed_at": None,
                "status": STATUS_IN_PROGRESS,
                "collections": list(collections),
                "record_counts": {},
                "location": str(final_dir.resolve()),
                "checksum": None,
                "size_bytes": None,
                "note": note[:200],
            }
        )

        try:
            work_dir.mkdir(exist_ok=False)
            manifest_collections: dict[str, dict] = {}
            for name in collections:
                manifest_collections[name] = self._export_collection(name, work_dir)

            manifest = {
                "format_version": FORMAT_VERSION,
                "application": "agriculturecloud",
                "backup_id": backup_id,
                "created_at": created_at.isoformat(),
                "database": self._db.name,
                "collections": manifest_collections,
                "total_documents": sum(item["documents"] for item in manifest_collections.values()),
                "note": note[:200],
            }
            manifest_path = work_dir / MANIFEST_NAME
            manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
            checksum = _sha256_file(manifest_path)
            size_bytes = sum(path.stat().st_size for path in work_dir.iterdir() if path.is_file())
            work_dir.rename(final_dir)
        except Exception as exc:
            shutil.rmtree(work_dir, ignore_errors=True)
            self._metadata.update_one(
                {"backup_id": backup_id},
                {"$set": {"status": STATUS_FAILED, "completed_at": _now(), "error_type": type(exc).__name__}},
            )
            logger.error(
                "backup failed",
                extra={"event": "backup_failed", "backup_id": backup_id, "error_type": type(exc).__name__},
            )
            raise BackupError(f"Backup {backup_id} failed: {exc}") from exc

        record_counts = {name: item["documents"] for name, item in manifest_collections.items()}
        self._metadata.update_one(
            {"backup_id": backup_id},
            {
                "$set": {
                    "status": STATUS_COMPLETED,
                    "completed_at": _now(),
                    "record_counts": record_counts,
                    "checksum": checksum,
                    "size_bytes": size_bytes,
                }
            },
        )
        logger.info(
            "backup created",
            extra={"event": "backup_created", "backup_id": backup_id, "record_counts": record_counts},
        )
        return self._metadata.find_one({"backup_id": backup_id}, {"_id": 0})

    def _export_collection(self, name: str, directory: Path) -> dict:
        file_name = f"{name}.jsonl.gz"
        path = directory / file_name
        count = 0
        # mtime=0 keeps the gzip output byte-identical for identical data.
        with open(path, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as archive:
            for document in self._db[name].find({}).sort("_id", 1):
                line = json_util.dumps(document, json_options=_JSON_OPTIONS)
                archive.write(line.encode("utf-8") + b"\n")
                count += 1
        indexes = []
        for index_name, info in self._db[name].index_information().items():
            if index_name == "_id_":
                continue
            entry = {"name": index_name, "key": [[field, direction] for field, direction in info["key"]]}
            entry.update({option: info[option] for option in _INDEX_OPTIONS if option in info})
            indexes.append(entry)
        return {"file": file_name, "documents": count, "sha256": _sha256_file(path), "indexes": indexes}

    # ----------------------------------------------------------------- list
    def list_backups(self, limit: int = 50) -> list[dict]:
        return list(self._metadata.find({}, {"_id": 0}).sort("created_at", -1).limit(limit))

    def resolve_backup_path(self, backup_id: str) -> Path:
        """Locate a backup directory by ID, staying inside the backup directory."""
        if not backup_id or any(sep in backup_id for sep in ("/", "\\", "..")):
            raise BackupError("Invalid backup ID.")
        path = self._backup_dir / backup_id
        if not path.is_dir():
            raise BackupError(f"No backup directory found for ID {backup_id}.")
        return path

    # --------------------------------------------------------------- verify
    def verify_backup(self, path: str | Path) -> dict:
        """Check a backup's structure and checksums. Returns its manifest."""
        directory = Path(path)
        manifest_path = directory / MANIFEST_NAME
        if not manifest_path.is_file():
            raise BackupError("Backup manifest is missing; this is not a complete backup.")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BackupError("Backup manifest cannot be read.") from exc
        if manifest.get("format_version") != FORMAT_VERSION or not isinstance(manifest.get("collections"), dict):
            raise BackupError("Backup manifest has an unsupported format.")

        recorded = self._metadata.find_one({"backup_id": manifest.get("backup_id")})
        if recorded and recorded.get("checksum") and recorded["checksum"] != _sha256_file(manifest_path):
            raise BackupError("Backup manifest does not match the checksum recorded when it was created.")

        for name, item in manifest["collections"].items():
            file_name = item.get("file", "")
            if Path(file_name).name != file_name:
                raise BackupError(f"Backup manifest references an unsafe file name for '{name}'.")
            file_path = directory / file_name
            if not file_path.is_file():
                raise BackupError(f"Backup file for '{name}' is missing.")
            if _sha256_file(file_path) != item.get("sha256"):
                raise BackupError(f"Backup file for '{name}' failed its checksum; the backup is corrupt.")
        return manifest

    # -------------------------------------------------------------- restore
    def restore(self, path: str | Path, *, drop_existing: bool = False) -> dict:
        """Restore a verified backup into this service's database.

        Raises :class:`BackupError` before changing anything if a target
        collection already holds data and ``drop_existing`` is False.
        """
        directory = Path(path)
        manifest = self.verify_backup(directory)
        names = list(manifest["collections"])

        occupied = [name for name in names if self._db[name].estimated_document_count() > 0]
        if occupied and not drop_existing:
            raise BackupError(
                "Refusing to restore: these collections already contain data: "
                + ", ".join(sorted(occupied))
                + ". Restore into an empty database, or pass --drop-existing to replace them."
            )

        restored: dict[str, int] = {}
        for name in names:
            item = manifest["collections"][name]
            temp = self._db[f"{name}{_TEMP_COLLECTION_SUFFIX}"]
            temp.drop()
            try:
                count = self._load_into(temp, directory / item["file"])
                if count != item["documents"]:
                    raise BackupError(
                        f"'{name}' contained {count} documents but the manifest lists {item['documents']}."
                    )
                for index in item.get("indexes", []):
                    options = {option: index[option] for option in _INDEX_OPTIONS if option in index}
                    temp.create_index([(field, direction) for field, direction in index["key"]], name=index["name"], **options)
                if count == 0 and not item.get("indexes"):
                    # Nothing was written, so the temporary collection does not exist to rename.
                    self._db[name].drop()
                else:
                    temp.rename(name, dropTarget=True)
            except Exception:
                temp.drop()
                raise
            restored[name] = count

        logger.info(
            "backup restored",
            extra={"event": "backup_restored", "backup_id": manifest.get("backup_id"), "record_counts": restored},
        )
        return {"backup_id": manifest.get("backup_id"), "database": self._db.name, "record_counts": restored}

    @staticmethod
    def _load_into(collection, file_path: Path) -> int:
        count = 0
        batch: list[dict] = []
        with gzip.open(file_path, "rt", encoding="utf-8") as archive:
            for line in archive:
                if not line.strip():
                    continue
                batch.append(json_util.loads(line, json_options=_JSON_OPTIONS))
                if len(batch) >= _INSERT_BATCH_SIZE:
                    collection.insert_many(batch, ordered=True)
                    count += len(batch)
                    batch = []
        if batch:
            collection.insert_many(batch, ordered=True)
            count += len(batch)
        return count
