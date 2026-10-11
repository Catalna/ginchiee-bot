#!/usr/bin/env python3
"""
scripts/copy_postgres.py

Copy rows between two PostgreSQL databases without destroying either side.

Written for a specific, real situation: the bot's data originally lived in
SQLite, was migrated into a local Postgres, and later the live service moved to
Supabase. Supabase had the schema but never received the history, while real
users had already started using Supabase and generated fresh rows.

Safety rules this script follows:

1. **Additive only.** Every insert is `ON CONFLICT DO NOTHING`. Nothing is
   updated or deleted, so a second run is inert.
2. **Primary keys are offset** by a fixed amount. Without this, rows whose ids
   overlap the target (local food_logs 1..26 vs Supabase 21..22) would be
   silently skipped by ON CONFLICT, losing data.
3. **Users are never overwritten.** A user that already exists on the target
   keeps whatever name and profile they set there, because that is the newer
   information from the person themselves.
4. **Diet profiles are only copied for users that have none**, so a profile
   someone just filled in is not replaced by stale numbers.
5. Everything runs in one transaction; a failure rolls back cleanly.

Usage:
    python scripts/copy_postgres.py --from "postgresql://..." --to "postgresql://..."
    python scripts/copy_postgres.py --from "$LOCAL" --to "$REMOTE" --dry-run

`--to` defaults to DATABASE_URL from the environment.
"""

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

# Load .env so `--to` can default to DATABASE_URL without importing the app
# config (which validates Telegram/Gemini credentials this script does not need).
try:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("copy-postgres")

#: How far above the target's current ids to place copied rows.
ID_OFFSET = 100000

#: Tables copied in foreign-key order. `key_columns` are the columns guarded by
#: a constraint that ON CONFLICT must target.
TABLES: list[tuple[str, tuple[str, ...]]] = [
    ("meal_schedules", ("user_id", "meal_type")),
    ("food_logs", ("id",)),
    ("conversations", ("id",)),
    ("user_activities", ("id",)),
    ("diet_profiles", ("id",)),
]

USER_COLUMNS = ("user_id", "name", "username", "timezone", "created_at")
COLUMNS_BY_TABLE = {
    "meal_schedules": ("id", "user_id", "meal_type", "time", "enabled"),
    "food_logs": ("id", "user_id", "date", "meal_type", "food_name", "amount_g",
                  "calories", "protein_g", "carbs_g", "fat_g", "logged_at"),
    "conversations": ("id", "user_id", "role", "content", "created_at"),
    "user_activities": ("id", "user_id", "title", "description", "activity_dt",
                        "remind_mins", "reminded", "done", "done_at", "created_at"),
    "diet_profiles": ("id", "user_id", "age", "gender", "height_cm", "weight_kg",
                      "goal", "activity_level", "updated_at"),
}


def _mask(url: str) -> str:
    if "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    if "@" in rest:
        creds, host = rest.rsplit("@", 1)
        if ":" in creds:
            user = creds.split(":", 1)[0]
            return f"{scheme}://{user}:***@{host}"
    return url


