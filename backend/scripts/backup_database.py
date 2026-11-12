"""Create a backup of the application database.

Usage (from the project root)::

    python scripts/backup_database.py                       # back up application data
    python scripts/backup_database.py --note "before v2.1"  # attach a note
    python scripts/backup_database.py --include-api-logs    # also export request telemetry
    python scripts/backup_database.py --list                # list previous backups

Backups are written to BACKUP_DIR (default ``./backups``) and never overwrite
an earlier backup. Each one is recorded in the ``backups`` collection. The
files contain personal data: keep the directory private and out of Git.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pymongo.database import Database as MongoDatabase  # noqa: E402

from app.db import API_LOGS, BACKUP_COLLECTIONS  # noqa: E402
from app.services.backup_service import BackupError, BackupService  # noqa: E402
from scripts._common import ScriptError, load_settings, open_database, record_admin_event  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Back up the AgricultureCloud database.")
    parser.add_argument("--note", default="", help="Short description stored with the backup.")
    parser.add_argument("--include-api-logs", action="store_true", help="Also back up request telemetry.")
    parser.add_argument("--list", action="store_true", help="List recorded backups and exit.")
    parser.add_argument("--backup-dir", default=None, help="Override BACKUP_DIR for this run.")
    return parser


def main(argv: list[str] | None = None, db: MongoDatabase | None = None, backup_dir: str | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if db is None:
            config = load_settings()
            db = open_database(config)
            backup_dir = backup_dir or config["BACKUP_DIR"]
        service = BackupService(db, args.backup_dir or backup_dir or "backups")

        if args.list:
            print(json.dumps(service.list_backups(), indent=2, default=str))
            return 0

        collections = BACKUP_COLLECTIONS + ((API_LOGS,) if args.include_api_logs else ())
        try:
            metadata = service.create_backup(collections, note=args.note)
        except BackupError as exc:
            record_admin_event(db, "backup.failed", outcome="failure", details={"reason": type(exc).__name__})
            print(f"Error: {exc}", file=sys.stderr)
            return 1

        record_admin_event(
            db,
            "backup.created",
            details={"backup_id": metadata["backup_id"], "record_counts": metadata["record_counts"]},
        )
        print(json.dumps(metadata, indent=2, default=str))
        print(f"\nBackup {metadata['backup_id']} completed: {metadata['location']}")
        return 0
    except ScriptError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
