"""
services/nutrition_service.py
Food logging, daily nutrition tracking, correction and deletion.

All nutrition totals are recomputed from food_logs, so there is no separate
cache table to keep in sync when entries are edited or removed.
"""

import logging
from datetime import date, datetime, timedelta
from typing import Optional

from database.connection import as_date, get_db
from nutrition.calculator import (
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

def split_meal_type(meal_type: Optional[str]) -> Optional[str]:
    if not meal_type:
        return None
    normalised = str(meal_type).strip().lower()
    return normalised if normalised in ("breakfast", "lunch", "snack", "dinner") else None


async def infer_meal_type(user_id: int, when: Optional[datetime] = None) -> str:
    """Guess which meal a log entry belongs to, based on the user's local time.

    Populates food_logs.meal_type, which was always NULL before and made the
    meal emoji in /today meaningless.
    """
    import pytz

    from services.activity_service import _tz_for

    moment = when
    if moment is None:
        try:
            tz_name = await _tz_for(user_id)
        except Exception:
            tz_name = "Asia/Jakarta"
        moment = datetime.now(pytz.timezone(tz_name))

    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=pytz.utc)

    hour = moment.hour
    if 3 <= hour < 10:
        return "breakfast"
    if 10 <= hour < 15:
        return "lunch"
    if 15 <= hour < 18:
        return "snack"
    return "dinner"


async def log_food_items(
    user_id: int,
    items: list[ParsedFoodItem],
    meal_type: Optional[str] = None,
    log_date: Optional[str] = None,
) -> list[dict]:
    """
    Log a list of ParsedFoodItem to the database.
    Returns list of logged items with calculated nutrition (and their ids).
    """
    db = await get_db()
    today = as_date(log_date or date.today())
    meal = split_meal_type(meal_type)
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

        log_id = await db.execute(
            """
            INSERT INTO food_logs
                (user_id, date, meal_type, food_name, amount_g,
                 calories, protein_g, carbs_g, fat_g)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            RETURNING id
            """,
            (
                user_id,
                today,
                meal,
                item.food_name,
                item.amount_g,
                nutrition.calories,
                nutrition.protein_g,
                nutrition.carbs_g,
                nutrition.fat_g,
            ),
        ).fetchval()

        logged.append({
            "id": log_id,
            "food_name": item.food_name,
            "amount_g": item.amount_g,
            "calories": nutrition.calories,
            "protein_g": nutrition.protein_g,
            "carbs_g": nutrition.carbs_g,
            "fat_g": nutrition.fat_g,
        })

    logger.info("Logged %d food item(s) for user %s on %s", len(logged), user_id, today)
    return logged


async def log_food_from_text(
    user_id: int,
    text: str,
    ai_service,
    meal_type: Optional[str] = None,
    log_date: Optional[str] = None,
) -> list[dict]:
    """Parse natural language text and log food items.

    Meal type is inferred from the clock when not supplied, so entries are at
    least tagged with something.
    """
    items = await parse_food_from_llm(text, ai_service)
    if not items:
        return []

    meal = split_meal_type(meal_type)
    if meal is None:
        meal = await infer_meal_type(user_id)

    return await log_food_items(user_id, items, meal, log_date)


