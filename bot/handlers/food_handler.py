"""
bot/handlers/food_handler.py
/log  — log food via natural language
/food — alias for /log
"""

import logging

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from services.nutrition_service import (
    get_adaptive_target,
    get_daily_progress,
    get_nutrition_history,
    log_food_from_text,
)
from services.personality import strip_markdown_for_telegram
from services.user_service import has_diet_profile, user_exists

logger = logging.getLogger(__name__)


async def log_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id

    if not await user_exists(user_id):
        await update.message.reply_text(
            "kamu belum terdaftar nih, ketik /start dulu ya"
        )
        return

    ai_service = context.bot_data["ai_service"]

    text = " ".join(context.args) if context.args else ""
    if not text:
        await update.message.reply_text(
            "cara catat makanan: `/log nasi 200g ayam 150g telur 1`\n"
            "atau langsung cerita aja, aku bisa auto-detect 😊",
            parse_mode="Markdown",
        )
        return

    try:
        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id, action="typing"
        )
    except Exception as exc:
        logger.debug("Failed to send typing chat action: %s", exc)

    logged_items = await log_food_from_text(
        user_id=user_id,
        text=text,
        ai_service=ai_service,
    )

    if not logged_items:
        await update.message.reply_text(
            "hmm, aku nggak nemu makanan di situ 😅 "
            "coba lebih spesifik, misalnya:\n"
            "`/log nasi 200g, ayam goreng 150g, telur 1 butir`",
            parse_mode="Markdown",
        )
        return

    # Everything the user needs, in ONE message: the numbers plus the reaction.
    # Sending two separate messages felt mechanical and doubled the noise.
    has_profile = await has_diet_profile(user_id)
    progress = await get_daily_progress(user_id) if has_profile else None
    adaptive_target = await get_adaptive_target(user_id) if has_profile else None
    history = await get_nutrition_history(user_id, days=5) if has_profile else []

    response = await ai_service.generate_food_response(
        logged_items=logged_items,
        daily_progress=progress,
        adaptive_target=adaptive_target,
        nutrition_history=history,
    )

    summary_lines = ["✅ *oke, aku catat ya!*"]
    total_cal = 0
    total_prot = 0
    for item in logged_items:
        total_cal += item["calories"]
        total_prot += item["protein_g"]
        summary_lines.append(
            f"• {strip_markdown_for_telegram(str(item['food_name']).capitalize())} "
            f"({item['amount_g']:.0f}g) — {item['calories']:.0f} kcal "
            f"(P: {item['protein_g']:.1f}g | K: {item['carbs_g']:.1f}g | L: {item['fat_g']:.1f}g)"
        )

    summary_lines.append(f"\n🔥 *Total: {total_cal:.0f} kcal | Protein: {total_prot:.1f}g*")

    if progress:
        target = (
            adaptive_target["calories"]
            if adaptive_target and adaptive_target.get("days_analysed", 0) >= 2
            else progress["target"]["calories"]
        )
        remaining = max(0, target - progress["consumed"]["calories"])
        summary_lines.append(
            f"🎯 sisa *{remaining:.0f} kcal* dari target {target} kcal hari ini"
        )

    summary_lines.append("\n_/today buat lihat progress lengkap_")

    await update.message.reply_text("\n".join(summary_lines), parse_mode="Markdown")
    await update.message.reply_text(response)

    from services.user_service import save_conversation

    await save_conversation(user_id, "user", text)
    await save_conversation(user_id, "model", "\n".join(summary_lines) + "\n" + response)


async def food_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Alias for /log."""
    await log_command(update, context)


def build_food_handlers():
    return [
        CommandHandler("log", log_command),
        CommandHandler("food", food_command),
    ]
