"""Database migration command-line interface.

Usage (from the project root)::

    python scripts/migrate.py status
    python scripts/migrate.py up --dry-run            # show what would change
    python scripts/migrate.py up                      # apply pending migrations
    python scripts/migrate.py up --to 2               # apply up to version 2
    python scripts/migrate.py up --allow-irreversible # permit data-destroying migrations
    python scripts/migrate.py down --to 1 --yes       # roll back to version 1
    python scripts/migrate.py unlock                  # clear a stale lock

Exit code 0 means success; 1 means an error; 2 means a migration is waiting
for ``--allow-irreversible``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pymongo.database import Database as MongoDatabase  # noqa: E402

from migrations.runner import MigrationError, MigrationRunner  # noqa: E402
from scripts._common import ScriptError, load_settings, open_database, record_admin_event  # noqa: E402


def _print(data) -> None:
    print(json.dumps(data, indent=2, default=str))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Apply or inspect AgricultureCloud database migrations.")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("status", help="List migrations and whether each has been applied.")

    up = commands.add_parser("up", help="Apply pending migrations.")
    up.add_argument("--to", type=int, default=None, help="Highest version to apply (default: all).")
    up.add_argument("--dry-run", action="store_true", help="Report what would change without changing it.")
    up.add_argument(
        "--allow-irreversible",
        action="store_true",
        help="Permit migrations that permanently delete data. Take a backup first.",
    )

    down = commands.add_parser("down", help="Roll back applied migrations.")
    down.add_argument("--to", type=int, required=True, help="Version to roll back to (0 = revert everything).")
    down.add_argument("--yes", action="store_true", help="Confirm the rollback.")

    commands.add_parser("unlock", help="Remove a stale migration lock left by a crashed run.")
    return parser


def main(argv: list[str] | None = None, db: MongoDatabase | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if db is None:
            db = open_database(load_settings())
        runner = MigrationRunner(db)

        if args.command == "status":
            _print(runner.status())
            return 0

        if args.command == "unlock":
            print("Lock removed." if runner.force_unlock() else "No lock was held.")
            return 0

        if args.command == "up":
            results = runner.upgrade(
                target=args.to, dry_run=args.dry_run, allow_irreversible=args.allow_irreversible
            )
            if not results:
                print("Database is up to date; nothing to apply.")
                return 0
            _print(results)
            if args.dry_run:
                print("Dry run only: no changes were made.")
                return 0
            for result in results:
                if result["status"] == "applied":
                    record_admin_event(
                        db,
                        "migration.applied",
                        details={"version": result["version"], "name": result["name"]},
                    )
            if any(result["status"] == "blocked" for result in results):
                print("A migration is waiting for --allow-irreversible; see 'reason' above.", file=sys.stderr)
                return 2
            return 0

        if args.command == "down":
            if not args.yes:
                print("Rolling back changes data. Re-run with --yes to confirm.", file=sys.stderr)
                return 1
            results = runner.downgrade(target=args.to)
            if not results:
                print("Nothing to roll back.")
                return 0
            _print(results)
            for result in results:
                record_admin_event(
                    db,
                    "migration.rolled_back",
                    details={"version": result["version"], "name": result["name"]},
                )
            return 0
    except (ScriptError, MigrationError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