async def log_food_from_photo_items(
    user_id: int,
    items_data: list[dict],
    meal_type: Optional[str] = None,
    log_date: Optional[str] = None,
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

    meal = split_meal_type(meal_type)
    if meal is None:
        meal = await infer_meal_type(user_id)

    return await log_food_items(user_id, parsed_items, meal, log_date)


# ── Daily Nutrition ───────────────────────────────────────────────────────────

async def get_daily_consumed(user_id: int, date_str: Optional[str] = None) -> dict:
    """Get consumed nutrition totals for a given date (defaults to today)."""
    db = await get_db()
    today = as_date(date_str or date.today())

    row = await db.execute(
        """
        SELECT
            COALESCE(SUM(calories), 0)::float8  AS calories,
            COALESCE(SUM(protein_g), 0)::float8 AS protein_g,
            COALESCE(SUM(carbs_g),   0)::float8 AS carbs_g,
            COALESCE(SUM(fat_g),     0)::float8 AS fat_g
        FROM food_logs
        WHERE user_id = $1 AND date = $2
        """,
        (user_id, today),
    ).fetchone()

    if not row:
        return {"calories": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0}
    return {
        "calories":  round(float(row[0]), 1),
        "protein_g": round(float(row[1]), 1),
        "carbs_g":   round(float(row[2]), 1),
        "fat_g":     round(float(row[3]), 1),
    }


async def get_today_food_logs(user_id: int, date_str: Optional[str] = None) -> list[dict]:
    """Get all food logs for today."""
    db = await get_db()
    today = as_date(date_str or date.today())

    rows = await db.execute(
        """
        SELECT id, food_name, amount_g, calories, protein_g, carbs_g, fat_g, meal_type, logged_at
        FROM food_logs
        WHERE user_id = $1 AND date = $2
        ORDER BY id ASC
        """,
        (user_id, today),
    ).fetchall()
    return [dict(r) for r in rows]


async def get_recent_food_logs(user_id: int, date_str: Optional[str] = None, limit: int = 5) -> list[dict]:
    """Most recent food log entries for a day (used by correction flows)."""
    db = await get_db()
    today = as_date(date_str or date.today())

    rows = await db.execute(
        """
        SELECT id, food_name, amount_g, calories, protein_g, carbs_g, fat_g, meal_type, logged_at
        FROM food_logs
        WHERE user_id = $1 AND date = $2
        ORDER BY id DESC
        LIMIT $3
        """,
        (user_id, today, int(limit)),
    ).fetchall()
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


# ── Correction & Deletion (agentic flows) ─────────────────────────────────────

async def delete_food_log(user_id: int, log_id: int) -> Optional[dict]:
    """Delete a single food log entry. Returns the deleted row for reporting."""
    db = await get_db()
    row = await db.execute(
        """
        DELETE FROM food_logs
        WHERE id = $1 AND user_id = $2
        RETURNING id, food_name, amount_g, calories, protein_g, carbs_g, fat_g
        """,
        (log_id, user_id),
    ).fetchone()
    return dict(row) if row else None


async def delete_food_log_by_name(user_id: int, food_name: str, date_str: Optional[str] = None) -> list[dict]:
    """Delete every today's entry whose name matches (case-insensitive)."""
    db = await get_db()
    today = as_date(date_str or date.today())
    rows = await db.execute(
        """
        DELETE FROM food_logs
        WHERE user_id = $1 AND date = $2 AND lower(food_name) = lower($3)
        RETURNING id, food_name, amount_g, calories, protein_g, carbs_g, fat_g
        """,
        (user_id, today, food_name),
    ).fetchall()
    return [dict(r) for r in rows]


async def delete_last_food_log(user_id: int, date_str: Optional[str] = None) -> Optional[dict]:
    """Undo the most recent entry."""
    db = await get_db()
    today = as_date(date_str or date.today())
    row = await db.execute(
        """
        DELETE FROM food_logs
        WHERE id = (
            SELECT id FROM food_logs
            WHERE user_id = $1 AND date = $2
            ORDER BY id DESC
            LIMIT 1
        )
        RETURNING id, food_name, amount_g, calories, protein_g, carbs_g, fat_g
        """,
        (user_id, today),
    ).fetchone()
    return dict(row) if row else None


async def delete_todays_food_logs(user_id: int, date_str: Optional[str] = None) -> list[dict]:
    """Remove every entry logged for a day (used by 'hapus semua makananku')."""
    db = await get_db()
    today = as_date(date_str or date.today())
    rows = await db.execute(
        """
        DELETE FROM food_logs
        WHERE user_id = $1 AND date = $2
        RETURNING id, food_name, amount_g, calories, protein_g, carbs_g, fat_g
        """,
        (user_id, today),
    ).fetchall()
    return [dict(r) for r in rows]


async def update_food_log(user_id: int, log_id: int, updates: dict) -> Optional[dict]:
    """Patch a single entry. `updates` may hold amount_g/calories/macros/name."""
    existing = await get_food_log(user_id, log_id)
    if not existing:
        return None

    fields = ["food_name", "meal_type", "amount_g", "calories", "protein_g", "carbs_g", "fat_g"]
    new_values = {}
    for field in fields:
        if field in updates and updates[field] is not None:
            new_values[field] = updates[field]

    if not new_values:
        return existing

    # Recalculate nutrition from amount when only the weight changed.
    nutrition_override = any(
        key in new_values for key in ("calories", "protein_g", "carbs_g", "fat_g")
    )
    if "amount_g" in new_values and not nutrition_override:
        amount = float(new_values["amount_g"])
        base = existing
        # Scale existing macros by the new weight ratio.
        old_amount = float(base["amount_g"]) or amount
        ratio = amount / old_amount if old_amount else 1.0
        new_values.setdefault("calories", round(float(base["calories"]) * ratio, 1))
        new_values.setdefault("protein_g", round(float(base["protein_g"]) * ratio, 1))
        new_values.setdefault("carbs_g", round(float(base["carbs_g"]) * ratio, 1))
        new_values.setdefault("fat_g", round(float(base["fat_g"]) * ratio, 1))

    assignments = []
    params: list = []
    index = 1
    for field, value in new_values.items():
        assignments.append(f"{field} = ${index}")
        params.append(value)
        index += 1

    params.extend([log_id, user_id])
    sql = (
        "UPDATE food_logs SET "
        + ", ".join(assignments)
        + f" WHERE id = ${index} AND user_id = ${index + 1} RETURNING *"
    )

    db = await get_db()
    row = await db.execute(sql, tuple(params)).fetchone()
    return dict(row) if row else None


async def get_food_log(user_id: int, log_id: int) -> Optional[dict]:
    db = await get_db()
    row = await db.execute(
        """
        SELECT id, food_name, meal_type, amount_g, calories, protein_g, carbs_g, fat_g, date, logged_at
        FROM food_logs
        WHERE id = $1 AND user_id = $2
        """,
        (log_id, user_id),
    ).fetchone()
    return dict(row) if row else None


async def replace_food_log(
    user_id: int,
    log_id: int,
    items: list[ParsedFoodItem],
    log_date: Optional[str] = None,
    meal_type: Optional[str] = None,
) -> list[dict]:
    """Replace one entry with a corrected set of items, atomically.

    Used for 'salah, yang bener itu ...' corrections: the old row is removed and
    the corrected items are inserted inside one transaction, so a failure cannot
    leave the user with neither the old nor the new data.
    """
    db = await get_db()
    today = as_date(log_date or date.today())

    existing = await get_food_log(user_id, log_id)
    if not existing:
        return []

    old_amount = float(existing["amount_g"]) or 0
    old_cal = float(existing["calories"]) or 0
    old_prot = float(existing["protein_g"]) or 0

    meal = split_meal_type(meal_type) or existing.get("meal_type") or await infer_meal_type(user_id)

    inserted: list[dict] = []
    async with db.transaction() as tx:
        await tx.execute("DELETE FROM food_logs WHERE id = $1 AND user_id = $2", (log_id, user_id))

        for item in items:
            nutrition = calculate_food_nutrition(
                food_name=item.food_name,
                amount_g=item.amount_g,
                estimated_calories=item.calories,
                estimated_protein_g=item.protein_g,
                estimated_carbs_g=item.carbs_g,
                estimated_fat_g=item.fat_g,
            )
            new_id = await tx.execute(
                """
                INSERT INTO food_logs
                    (user_id, date, meal_type, food_name, amount_g,
                     calories, protein_g, carbs_g, fat_g)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                RETURNING id
                """,
                (
                    user_id, today, meal, item.food_name, item.amount_g,
                    nutrition.calories, nutrition.protein_g, nutrition.carbs_g, nutrition.fat_g,
                ),
            ).fetchval()

            inserted.append({
                "id": new_id,
                "food_name": item.food_name,
                "amount_g": item.amount_g,
                "calories": nutrition.calories,
                "protein_g": nutrition.protein_g,
                "carbs_g": nutrition.carbs_g,
                "fat_g": nutrition.fat_g,
            })

    logger.info(
        "Replaced food log %s for user %s: %.0fg/%.0fkcal -> %.0fg/%.0fkcal",
        log_id, user_id, old_amount, old_cal,
        sum(i["amount_g"] for i in inserted), sum(i["calories"] for i in inserted),
    )

    return {
        "replaced": existing,
        "old_totals": {
            "amount_g": old_amount, "calories": old_cal, "protein_g": old_prot,
        },
        "items": inserted,
        "totals": {
            "amount_g": sum(i["amount_g"] for i in inserted),
            "calories": sum(i["calories"] for i in inserted),
            "protein_g": sum(i["protein_g"] for i in inserted),
            "carbs_g": sum(i["carbs_g"] for i in inserted),
            "fat_g": sum(i["fat_g"] for i in inserted),
        },
        "meal_type": meal,
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
    since = as_date(today - timedelta(days=days))
    today_str = as_date(today)

    # ROUND() needs numeric (not double precision), so cast twice.
    rows = await db.execute(
        """
        SELECT
            date,
            ROUND((SUM(calories) )::numeric, 1)::float8  AS calories,
            ROUND((SUM(protein_g))::numeric, 1)::float8 AS protein_g,
            ROUND((SUM(carbs_g)  )::numeric, 1)::float8 AS carbs_g,
            ROUND((SUM(fat_g)    )::numeric, 1)::float8 AS fat_g,
            COUNT(*)::int                                 AS logged_entries
        FROM food_logs
        WHERE user_id = $1
          AND date >= $2
          AND date < $3
        GROUP BY date
        ORDER BY date DESC
        """,
        (user_id, since, today_str),
    ).fetchall()
    return [dict(r) for r in rows]


async def get_adaptive_target(user_id: int) -> Optional[dict]:
    """
    Calculate an adaptive calorie & macro target for today based on recent eating trends.

    Logic:
      - Start from the base TDEE target (from user profile).
      - Look at the last 3 days with meaningful logging (>= 2 entries).
      - Compute average surplus/deficit vs. base target.
      - Adjust today's target to gently compensate (50% of the trend, capped at ±200 kcal).
      - Protein/carbs/fat are recalculated proportionally (30/40/30 split).

    Returns None if user has no diet profile.
    """
    base_target = await get_nutrition_target(user_id)
    if not base_target:
        return None

    history = await get_nutrition_history(user_id, days=7)
    valid_days = [h for h in history if h["logged_entries"] >= 2]

    if len(valid_days) < 2:
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

    recent = valid_days[:3]
    avg_consumed = sum(d["calories"] for d in recent) / len(recent)
    avg_surplus = avg_consumed - base_target.calories

    raw_adjustment = -avg_surplus * 0.5
    adjustment = max(-200.0, min(200.0, raw_adjustment))
    adjusted_calories = max(1200.0, base_target.calories + adjustment)

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


async def get_daily_totals_for(user_id: int, date_str: str) -> dict:
    """Totals for an arbitrary date (used by correction previews)."""
    return await get_daily_consumed(user_id, date_str)



