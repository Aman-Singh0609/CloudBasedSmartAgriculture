"""Report request counts and latency per endpoint from the ``api_logs`` collection.

Usage (from the project root)::

    python scripts/api_stats.py              # last 24 hours
    python scripts/api_stats.py --hours 1

Useful for spotting slow endpoints. Records expire automatically after 14 days.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pymongo.database import Database as MongoDatabase  # noqa: E402

from app.repositories.log_repository import ApiLogRepository  # noqa: E402
from scripts._common import ScriptError, load_settings, open_database  # noqa: E402


def main(argv: list[str] | None = None, db: MongoDatabase | None = None) -> int:
    parser = argparse.ArgumentParser(description="Show API latency per endpoint.")
    parser.add_argument("--hours", type=float, default=24.0, help="Look back this many hours (default 24).")
    args = parser.parse_args(argv)
    try:
        if db is None:
            db = open_database(load_settings())
    except ScriptError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    since = datetime.now(timezone.utc) - timedelta(hours=args.hours)
    rows = ApiLogRepository(lambda: db).latency_by_endpoint(since=since)
    if not rows:
        print("No requests recorded in that period.")
        return 0

    print(f"{'METHOD':7} {'ENDPOINT':34} {'COUNT':>7} {'AVG ms':>9} {'MAX ms':>9} {'5xx':>5}")
    for row in rows:
        key = row["_id"]
        print(
            f"{str(key.get('method')):7} {str(key.get('endpoint')):34} {row['count']:>7} "
            f"{(row['avg_ms'] or 0):>9.1f} {(row['max_ms'] or 0):>9.1f} {row['errors']:>5}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
