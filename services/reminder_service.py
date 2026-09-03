"""
services/reminder_service.py
Build reminder context and trigger AI-generated reminder messages.
"""

import logging
from datetime import date

from services.nutrition_service import get_daily_consumed, get_nutrition_target
from services.user_service import get_user

logger = logging.getLogger(__name__)


async def build_reminder_context(user_id: int, meal_type: str) -> dict:
    """
    Gather all context needed for generating an intelligent reminder.
    """
    user = await get_user(user_id)
    target = await get_nutrition_target(user_id)
    consumed = await get_daily_consumed(user_id)

    return {
        "user_id": user_id,
        "user_name": user["name"] if user else "kamu",
        "meal_type": meal_type,
        "date": date.today().isoformat(),
        # Consumed today
        "calories_consumed": consumed.get("calories", 0),
        "protein_consumed": consumed.get("protein_g", 0),
        "carbs_consumed": consumed.get("carbs_g", 0),
        "fat_consumed": consumed.get("fat_g", 0),
        # Targets
        "calories_target": target.calories if target else 2000,
        "protein_target": target.protein_g if target else 130,
        "carbs_target": target.carbs_g if target else 230,
        "fat_target": target.fat_g if target else 70,
    }


async def generate_reminder_message(
    user_id: int,
    meal_type: str,
    ai_service,
) -> str:
    """
    Generate an intelligent, personalized reminder message using AI.
    """
    context = await build_reminder_context(user_id, meal_type)
    return await ai_service.generate_reminder(context)
