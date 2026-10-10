"""
services/user_service.py
CRUD operations for users, diet profiles, and meal schedules.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from database.connection import get_db

logger = logging.getLogger(__name__)


# ── User ──────────────────────────────────────────────────────────────────────

async def create_or_update_user(
    user_id: int,
    name: str,
    username: Optional[str] = None,
    timezone: str = "Asia/Jakarta",
) -> None:
    db = await get_db()
    await db.execute(
        """
        INSERT INTO users (user_id, name, username, timezone)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT(user_id) DO UPDATE SET
            name = excluded.name,
            username = excluded.username,
            timezone = excluded.timezone
        """,
        (user_id, name, username, timezone),
    )


async def update_user_name(user_id: int, name: str) -> Optional[dict]:
    """Change a user's display name (used by the 'ganti nama panggilan' flow).

    Only touches `name`; timezone and username are preserved.
    """
    clean = (name or "").strip()
    if not clean:
        return None

    db = await get_db()
    row = await db.execute(
        """
        UPDATE users
        SET name = $1
        WHERE user_id = $2
        RETURNING user_id, name, username, timezone, created_at
        """,
        (clean, user_id),
    ).fetchone()

    if row:
        logger.info("User %s renamed to %r", user_id, clean)
        return dict(row)
    return None


async def update_user_timezone(user_id: int, timezone_name: str) -> Optional[dict]:
    db = await get_db()
    row = await db.execute(
        """
        UPDATE users
        SET timezone = $1
        WHERE user_id = $2
        RETURNING user_id, name, username, timezone, created_at
        """,
        (timezone_name, user_id),
    ).fetchone()
    return dict(row) if row else None


async def get_user(user_id: int) -> Optional[dict]:
    db = await get_db()
    row = await db.execute(
        "SELECT user_id, name, username, timezone, created_at FROM users WHERE user_id = $1",
        (user_id,),
    ).fetchone()
    return dict(row) if row else None


async def user_exists(user_id: int) -> bool:
    return (await get_user(user_id)) is not None


# ── Diet Profile ──────────────────────────────────────────────────────────────

async def create_or_update_diet_profile(
    user_id: int,
    age: int,
    gender: str,
    height_cm: float,
    weight_kg: float,
    goal: str,
    activity_level: str,
) -> None:
    db = await get_db()
    # Must be a datetime, not an ISO string: asyncpg refuses str for a
    # TIMESTAMPTZ parameter, and `updated_at` is used to detect profile changes.
    now = datetime.now(timezone.utc)
    await db.execute(
        """
        INSERT INTO diet_profiles
            (user_id, age, gender, height_cm, weight_kg, goal, activity_level, updated_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        ON CONFLICT(user_id) DO UPDATE SET
            age            = excluded.age,
            gender         = excluded.gender,
            height_cm      = excluded.height_cm,
            weight_kg      = excluded.weight_kg,
            goal           = excluded.goal,
            activity_level = excluded.activity_level,
            updated_at     = excluded.updated_at
        """,
        (user_id, age, gender, height_cm, weight_kg, goal, activity_level, now),
    )


async def get_diet_profile(user_id: int) -> Optional[dict]:
    db = await get_db()
    row = await db.execute(
        """
        SELECT id, user_id, age, gender, height_cm, weight_kg, goal, activity_level, updated_at
        FROM diet_profiles
        WHERE user_id = $1
        """,
        (user_id,),
    ).fetchone()
    return dict(row) if row else None


async def has_diet_profile(user_id: int) -> bool:
    return (await get_diet_profile(user_id)) is not None


# ── Meal Schedule ─────────────────────────────────────────────────────────────

VALID_MEALS = ("breakfast", "lunch", "snack", "dinner")


async def set_meal_schedule(
    user_id: int, meal_type: str, time: str, enabled: bool = True
) -> None:
    """Set or update a meal schedule entry. time format: HH:MM"""
    db = await get_db()
    await db.execute(
        """
        INSERT INTO meal_schedules (user_id, meal_type, time, enabled)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT(user_id, meal_type) DO UPDATE SET
            time    = excluded.time,
            enabled = excluded.enabled
        """,
        (user_id, meal_type, time, enabled),
    )


async def get_meal_schedules(user_id: int) -> list[dict]:
    db = await get_db()
    rows = await db.execute(
        "SELECT * FROM meal_schedules WHERE user_id = $1 ORDER BY time",
        (user_id,),
    ).fetchall()
    return [dict(r) for r in rows]


async def get_all_active_schedules() -> list[dict]:
    """Return all enabled schedules (all users) — used by scheduler."""
    db = await get_db()
    rows = await db.execute(
        """
        SELECT ms.*, u.timezone, u.name
        FROM meal_schedules ms
        JOIN users u ON ms.user_id = u.user_id
        WHERE ms.enabled = TRUE
        """
    ).fetchall()
    return [dict(r) for r in rows]


# ── Conversation History ──────────────────────────────────────────────────────

async def save_conversation(user_id: int, role: str, content: str) -> None:
    db = await get_db()
    await db.execute(
        "INSERT INTO conversations (user_id, role, content) VALUES ($1, $2, $3)",
        (user_id, role, content),
    )


async def get_recent_conversations(user_id: int, limit: int = 10) -> list[dict]:
    """Recent conversation entries, oldest first.

    Ordered by `id` rather than `created_at` because created_at has second
    precision and rapid successive messages can share a timestamp, which made
    the previous ordering non-deterministic.
    """
    db = await get_db()
    rows = await db.execute(
        """
        SELECT id, role, content, created_at FROM conversations
        WHERE user_id = $1
        ORDER BY id DESC
        LIMIT $2
        """,
        (user_id, int(limit)),
    ).fetchall()
    return [dict(r) for r in reversed(rows)]


async def prune_conversations(user_id: int, keep: int = 100) -> int:
    """Trim old conversation history so the table cannot grow without bound."""
    db = await get_db()
    deleted = await db.execute(
        """
        DELETE FROM conversations
        WHERE user_id = $1
          AND id < (
              SELECT id FROM conversations
              WHERE user_id = $1
              ORDER BY id DESC
              OFFSET $2
              LIMIT 1
          )
        """,
        (user_id, int(keep)),
    ).fetchval()
    return deleted or 0