async def copy(src_dsn: str, dst_dsn: str, dry_run: bool) -> int:
    import asyncpg

    src = await asyncpg.connect(src_dsn)
    dst = await asyncpg.connect(dst_dsn)

    copied = 0
    try:
        dst_users = {
            row["user_id"] for row in await dst.fetch("SELECT user_id FROM users")
        }
        logger.info("Target already has %d user(s): %s", len(dst_users), sorted(dst_users))

        # ── users: only the ones absent from the target ─────────────────────
        src_users = await src.fetch("SELECT * FROM users")
        missing = [u for u in src_users if u["user_id"] not in dst_users]

        logger.info("users: %d on source, %d missing on target", len(src_users), len(missing))
        for row in missing:
            logger.info("  would copy user %s (%r)", row["user_id"], row["name"])
            if dry_run:
                continue
            # asyncpg's execute() takes *args, not a positional tuple.
            await dst.execute(
                """
                INSERT INTO users (user_id, name, username, timezone, created_at)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (user_id) DO NOTHING
                """,
                row["user_id"], row["name"], row["username"], row["timezone"],
                row["created_at"],
            )
            copied += 1

        # ── diet profiles: only for users without one ───────────────────────
        profiles = await src.fetch("SELECT * FROM diet_profiles")
        keep_profiles = []
        for p in profiles:
            existing = await dst.fetchval(
                "SELECT count(*) FROM diet_profiles WHERE user_id = $1", p["user_id"]
            )
            if existing:
                logger.info(
                    "  skip profile for user %s: target already has one (newer data)",
                    p["user_id"],
                )
            else:
                keep_profiles.append(p)
        logger.info("diet_profiles: %d on source, %d to copy", len(profiles), len(keep_profiles))

        # ── remaining tables, id-offset guarded ─────────────────────────────
        for table, conflict_cols in TABLES:
            if table == "diet_profiles":
                rows = keep_profiles
            else:
                rows = await src.fetch(f"SELECT * FROM {table}")

            if not rows:
                logger.info("%s: nothing to copy", table)
                continue

            columns = COLUMNS_BY_TABLE[table]
            conflict = ", ".join(conflict_cols)
            placeholders = ", ".join(f"${i + 1}" for i in range(len(columns)))
            sql = (
                f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) "
                f"ON CONFLICT ({conflict}) DO NOTHING"
            )

            values = []
            for row in rows:
                record = []
                for column in columns:
                    value = row[column]
                    if column == "id":
                        value = value + ID_OFFSET
                    record.append(value)
                values.append(tuple(record))

            logger.info("%s: %d row(s) to copy (id +%d)", table, len(values), ID_OFFSET)
            if dry_run:
                continue

            await dst.executemany(sql, values)
            copied += len(values)

        if dry_run:
            logger.info("Dry run — nothing written.")
            return 0

        # Push identity sequences past the offset range so new inserts do not
        # collide with the copied rows.
        for table in ("food_logs", "conversations", "user_activities",
                      "meal_schedules", "diet_profiles"):
            await dst.execute(
                f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                f"GREATEST((SELECT COALESCE(MAX(id), 0) FROM {table}), 1), true)"
            )
        logger.info("Identity sequences advanced.")

    finally:
        await src.close()
        await dst.close()

    return copied


async def report(dst_dsn: str) -> None:
    import asyncpg

    conn = await asyncpg.connect(dst_dsn)
    try:
        logger.info("--- target after copy ---")
        for table in ("users", "diet_profiles", "meal_schedules", "food_logs",
                      "conversations", "user_activities"):
            count = await conn.fetchval(f"SELECT count(*) FROM {table}")
            logger.info("   %-18s %d", table, count)

        logger.info("--- recent conversation order (newest first) ---")
        rows = await conn.fetch(
            "SELECT id, user_id, role, created_at FROM conversations "
            "ORDER BY id DESC LIMIT 6"
        )
        for r in rows:
            logger.info("   id=%s user=%s %-5s %s", r["id"], r["user_id"], r["role"],
                        r["created_at"].strftime("%Y-%m-%d %H:%M"))
    finally:
        await conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Copy rows between PostgreSQL databases.")
    parser.add_argument("--from", dest="src", required=True,
                        help="Source connection string")
    parser.add_argument("--to", dest="dst",
                        default=os.environ.get("DATABASE_URL"),
                        help="Target connection string (default: DATABASE_URL)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.dst:
        logger.error("No target given: pass --to or set DATABASE_URL.")
        return 1

    if args.src == args.dst:
        logger.error("Source and target are the same database. Nothing to do.")
        return 1

    logger.info("Source: %s", _mask(args.src))
    logger.info("Target: %s", _mask(args.dst))
    logger.info("")

    asyncio.run(copy(args.src, args.dst, args.dry_run))
    if not args.dry_run:
        asyncio.run(report(args.dst))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
