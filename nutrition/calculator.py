"""
nutrition/calculator.py
Deterministic BMR/TDEE/macro target calculator.
Formula: Mifflin-St Jeor
"""

from dataclasses import dataclass
from typing import Literal

ACTIVITY_MULTIPLIERS = {
    "SEDENTARY":   1.2,
    "LIGHT":       1.375,
    "MODERATE":    1.55,
    "ACTIVE":      1.725,
    "VERY_ACTIVE": 1.9,
}

GOAL_CALORIE_DELTA = {
    "LOSE_WEIGHT":     -500,
    "MAINTAIN_WEIGHT":    0,
    "GAIN_WEIGHT":     +300,
}


@dataclass
class NutritionTarget:
    calories: float
    protein_g: float
    carbs_g: float
    fat_g: float
    water_ml: float


def calculate_bmr(
    gender: Literal["male", "female"],
    weight_kg: float,
    height_cm: float,
    age: int,
) -> float:
    """Mifflin-St Jeor BMR formula."""
    if gender == "male":
        return 10 * weight_kg + 6.25 * height_cm - 5 * age + 5
    else:
        return 10 * weight_kg + 6.25 * height_cm - 5 * age - 161


def calculate_tdee(bmr: float, activity_level: str) -> float:
    """Total Daily Energy Expenditure."""
    multiplier = ACTIVITY_MULTIPLIERS.get(activity_level, 1.2)
    return bmr * multiplier


def calculate_daily_target(
    gender: Literal["male", "female"],
    weight_kg: float,
    height_cm: float,
    age: int,
    activity_level: str,
    goal: str,
) -> NutritionTarget:
    """Calculate daily nutrition targets based on user profile."""
    bmr = calculate_bmr(gender, weight_kg, height_cm, age)
    tdee = calculate_tdee(bmr, activity_level)
    delta = GOAL_CALORIE_DELTA.get(goal, 0)
    calories = max(tdee + delta, 1200)  # safety floor

    # Macro split (standard balanced diet):
    # Protein: 30%, Carbs: 40%, Fat: 30%
    protein_g = (calories * 0.30) / 4   # 4 kcal/g
    carbs_g   = (calories * 0.40) / 4
    fat_g     = (calories * 0.30) / 9   # 9 kcal/g

    # Water: 35ml per kg body weight
    water_ml = weight_kg * 35

    return NutritionTarget(
        calories=round(calories),
        protein_g=round(protein_g),
        carbs_g=round(carbs_g),
        fat_g=round(fat_g),
        water_ml=round(water_ml),
    )


@dataclass
class NutritionInfo:
    calories: float
    protein_g: float
    carbs_g: float
    fat_g: float


def calculate_food_nutrition(food_name: str, amount_g: float) -> NutritionInfo:
    """
    Calculate nutrition for a specific food and amount.
    Uses the food_database for lookup.
    """
    from nutrition.food_database import FOOD_DB

    key = food_name.lower().strip()
    entry = FOOD_DB.get(key)

    if entry is None:
        # Try partial match
        for db_key, db_val in FOOD_DB.items():
            if key in db_key or db_key in key:
                entry = db_val
                break

    if entry is None:
        # Fallback: estimate as generic food
        entry = {"calories": 150, "protein": 5, "carbs": 20, "fat": 5}

    factor = amount_g / 100  # database stores values per 100g

    return NutritionInfo(
        calories=round(entry["calories"] * factor, 1),
        protein_g=round(entry["protein"] * factor, 1),
        carbs_g=round(entry["carbs"] * factor, 1),
        fat_g=round(entry["fat"] * factor, 1),
    )
