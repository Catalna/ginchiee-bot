"""
bot/handlers/today_handler.py
/today — Show daily nutrition summary and progress.
"""

import logging
from datetime import datetime
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from services.nutrition_service import get_daily_progress, get_today_food_logs
from services.user_service import user_exists, has_diet_profile

logger = logging.getLogger(__name__)


def _progress_bar(pct: int, length: int = 10) -> str:
    filled = min(int(pct / 100 * length), length)
    bar = "█" * filled + "░" * (length - filled)
    return f"[{bar}] {pct}%"


async def today_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id

    if not await user_exists(user_id):
        await update.message.reply_text("Kamu belum terdaftar! Ketik /start dulu 😊")
        return

    if not await has_diet_profile(user_id):
        await update.message.reply_text(
            "Profil dietmu belum lengkap 😅\nKetik /start untuk setup profil!"
        )
        return

    try:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    except Exception as e:
        logger.debug(f"Failed to send typing chat action: {e}")

    progress = await get_daily_progress(user_id)
    food_logs = await get_today_food_logs(user_id)

    today_str = datetime.now().strftime("%A, %d %B %Y")

    if not progress:
        await update.message.reply_text("Gagal mengambil data progress. Coba lagi nanti!")
        return

    t = progress["target"]
    c = progress["consumed"]
    p = progress["percentage"]

    # Progress bars
    cal_bar   = _progress_bar(p["calories"])
    prot_bar  = _progress_bar(p["protein_g"])
    carbs_bar = _progress_bar(p["carbs_g"])
    fat_bar   = _progress_bar(p["fat_g"])

    # Food log list
    food_list = ""
    if food_logs:
        food_list = "\n\n🍽️ *Makanan Hari Ini:*\n"
        for log in food_logs:
            meal_emoji = {
                "breakfast": "🌅",
                "lunch": "☀️",
                "snack": "🍎",
                "dinner": "🌙",
            }.get(log.get("meal_type", ""), "•")
            food_list += (
                f"{meal_emoji} {log['food_name'].capitalize()} "
                f"({log['amount_g']:.0f}g) — {log['calories']:.0f} kcal\n"
            )
    else:
        food_list = "\n\n_Belum ada makanan yang dicatat hari ini._\n_Gunakan /log untuk mencatat!_"

    await update.message.reply_text(
        f"📊 *Progress Hari Ini*\n"
        f"_{today_str}_\n\n"
        f"🔥 *Kalori:* {c['calories']:.0f} / {t['calories']} kcal\n"
        f"`{cal_bar}`\n\n"
        f"🥩 *Protein:* {c['protein_g']:.0f} / {t['protein_g']}g\n"
        f"`{prot_bar}`\n\n"
        f"🍚 *Karbs:* {c['carbs_g']:.0f} / {t['carbs_g']}g\n"
        f"`{carbs_bar}`\n\n"
        f"🥑 *Lemak:* {c['fat_g']:.0f} / {t['fat_g']}g\n"
        f"`{fat_bar}`"
        f"{food_list}",
        parse_mode="Markdown",
    )


def build_today_handlers():
    return [CommandHandler("today", today_command)]
