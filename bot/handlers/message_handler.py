"""
bot/handlers/message_handler.py
Handles free-form text messages (natural language conversation).
Auto-detects food logging intent vs. general conversation.
"""

import logging
from telegram import Update
from telegram.ext import ContextTypes, MessageHandler, filters

from services.user_service import (
    get_user,
    get_diet_profile,
    get_meal_schedules,
    save_conversation,
    get_recent_conversations,
    user_exists,
)
from services.nutrition_service import (
    get_daily_consumed,
    get_nutrition_target,
    get_today_food_logs,
    log_food_from_text,
    get_daily_progress,
)

logger = logging.getLogger(__name__)

# Keywords that suggest food logging intent
FOOD_LOG_KEYWORDS = [
    "makan", "minum", "habis makan", "baru makan", "udah makan", "lg makan", "lagi makan",
    "sarapan", "lunch", "dinner", "snack", "cemilan", "ngemil", "sarap",
    "nasi", "ayam", "telur", "ikan", "tempe", "tahu", "daging", "sapi", "kambing", "bebek",
    "mie", "bihun", "kwetiau", "pasta", "spaghetti", "roti", "susu", "kopi", "teh", "jus",
    "buah", "sayur", "pizza", "burger", "bakso", "soto", "sate", "rendang", "gulai",
    "sambal", "sambel", "pepes", "opor", "rawon", "pecel", "geprek", "penyet",
    "udang", "cumi", "kepiting", "seafood", "martabak", "gorengan", "kentang",
    "gw makan", "aku makan", "saya makan", "tadi makan", "mkn",
    "gram", "100g", "150g", "200g", "250g", "porsi", "potong", "butir", "mangkok", "mangkuk", "piring", "gelas", "sendok", "sdm",
]


def _looks_like_food_log(text: str) -> bool:
    """Heuristic: does this message describe eating something?"""
    text_lower = text.lower()
    return any(kw in text_lower for kw in FOOD_LOG_KEYWORDS)


async def _build_chat_context(user_id: int) -> dict:
    """Build context dict for AI service."""
    user = await get_user(user_id)
    profile = await get_diet_profile(user_id)
    target = await get_nutrition_target(user_id)
    consumed = await get_daily_consumed(user_id)
    food_logs = await get_today_food_logs(user_id)
    schedules = await get_meal_schedules(user_id)

    return {
        "profile": {
            **dict(profile),
            "name": user["name"] if user else "User",
        } if profile else {"name": user["name"] if user else "User"},
        "target": {
            "calories": target.calories,
            "protein_g": target.protein_g,
            "carbs_g": target.carbs_g,
            "fat_g": target.fat_g,
        } if target else None,
        "consumed": consumed,
        "food_logs": food_logs[-5:] if food_logs else [],
        "schedules": schedules,
    }


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Main message handler for free-form text."""
    user_id = update.effective_user.id
    text = update.message.text.strip()

    if not text:
        return

    # Check if registered
    if not await user_exists(user_id):
        await update.message.reply_text(
            "Halo! 👋 Kayaknya kita belum kenalan nih~\n"
            "Ketik /start untuk mulai ya! 😊"
        )
        return

    ai_service = context.bot_data["ai_service"]

    # Typing indicator
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id, action="typing"
    )

    # Auto-detect food logging intent
    if _looks_like_food_log(text):
        await _handle_possible_food_log(update, context, user_id, text, ai_service)
        return

    # General AI conversation
    await _handle_general_chat(update, context, user_id, text, ai_service)


async def _handle_possible_food_log(
    update, context, user_id: int, text: str, ai_service
) -> None:
    """
    Try to parse and log food from a natural language message.
    If food items found → log them.
    If not → treat as conversation.
    """
    from services.nutrition_service import log_food_from_text, get_daily_progress
    from services.user_service import has_diet_profile

    # Save user message to history first
    await save_conversation(user_id, "user", text)

    logged_items = await log_food_from_text(
        user_id=user_id,
        text=text,
        ai_service=ai_service,
    )

    if logged_items:
        # Respond as food log
        progress = await get_daily_progress(user_id) if await has_diet_profile(user_id) else None
        response = await ai_service.generate_food_response(logged_items, progress)

        # Show quick summary
        food_summary = "✅ *Aku catat ya!*\n"
        for item in logged_items:
            food_summary += (
                f"• {item['food_name'].capitalize()} ({item['amount_g']:.0f}g) "
                f"— {item['calories']:.0f} kcal\n"
            )

        await update.message.reply_text(food_summary, parse_mode="Markdown")
        await update.message.reply_text(response)
        await save_conversation(user_id, "model", response)
    else:
        # Treat as general chat
        await _handle_general_chat(update, context, user_id, text, ai_service)


async def _handle_general_chat(
    update, context, user_id: int, text: str, ai_service
) -> None:
    """Handle general conversational messages."""
    # Get conversation history
    history = await get_recent_conversations(user_id)

    # Build context
    chat_context = await _build_chat_context(user_id)

    # Generate response
    response = await ai_service.chat(
        message=text,
        context=chat_context,
        history=history,
    )

    # Save to conversation history
    await save_conversation(user_id, "user", text)
    await save_conversation(user_id, "model", response)

    await update.message.reply_text(response)


def build_message_handlers():
    return [
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_message,
        )
    ]
