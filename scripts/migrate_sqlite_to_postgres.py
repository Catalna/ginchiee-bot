#!/usr/bin/env python3
"""
scripts/migrate_sqlite_to_postgres.py

One-off migration of the old local SQLite database (data/nichie.db) into
PostgreSQL. The source file is opened strictly read-only, so nothing in the
original database is modified.

Usage (from the project root, with DATABASE_URL set):
    python scripts/migrate_sqlite_to_postgres.py --source data/nichie.db
    python scripts/migrate_sqlite_to_postgres.py --source data/nichie.db --dry-run

Safe to re-run: rows whose primary key already exists are skipped. Row ids and
timestamps are preserved, so the chat history keeps its original order.
"""

import argparse
import asyncio
import logging
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("migrate-sqlite")

VALID_TIMEZONES = {"Asia/Jakarta", "Asia/Makassar", "Asia/Jayapura"}

# Table order respects the foreign key graph: parents first.
TABLES = [
    ("users", ["user_id", "name", "username", "timezone", "created_at"]),
    ("diet_profiles", [
        "id", "user_id", "age", "gender", "height_cm", "weight_kg",
        "goal", "activity_level", "updated_at",
    ]),
    ("meal_schedules", ["id", "user_id", "meal_type", "time", "enabled"]),
    ("food_logs", [
        "id", "user_id", "date", "meal_type", "food_name", "amount_g",
        "calories", "protein_g", "carbs_g", "fat_g", "logged_at",
    ]),
    ("conversations", ["id", "user_id", "role", "content", "created_at"]),
    ("user_activities", [
        "id", "user_id", "title", "description", "activity_dt",
        "remind_mins", "reminded", "done", "done_at", "created_at",
    ]),
]


def _table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    """Columns that actually exist in the source table."""
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    except sqlite3.OperationalError:
        return []
    return [row[1] for row in rows]


def _read_sqlite(source: Path) -> dict[str, list[dict]]:
    uri = f"file:{source.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    data: dict[str, list[dict]] = {}

    try:
        for table, columns in TABLES:
            available = _table_columns(conn, table)
            if not available:
                logger.info("Skipping %s: table not present", table)
                continue

            # Only read columns the source actually has; anything new in the
            # Postgres schema (e.g. done/done_at) falls back to the DB default.
            selected = [c for c in columns if c in available]
            if not selected:
                logger.info("Skipping %s: no matching columns", table)
                continue

            missing = [c for c in columns if c not in available]
            if missing:
                logger.info("  %s: %s not in source, using defaults", table, ", ".join(missing))

            try:
                rows = conn.execute(
                    f"SELECT {', '.join(selected)} FROM {table} ORDER BY 1"
                ).fetchall()
            except sqlite3.OperationalError as exc:
                logger.info("Skipping %s: %s", table, exc)
                continue

            data[table] = [
                {column: _clean(column, row[column]) for column in selected}
                for row in rows
            ]
    finally:
        conn.close()

    return data


def _clean(column: str, value):
    """Coerce SQLite values into something Postgres will accept."""
    if value is None:
        return None

    # Boolean columns are stored as 0/1 in SQLite.
    if column in ("enabled", "reminded", "done"):
        return bool(value)

    # asyncpg rejects ISO strings for DATE/TIMESTAMPTZ parameters.
    if column in ("date", "created_at", "updated_at", "logged_at", "activity_dt", "done_at"):
        if column == "date":
            try:
                return date.fromisoformat(str(value)[:10])
            except ValueError:
                return value
        if isinstance(value, (int, float)):
            return datetime.utcfromtimestamp(value)
        text = str(value).strip().replace("T", " ")
        parsed = None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(text[:19], fmt)
                break
            except ValueError:
                continue
        if parsed is not None:
            # SQLite stores UTC; tag it as UTC so Postgres converts correctly.
            from datetime import timezone
            return parsed.replace(tzinfo=timezone.utc)
        return text

    # Guard against unknown timezones that pytz/Postgres would reject.
    if column == "timezone" and str(value) not in VALID_TIMEZONES:
        logger.warning("Unknown timezone %r for user, falling back to Asia/Jakarta", value)
        return "Asia/Jakarta"

    return value


async def _migrate(source: Path, database_url: str, dry_run: bool) -> bool:
    import asyncpg

    # Make sure the target schema (including all pending migrations) exists
    # before copying anything in.
    from database.connection import close_db, init_db
    from database.migrations import run_migrations

    await init_db()
    applied = await run_migrations()
    if applied:
        logger.info("Applied migration(s): %s", ", ".join(applied))

    data = _read_sqlite(source)

    for table, _ in TABLES:
        count = len(data.get(table, []))
        logger.info("  %-18s %d row(s)", table, count)

    if dry_run:
        logger.info("Dry run — nothing written.")
        await close_db()
        return True

    conn = await asyncpg.connect(database_url)

    # Tables with a GENERATED BY DEFAULT identity need explicit id inserts.
    identity_tables = {"diet_profiles", "meal_schedules", "food_logs", "conversations", "user_activities"}

    inserted_total = 0
    try:
        for table, columns in TABLES:
            rows = data.get(table, [])
            if not rows:
                continue

            # Build the column list from the keys actually read for this table,
            # so missing source columns fall back to the Postgres defaults.
            column_names = list(rows[0].keys())
            placeholders = ", ".join(f"${i + 1}" for i in range(len(column_names)))
            column_list = ", ".join(column_names)
            sql = f"INSERT INTO {table} ({column_list}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"

            # One row at a time: volumes are tiny and this keeps the conflict
            # semantics obvious and any failure pinpointable.
            for row in rows:
                values = tuple(row[column] for column in column_names)
                await conn.execute(sql, *values)
                inserted_total += 1

            if table in identity_tables:
                # Keep the identity sequence ahead of the migrated ids.
                await conn.execute(
                    f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                    f"GREATEST((SELECT COALESCE(MAX(id), 0) FROM {table}), 1), true)"
                )
                logger.info("  %s: identity sequence advanced", table)

        logger.info("Migration complete. %d statement(s) executed.", inserted_total)
    finally:
        await conn.close()
        await close_db()

    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate SQLite data to PostgreSQL.")
    parser.add_argument(
        "--source",
        default=str(PROJECT_ROOT / "data" / "nichie.db"),
        help="Path to the SQLite database (opened read-only)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Report row counts without writing"
    )
    args = parser.parse_args()

    source = Path(args.source).resolve()
    if not source.exists():
        logger.error("Source database not found: %s", source)
        return 1

    from config.settings import DATABASE_URL

    logger.info("Source: %s", source)
    logger.info("Target: %s", _mask(DATABASE_URL))

    ok = asyncio.run(_migrate(source, DATABASE_URL, args.dry_run))
    return 0 if ok else 1


def _mask(url: str) -> str:
    """Hide the password in a log line."""
    if "@" not in url or "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    if "@" in rest:
        credentials, host = rest.rsplit("@", 1)
        if ":" in credentials:
            user = credentials.split(":", 1)[0]
            return f"{scheme}://{user}:***@{host}"
    return url


if __name__ == "__main__":
    raise SystemExit(main())



