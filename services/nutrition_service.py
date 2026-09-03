"""
services/nutrition_service.py
Food logging, daily nutrition tracking, and progress calculation.
"""

import logging
from datetime import date, datetime
from typing import Optional

from database.connection import get_db
from nutrition.calculator import (
    NutritionInfo,
    NutritionTarget,
    calculate_daily_target,
    calculate_food_nutrition,
)
from nutrition.parser import ParsedFoodItem, parse_food_from_llm
from services.user_service import get_diet_profile

logger = logging.getLogger(__name__)


# ── Nutrition Target ──────────────────────────────────────────────────────────

async def get_nutrition_target(user_id: int) -> Optional[NutritionTarget]:
    """Calculate nutrition target from user's diet profile."""
    profile = await get_diet_profile(user_id)
    if not profile:
        return None

    return calculate_daily_target(
        gender=profile["gender"],
        weight_kg=profile["weight_kg"],
        height_cm=profile["height_cm"],
        age=profile["age"],
        activity_level=profile["activity_level"],
        goal=profile["goal"],
    )


# ── Food Logging ──────────────────────────────────────────────────────────────

async def log_food_items(
    user_id: int,
    items: list[ParsedFoodItem],
    meal_type: Optional[str] = None,
    log_date: Optional[str] = None,
) -> list[dict]:
    """
    Log a list of ParsedFoodItem to the database.
    Returns list of logged items with calculated nutrition.
    """
    db = await get_db()
    today = log_date or date.today().isoformat()
    logged = []

    for item in items:
        nutrition = calculate_food_nutrition(item.food_name, item.amount_g)

        await db.execute(
            """
            INSERT INTO food_logs
                (user_id, date, meal_type, food_name, amount_g,
                 calories, protein_g, carbs_g, fat_g)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                today,
                meal_type,
                item.food_name,
                item.amount_g,
                nutrition.calories,
                nutrition.protein_g,
                nutrition.carbs_g,
                nutrition.fat_g,
            ),
        )

        logged.append({
            "food_name": item.food_name,
            "amount_g": item.amount_g,
            "calories": nutrition.calories,
            "protein_g": nutrition.protein_g,
            "carbs_g": nutrition.carbs_g,
            "fat_g": nutrition.fat_g,
        })

    await db.commit()

    # Update daily nutrition cache
    await _update_daily_nutrition(user_id, today)

    return logged


async def log_food_from_text(
    user_id: int,
    text: str,
    ai_service,
    meal_type: Optional[str] = None,
) -> list[dict]:
    """Parse natural language text and log food items."""
    items = await parse_food_from_llm(text, ai_service)
    if not items:
        return []
    return await log_food_items(user_id, items, meal_type)


# ── Daily Nutrition ───────────────────────────────────────────────────────────

async def _update_daily_nutrition(user_id: int, date_str: str) -> None:
    """Recompute and cache daily nutrition totals from food_logs."""
    db = await get_db()
    async with db.execute(
        """
        SELECT
            COALESCE(SUM(calories), 0)  AS total_calories,
            COALESCE(SUM(protein_g), 0) AS total_protein_g,
            COALESCE(SUM(carbs_g),   0) AS total_carbs_g,
            COALESCE(SUM(fat_g),     0) AS total_fat_g
        FROM food_logs
        WHERE user_id = ? AND date = ?
        """,
        (user_id, date_str),
    ) as cursor:
        row = await cursor.fetchone()

    if row:
        await db.execute(
            """
            INSERT INTO daily_nutrition
                (user_id, date, total_calories, total_protein_g, total_carbs_g, total_fat_g)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, date) DO UPDATE SET
                total_calories  = excluded.total_calories,
                total_protein_g = excluded.total_protein_g,
                total_carbs_g   = excluded.total_carbs_g,
                total_fat_g     = excluded.total_fat_g
            """,
            (user_id, date_str, row[0], row[1], row[2], row[3]),
        )
        await db.commit()


async def get_daily_consumed(user_id: int, date_str: Optional[str] = None) -> dict:
    """Get today's consumed nutrition totals."""
    db = await get_db()
    today = date_str or date.today().isoformat()

    async with db.execute(
        """
        SELECT
            COALESCE(SUM(calories), 0)  AS calories,
            COALESCE(SUM(protein_g), 0) AS protein_g,
            COALESCE(SUM(carbs_g),   0) AS carbs_g,
            COALESCE(SUM(fat_g),     0) AS fat_g
        FROM food_logs
        WHERE user_id = ? AND date = ?
        """,
        (user_id, today),
    ) as cursor:
        row = await cursor.fetchone()
        return {
            "calories":  round(row[0], 1) if row else 0,
            "protein_g": round(row[1], 1) if row else 0,
            "carbs_g":   round(row[2], 1) if row else 0,
            "fat_g":     round(row[3], 1) if row else 0,
        }


async def get_today_food_logs(user_id: int, date_str: Optional[str] = None) -> list[dict]:
    """Get all food logs for today."""
    db = await get_db()
    today = date_str or date.today().isoformat()

    async with db.execute(
        """
        SELECT food_name, amount_g, calories, protein_g, carbs_g, fat_g, meal_type, logged_at
        FROM food_logs
        WHERE user_id = ? AND date = ?
        ORDER BY logged_at
        """,
        (user_id, today),
    ) as cursor:
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def get_daily_progress(user_id: int) -> Optional[dict]:
    """
    Return a dict with target, consumed, and percentage progress.
    Returns None if no diet profile exists.
    """
    target = await get_nutrition_target(user_id)
    if not target:
        return None

    consumed = await get_daily_consumed(user_id)

    def pct(consumed: float, target: float) -> int:
        if target == 0:
            return 0
        return round((consumed / target) * 100)

    return {
        "target": {
            "calories":  target.calories,
            "protein_g": target.protein_g,
            "carbs_g":   target.carbs_g,
            "fat_g":     target.fat_g,
        },
        "consumed": consumed,
        "percentage": {
            "calories":  pct(consumed["calories"],  target.calories),
            "protein_g": pct(consumed["protein_g"], target.protein_g),
            "carbs_g":   pct(consumed["carbs_g"],   target.carbs_g),
            "fat_g":     pct(consumed["fat_g"],     target.fat_g),
        },
    }
