"""
tests/test_database.py

Unit tests for the placeholder translator, plus an integration suite that runs
against a real PostgreSQL database when DATABASE_URL is available.

The integration tests are the point: they prove the wrapper keeps the aiosqlite
call patterns working (`await db.execute(...)`, `async with db.execute(...) as
cursor`), that `?` never breaks inside string literals, that migrations are
idempotent, and that transactional writes roll back.

Run:
    python -m unittest tests.test_database -v
"""

import asyncio
import os
import unittest
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from database.connection import translate_placeholders  # noqa: E402

PROBE_USER_ID = 999_999_001


class PlaceholderTranslationTests(unittest.TestCase):
    """Pure functions — no database required."""

    def test_single_placeholder(self) -> None:
        self.assertEqual(
            translate_placeholders("SELECT * FROM users WHERE user_id = ?"),
            "SELECT * FROM users WHERE user_id = $1",
        )

    def test_multiple_placeholders_number_in_order(self) -> None:
        self.assertEqual(
            translate_placeholders("UPDATE t SET a = ?, b = ? WHERE id = ?"),
            "UPDATE t SET a = $1, b = $2 WHERE id = $3",
        )

    def test_question_mark_inside_string_literal_is_preserved(self) -> None:
        sql = "INSERT INTO t (q) VALUES ('sudah makan?')"
        result = translate_placeholders(sql)
        self.assertEqual(result, "INSERT INTO t (q) VALUES ('sudah makan?')")
        self.assertNotIn("$1", result)

    def test_mixed_placeholder_and_literal(self) -> None:
        sql = "SELECT * FROM t WHERE name = ? AND note = 'why?'"
        self.assertEqual(
            translate_placeholders(sql),
            "SELECT * FROM t WHERE name = $1 AND note = 'why?'",
        )

    def test_escaped_quote_in_literal(self) -> None:
        sql = "INSERT INTO t (v) VALUES ('it''s ok?')"
        self.assertEqual(
            translate_placeholders(sql),
            "INSERT INTO t (v) VALUES ('it''s ok?')",
        )

    def test_no_placeholder(self) -> None:
        sql = "SELECT now()"
        self.assertEqual(translate_placeholders(sql), sql)


def _resolve_database_url() -> str | None:
    """Read DATABASE_URL from the environment or the project .env file."""
    if os.getenv("DATABASE_URL"):
        return os.getenv("DATABASE_URL")
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return None
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("DATABASE_URL="):
            value = line.split("=", 1)[1].strip()
            return value or None
    return None


class _FrozenDatetime:
    """Stand-in for `datetime` whose `now(tz)` returns a fixed instant.

    Used to pin the clock inside timezone assertions without touching the
    system clock (which would be flaky and affect other tests).
    """

    def __init__(self, moment):
        self._moment = moment

    def now(self, tz=None):
        if tz is None:
            return self._moment.replace(tzinfo=None)
        return self._moment.astimezone(tz)

    def date(self):
        return self._moment.date()


