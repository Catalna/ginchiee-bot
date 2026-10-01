"""
services/nutrition_service.py
Food logging, daily nutrition tracking, and progress calculation.
"""

import logging
from datetime import date, datetime, timedelta
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
        nutrition = calculate_food_nutrition(
            food_name=item.food_name,
            amount_g=item.amount_g,
            estimated_calories=item.calories,
            estimated_protein_g=item.protein_g,
            estimated_carbs_g=item.carbs_g,
            estimated_fat_g=item.fat_g,
        )

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


async def log_food_from_photo_items(
    user_id: int,
    items_data: list[dict],
    meal_type: Optional[str] = None,
) -> list[dict]:
    """Convert parsed photo food items dicts to ParsedFoodItem and log them."""
    if not items_data:
        return []
    parsed_items = []
    for d in items_data:
        parsed_items.append(
            ParsedFoodItem(
                food_name=str(d.get("food", "Makanan")).strip(),
                amount_g=float(d.get("amount_g", 100)),
                unit=str(d.get("unit", "porsi")),
                amount_raw=f"{d.get('amount_g', 100)}g",
                calories=float(d.get("calories", 0)),
                protein_g=float(d.get("protein_g", 0)),
                carbs_g=float(d.get("carbs_g", 0)),
                fat_g=float(d.get("fat_g", 0)),
            )
        )
    return await log_food_items(user_id, parsed_items, meal_type)


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


# ── Nutrition History & Adaptive Target ───────────────────────────────────────

async def get_nutrition_history(user_id: int, days: int = 7) -> list[dict]:
    """
    Return daily nutrition totals for the past N days (excluding today).
    Each row: {date, calories, protein_g, carbs_g, fat_g, logged_entries}.
    Only returns days where at least 1 food entry was logged.
    """
    db = await get_db()
    today = date.today()
    since = (today - timedelta(days=days)).isoformat()
    today_str = today.isoformat()

    async with db.execute(
        """
        SELECT
            date,
            ROUND(SUM(calories), 1)  AS calories,
            ROUND(SUM(protein_g), 1) AS protein_g,
            ROUND(SUM(carbs_g),   1) AS carbs_g,
            ROUND(SUM(fat_g),     1) AS fat_g,
            COUNT(*)                  AS logged_entries
        FROM food_logs
        WHERE user_id = ?
          AND date >= ?
          AND date < ?
        GROUP BY date
        ORDER BY date DESC
        """,
        (user_id, since, today_str),
    ) as cursor:
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def get_adaptive_target(user_id: int) -> Optional[dict]:
    """
    Calculate an adaptive calorie & macro target for today based on recent eating trends.

    Logic:
      - Start from the base TDEE target (from user profile).
      - Look at the last 3 days with meaningful logging (>= 2 entries).
      - Compute average surplus/deficit vs. base target.
      - Adjust today's target to gently compensate (50% of the trend, capped at ±200 kcal):
          * Consistently overate  -> slightly reduce today's target.
          * Consistently underate -> slightly increase today's target.
      - Protein/carbs/fat are recalculated proportionally (30/40/30 split).

    Returns None if user has no diet profile.
    Returns dict with keys:
        calories, protein_g, carbs_g, fat_g,
        base_calories, adjustment_kcal, trend_label, days_analysed.
    """
    base_target = await get_nutrition_target(user_id)
    if not base_target:
        return None

    history = await get_nutrition_history(user_id, days=7)
    # Only count days with at least 2 entries (more representative)
    valid_days = [h for h in history if h["logged_entries"] >= 2]

    if len(valid_days) < 2:
        # Not enough history — return base target with neutral metadata
        return {
            "calories":        base_target.calories,
            "protein_g":       base_target.protein_g,
            "carbs_g":         base_target.carbs_g,
            "fat_g":           base_target.fat_g,
            "base_calories":   base_target.calories,
            "adjustment_kcal": 0,
            "trend_label":     "belum cukup data",
            "days_analysed":   0,
        }

    # Use the 3 most recent valid days
    recent = valid_days[:3]
    avg_consumed = sum(d["calories"] for d in recent) / len(recent)
    avg_surplus = avg_consumed - base_target.calories  # positive = overate

    # Clamp compensation to ±200 kcal, apply 50% of the trend
    raw_adjustment = -avg_surplus * 0.5
    adjustment = max(-200.0, min(200.0, raw_adjustment))
    adjusted_calories = max(1200.0, base_target.calories + adjustment)

    # Recalculate macros proportionally (same 30/40/30 split as base)
    protein_g = (adjusted_calories * 0.30) / 4
    carbs_g   = (adjusted_calories * 0.40) / 4
    fat_g     = (adjusted_calories * 0.30) / 9

    if avg_surplus > 50:
        trend_label = "kelebihan kalori"
    elif avg_surplus < -50:
        trend_label = "kekurangan kalori"
    else:
        trend_label = "seimbang"

    return {
        "calories":        round(adjusted_calories),
        "protein_g":       round(protein_g),
        "carbs_g":         round(carbs_g),
        "fat_g":           round(fat_g),
        "base_calories":   base_target.calories,
        "adjustment_kcal": round(adjustment),
        "trend_label":     trend_label,
        "days_analysed":   len(recent),
    }
