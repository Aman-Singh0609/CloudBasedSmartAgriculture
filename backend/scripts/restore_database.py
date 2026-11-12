"""Restore the application database from a backup.

Usage (from the project root)::

    python scripts/restore_database.py --backup-id <id> --verify-only
    python scripts/restore_database.py --backup-id <id> --confirm <database-name>
    python scripts/restore_database.py --backup-id <id> --confirm <database-name> --drop-existing
    python scripts/restore_database.py --path /some/backup/dir --confirm <database-name>

Safety:

* The backup's checksums are verified before anything is changed.
* You must confirm by giving the exact name of the database being restored
  into (``--confirm``, or by typing it when prompted).
* Collections that already hold data are not replaced unless you pass
  ``--drop-existing``. In that case a safety backup of the current data is
  taken first (skip with ``--no-safety-backup``).
* Every attempt is written to the audit log.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pymongo.database import Database as MongoDatabase  # noqa: E402

from app.services.backup_service import BackupError, BackupService  # noqa: E402
from scripts._common import ScriptError, load_settings, open_database, record_admin_event  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Restore the AgricultureCloud database from a backup.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--backup-id", help="ID of a backup inside BACKUP_DIR.")
    source.add_argument("--path", help="Path to a backup directory.")
    parser.add_argument("--confirm", default=None, help="Name of the database to restore into (required to proceed).")
    parser.add_argument("--drop-existing", action="store_true", help="Replace collections that already contain data.")
    parser.add_argument("--no-safety-backup", action="store_true", help="Skip the automatic pre-restore backup.")
    parser.add_argument("--verify-only", action="store_true", help="Check the backup's integrity and exit.")
    parser.add_argument("--backup-dir", default=None, help="Override BACKUP_DIR for this run.")
    return parser


def _confirmed(database_name: str, supplied: str | None) -> bool:
    if supplied is not None:
        return supplied == database_name
    if not sys.stdin.isatty():
        return False
    answer = input(f"Type the database name ({database_name}) to confirm the restore: ")
    return answer.strip() == database_name


def main(argv: list[str] | None = None, db: MongoDatabase | None = None, backup_dir: str | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if db is None:
            config = load_settings()
            db = open_database(config)
            backup_dir = backup_dir or config["BACKUP_DIR"]
        service = BackupService(db, args.backup_dir or backup_dir or "backups")

        try:
            path = Path(args.path) if args.path else service.resolve_backup_path(args.backup_id)
            manifest = service.verify_backup(path)
        except BackupError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1

        summary = {
            "backup_id": manifest.get("backup_id"),
            "created_at": manifest.get("created_at"),
            "source_database": manifest.get("database"),
            "target_database": db.name,
            "record_counts": {name: item["documents"] for name, item in manifest["collections"].items()},
        }
        print("Backup verified: all checksums match.")
        print(json.dumps(summary, indent=2))
        if args.verify_only:
            return 0

        if not _confirmed(db.name, args.confirm):
            print(
                f"Restore not confirmed. Re-run with --confirm {db.name} to restore into that database.",
                file=sys.stderr,
            )
            return 1

        record_admin_event(
            db,
            "restore.attempted",
            details={"backup_id": summary["backup_id"], "drop_existing": args.drop_existing},
        )
        try:
            if args.drop_existing and not args.no_safety_backup:
                safety = service.create_backup(note=f"automatic safety backup before restoring {summary['backup_id']}")
                print(f"Safety backup of current data created: {safety['backup_id']}")
            result = service.restore(path, drop_existing=args.drop_existing)
        except BackupError as exc:
            record_admin_event(
                db, "restore.failed", outcome="failure", details={"backup_id": summary["backup_id"]}
            )
            print(f"Error: {exc}", file=sys.stderr)
            return 1

        record_admin_event(db, "restore.completed", details=result)
        print(json.dumps(result, indent=2))
        print("Restore completed.")
        return 0
    except ScriptError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