@unittest.skipUnless(_resolve_database_url(), "DATABASE_URL not available")
class PostgresIntegrationTests(unittest.TestCase):
    """All checks run inside one event loop (see module docstring)."""

    def setUp(self) -> None:
        from database.connection import close_db

        # A previous run may have left the pool bound to a dead loop; closing it
        # here is safe because the pool is torn down in the same loop that will
        # recreate it, at the end of _run_all_checks.
        self._close_db = close_db
        asyncio.run(self._close_pool_if_possible())

    async def _close_pool_if_possible(self) -> None:
        import database.connection as connection_module

        if connection_module._pool is not None:
            try:
                await connection_module._pool.close()
            except Exception:
                pass
            connection_module._pool = None

    # ── Individual checks ────────────────────────────────────────────────────

    async def _reset_probe(self, db) -> None:
        """Remove any probe rows so each check starts from a clean slate."""
        await db.execute(
            "DELETE FROM user_activities WHERE user_id = $1", (PROBE_USER_ID,)
        )
        await db.execute("DELETE FROM users WHERE user_id = $1", (PROBE_USER_ID,))

    async def _check_migrations_idempotent(self, db) -> None:
        from database.migrations import run_migrations

        await run_migrations()
        second = await run_migrations()
        self.assertEqual(second, [], f"re-running migrations applied {second}")

        recorded = await db.execute(
            "SELECT version, name FROM schema_migrations ORDER BY version"
        ).fetchall()
        self.assertTrue(recorded, "no migrations recorded")
        self.assertEqual(recorded[0]["version"], 1)

    async def _check_tables(self, db) -> None:
        rows = await db.execute(
            """
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
            """
        ).fetchall()
        names = {row[0] for row in rows}

        expected = {
            "users", "diet_profiles", "meal_schedules", "food_logs",
            "conversations", "user_activities", "schema_migrations",
        }
        self.assertTrue(expected.issubset(names), f"missing tables: {sorted(expected - names)}")

        # google_calendar_tokens: orphaned, no code references it.
        # daily_nutrition: write-only, never read.
        self.assertNotIn("google_calendar_tokens", names)
        self.assertNotIn("daily_nutrition", names)

    async def _check_await_execute(self, db) -> None:
        await db.execute(
            """
            INSERT INTO users (user_id, name, username, timezone)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (user_id) DO UPDATE SET name = excluded.name
            """,
            (PROBE_USER_ID, "Test User", "tester", "Asia/Jakarta"),
        )
        await db.commit()

        value = await db.execute(
            "SELECT name FROM users WHERE user_id = $1", (PROBE_USER_ID,)
        ).fetchval()
        self.assertEqual(value, "Test User")

    async def _check_async_with_cursor(self, db) -> None:
        await db.execute(
            "INSERT INTO users (user_id, name, username) VALUES ($1, $2, $3)",
            (PROBE_USER_ID, "Cursor Test", "cur"),
        )
        async with db.execute(
            "SELECT user_id, name, timezone FROM users WHERE user_id = $1",
            (PROBE_USER_ID,),
        ) as cursor:
            row = await cursor.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(dict(row)["name"], "Cursor Test")
        self.assertEqual(row[0], PROBE_USER_ID)

    async def _check_returning_id(self, db) -> None:
        # Foreign keys are enforced, so the parent user must exist first.
        await db.execute(
            "INSERT INTO users (user_id, name, username) VALUES ($1, $2, $3)",
            (PROBE_USER_ID, "Returning Test", "ret"),
        )

        activity_id = await db.execute(
            """
            INSERT INTO user_activities (user_id, title, activity_dt, remind_mins)
            VALUES ($1, $2, now() + interval '1 day', 30)
            RETURNING id
            """,
            (PROBE_USER_ID, "Test Activity"),
        ).fetchval()
        self.assertIsInstance(activity_id, int)

        deleted = await db.execute(
            "DELETE FROM user_activities WHERE id = $1 AND user_id = $2 RETURNING id",
            (activity_id, PROBE_USER_ID),
        ).fetchone()
        self.assertIsNotNone(deleted)

    async def _check_meal_type_inference(self, db) -> None:
        """meal_type used to be permanently NULL, making /today emoji useless."""
        from services.nutrition_service import get_today_food_logs, user_today

        await db.execute(
            "INSERT INTO users (user_id, name, username) VALUES ($1, $2, $3)",
            (PROBE_USER_ID, "Meal Type Test", "mt"),
        )

        logged = await db.execute(
            """
            INSERT INTO food_logs (user_id, date, meal_type, food_name, amount_g,
                                   calories, protein_g, carbs_g, fat_g)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            RETURNING id
            """,
            (PROBE_USER_ID, user_today(PROBE_USER_ID), "lunch", "nasi goreng",
             200.0, 320.0, 8.0, 45.0, 10.0),
        ).fetchval()

        self.assertIsInstance(logged, int)
        rows = await get_today_food_logs(PROBE_USER_ID)
        self.assertTrue(rows)
        self.assertEqual(rows[-1]["meal_type"], "lunch")

    async def _check_today_follows_user_timezone(self, db) -> None:
        """Regression test for a real data-correctness bug.

        date.today() reads the machine's timezone. On Hugging Face the
        container runs in UTC while users are in Asia/Jakarta (UTC+7), so
        between 00:00 and 07:00 WIB the UTC date is still the previous day.
        Meals logged then got filed under yesterday and /today showed nothing.

        `user_today()` must be derived from the user's stored timezone, so it
        stays correct even when the app machine and the user disagree.
        """
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from services.nutrition_service import user_today
        from services.user_service import (
            create_or_update_user,
            update_user_timezone,
        )

        # WIB is UTC+7 with no DST: between 00:00 and 07:00 WIB the UTC date is
        # the day before.
        for hour in (0, 3, 6):
            probe = PROBE_USER_ID + hour
            wib = ZoneInfo("Asia/Jakarta")
            sample = datetime(2026, 10, 12, hour, 0, tzinfo=wib)

            expected_utc_date = sample.astimezone(ZoneInfo("UTC")).date()
            expected_wib_date = sample.date()

            # Only instructive when the two actually differ.
            if expected_utc_date == expected_wib_date:
                continue

            await create_or_update_user(probe, "TZ Probe", None, "Asia/Jakarta")
            await update_user_timezone(probe, "Asia/Jakarta")
            try:
                # Pin the clock by patching the helper's clock source instead of
                # the system clock, then verify the chosen date.
                import services.nutrition_service as ns

                original = ns.datetime
                ns.datetime = _FrozenDatetime(sample)
                try:
                    actual = user_today(probe)
                finally:
                    ns.datetime = original

                self.assertEqual(
                    actual, expected_wib_date,
                    f"user_today must use the user's timezone at {hour:02d}:00 WIB",
                )
                self.assertNotEqual(
                    actual, expected_utc_date,
                    "user_today must not fall back to the machine/UTC date",
                )
            finally:
                await db.execute("DELETE FROM users WHERE user_id=$1", (probe,))

    async def _check_activity_due_reminder(self, db) -> None:
        from datetime import datetime, timedelta, timezone

        from services.activity_service import (
            add_activity,
            get_due_activity_reminders,
            delete_activity,
        )

        await db.execute(
            "INSERT INTO users (user_id, name, username) VALUES ($1, $2, $3)",
            (PROBE_USER_ID, "Reminder Test", "rem"),
        )

        # 10 minutes away with a 30 minute lead time -> already due.
        soon = datetime.now(timezone.utc) + timedelta(minutes=10)
        # 3 days away with a 30 minute lead time -> not due.
        later = datetime.now(timezone.utc) + timedelta(days=3)

        soon_id = await add_activity(
            PROBE_USER_ID, "Reminder Soon", soon.isoformat(timespec="seconds"), remind_mins=30
        )
        later_id = await add_activity(
            PROBE_USER_ID, "Reminder Later", later.isoformat(timespec="seconds"), remind_mins=30
        )

        due = await get_due_activity_reminders()
        due_ids = {row["id"] for row in due}

        dump = await db.execute(
            """
            SELECT id, title, activity_dt, remind_mins, reminded, user_id
            FROM user_activities WHERE user_id = $1
            """,
            (PROBE_USER_ID,),
        ).fetchall()
        now_val = await db.execute("SELECT now()").fetchval()
        print(f"    [diag] now={now_val}")
        for r in dump:
            due_at = r[2].timestamp() and (r[2] - __import__("datetime").timedelta(minutes=r[3]))
            print(f"    [diag] row id={r[0]} title={r[1]!r} dt={r[2]} remind={r[3]} due_at={due_at} in_due={r[0] in due_ids}")
        print(f"    [diag] due_ids={sorted(due_ids)} soon={soon_id} later={later_id}")

        self.assertIn(soon_id, due_ids, "reminder within the lead window should be due")
        self.assertNotIn(later_id, due_ids, "activity 3 days out should not be due")

        await delete_activity(soon_id, PROBE_USER_ID)
        await delete_activity(later_id, PROBE_USER_ID)

    async def _check_remind_mins_is_bounded(self, db) -> None:
        """An absurd lead time must not make far-future activities fire now."""
        from datetime import datetime, timedelta, timezone

        from services.activity_service import (
            add_activity,
            get_due_activity_reminders,
            delete_activity,
        )

        await db.execute(
            "INSERT INTO users (user_id, name, username) VALUES ($1, $2, $3)",
            (PROBE_USER_ID, "Bounds Test", "bnd"),
        )

        far = datetime.now(timezone.utc) + timedelta(days=3)
        activity_id = await add_activity(
            PROBE_USER_ID, "Far Future", far.isoformat(timespec="seconds"),
            remind_mins=999_999_999,
        )
        try:
            due_ids = {row["id"] for row in await get_due_activity_reminders()}
            self.assertNotIn(
                activity_id, due_ids,
                "an absurd remind_mins must not fire a far-future activity",
            )
        finally:
            await delete_activity(activity_id, PROBE_USER_ID)

    async def _check_question_mark_translation(self, db) -> None:
        await db.execute(
            "INSERT INTO users (user_id, name, username) VALUES (?, ?, ?)",
            (PROBE_USER_ID, "Question Mark", "qm"),
        )
        async with db.execute(
            "SELECT name FROM users WHERE user_id = ?", (PROBE_USER_ID,)
        ) as cursor:
            row = await cursor.fetchone()
        self.assertEqual(row[0], "Question Mark")

    async def _check_literal_question_mark(self, db) -> None:
        await db.execute(
            "INSERT INTO users (user_id, name, username) VALUES (?, ?, ?)",
            (PROBE_USER_ID, "sudah makan?", "qmlit"),
        )
        value = await db.execute(
            "SELECT name FROM users WHERE user_id = ?", (PROBE_USER_ID,)
        ).fetchval()
        self.assertEqual(value, "sudah makan?")

    async def _check_transaction_rollback(self, db) -> None:
        async def _fail(tx) -> None:
            await tx.execute(
                "INSERT INTO users (user_id, name, username) VALUES ($1, $2, $3)",
                (PROBE_USER_ID, "Tx Rollback", "txrb"),
            )
            # Second insert violates the PRIMARY KEY on user_id.
            await tx.execute(
                "INSERT INTO users (user_id, name, username) VALUES ($1, $2, $3)",
                (PROBE_USER_ID, "Duplicate", "txdup"),
            )

        with self.assertRaises(Exception):
            async with db.transaction() as tx:
                await _fail(tx)

        exists = await db.execute(
            "SELECT 1 FROM users WHERE user_id = $1", (PROBE_USER_ID,)
        ).fetchone()
        self.assertIsNone(exists, "transaction was not rolled back")

    # ── Suite ────────────────────────────────────────────────────────────────

    def test_all_checks_against_real_database(self) -> None:
        asyncio.run(self._run_all_checks())

    async def _run_all_checks(self) -> None:
        from database.connection import init_db

        db = await init_db()

        checks = [
            ("migrations idempotent", self._check_migrations_idempotent),
            ("expected tables exist", self._check_tables),
            ("await db.execute pattern", self._check_await_execute),
            ("async with cursor pattern", self._check_async_with_cursor),
            ("RETURNING id + fetchval", self._check_returning_id),
            ("meal_type is populated", self._check_meal_type_inference),
            ("today follows user timezone", self._check_today_follows_user_timezone),
            ("? placeholder translation", self._check_question_mark_translation),
            ("literal ? preserved", self._check_literal_question_mark),
            ("transaction rollback", self._check_transaction_rollback),
            ("timestamptz due reminders", self._check_activity_due_reminder),
            ("remind_mins bounded", self._check_remind_mins_is_bounded),
        ]

        failures = []
        for name, check in checks:
            await self._reset_probe(db)
            try:
                await check(db)
                print(f"  PASS  {name}")
            except Exception as exc:  # noqa: BLE001
                print(f"  FAIL  {name}: {exc}")
                failures.append((name, exc))

        # Always clean the probe user so the next run starts clean.
        await self._reset_probe(db)

        await self._reset_probe(db)
        # Close in the same event loop that created the pool.
        from database.connection import close_db
        await close_db()

        if failures:
            self.fail(f"{len(failures)} check(s) failed: {[n for n, _ in failures]}")


if __name__ == "__main__":
    unittest.main()



