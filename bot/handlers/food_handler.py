"""
bot/handlers/food_handler.py
/log  — log food via natural language
/food — alias for /log
"""

import logging
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from services.nutrition_service import log_food_from_text, get_daily_progress
from services.user_service import has_diet_profile, user_exists

logger = logging.getLogger(__name__)


async def log_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    ai_service = context.bot_data["ai_service"]

    if not await user_exists(user_id):
        await update.message.reply_text("Kamu belum terdaftar! Ketik /start dulu 😊")
        return

    # Get text after command
    text = " ".join(context.args) if context.args else ""
    if not text:
        await update.message.reply_text(
            "📝 *Cara log makanan:*\n\n"
            "`/log nasi 200g ayam 150g telur 1`\n"
            "`/log makan siang: nasi goreng 1 porsi`\n"
            "`/log bubur ayam 1 mangkok dan teh manis`\n\n"
            "_Kamu juga bisa langsung cerita ke aku dan aku bisa bantu log-in!_",
            parse_mode="Markdown",
        )
        return

    # Typing indicator
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    # Parse and log food
    logged_items = await log_food_from_text(
        user_id=user_id,
        text=text,
        ai_service=ai_service,
    )

    if not logged_items:
        await update.message.reply_text(
            "Hmm, aku tidak bisa mengenali makanan dari teks itu 😅\n"
            "Coba tulis lebih spesifik, contoh:\n"
            "`/log nasi 200g, ayam goreng 150g, telur 1 butir`",
            parse_mode="Markdown",
        )
        return

    # Get progress for AI response
    progress = await get_daily_progress(user_id) if await has_diet_profile(user_id) else None

    # Generate AI response
    response = await ai_service.generate_food_response(logged_items, progress)

    # Also add a food summary
    food_summary = "✅ *Makanan tercatat:*\n"
    for item in logged_items:
        food_summary += (
            f"• {item['food_name'].capitalize()} ({item['amount_g']:.0f}g) "
            f"— {item['calories']:.0f} kcal\n"
        )

    await update.message.reply_text(
        food_summary,
        parse_mode="Markdown",
    )
    await update.message.reply_text(response)


async def food_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Alias for /log."""
    await log_command(update, context)


def build_food_handlers():
    return [
        CommandHandler("log", log_command),
        CommandHandler("food", food_command),
    ]
