"""
services/user_service.py
CRUD operations for users, diet profiles, and meal schedules.
"""

import logging
from datetime import datetime
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
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            name = excluded.name,
            username = excluded.username
        """,
        (user_id, name, username, timezone),
    )
    await db.commit()


async def get_user(user_id: int) -> Optional[dict]:
    db = await get_db()
    async with db.execute(
        "SELECT * FROM users WHERE user_id = ?", (user_id,)
    ) as cursor:
        row = await cursor.fetchone()
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
    now = datetime.utcnow().isoformat()
    await db.execute(
        """
        INSERT INTO diet_profiles
            (user_id, age, gender, height_cm, weight_kg, goal, activity_level, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
    await db.commit()


async def get_diet_profile(user_id: int) -> Optional[dict]:
    db = await get_db()
    async with db.execute(
        "SELECT * FROM diet_profiles WHERE user_id = ?", (user_id,)
    ) as cursor:
        row = await cursor.fetchone()
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
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id, meal_type) DO UPDATE SET
            time    = excluded.time,
            enabled = excluded.enabled
        """,
        (user_id, meal_type, time, int(enabled)),
    )
    await db.commit()


async def get_meal_schedules(user_id: int) -> list[dict]:
    db = await get_db()
    async with db.execute(
        "SELECT * FROM meal_schedules WHERE user_id = ? ORDER BY time", (user_id,)
    ) as cursor:
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def get_all_active_schedules() -> list[dict]:
    """Return all enabled schedules (all users) — used by scheduler."""
    db = await get_db()
    async with db.execute(
        """
        SELECT ms.*, u.timezone, u.name
        FROM meal_schedules ms
        JOIN users u ON ms.user_id = u.user_id
        WHERE ms.enabled = 1
        """
    ) as cursor:
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def save_conversation(user_id: int, role: str, content: str) -> None:
    db = await get_db()
    await db.execute(
        "INSERT INTO conversations (user_id, role, content) VALUES (?, ?, ?)",
        (user_id, role, content),
    )
    await db.commit()


async def get_recent_conversations(user_id: int, limit: int = 10) -> list[dict]:
    db = await get_db()
    async with db.execute(
        """
        SELECT role, content FROM conversations
        WHERE user_id = ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (user_id, limit),
    ) as cursor:
        rows = await cursor.fetchall()
        # Return in chronological order
        return [dict(r) for r in reversed(rows)]
