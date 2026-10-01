"""
bot/handlers/schedule_handler.py
/schedule  — Set meal schedule times
/reminders — View current meal schedule
"""

import logging
import re
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from services.user_service import (
    get_meal_schedules,
    set_meal_schedule,
    user_exists,
    VALID_MEALS,
)

logger = logging.getLogger(__name__)

# States
SELECT_MEAL, SET_TIME = range(2)

MEAL_LABELS = {
    "breakfast": "🌅 Sarapan",
    "lunch":     "☀️ Makan Siang",
    "snack":     "🍎 Snack",
    "dinner":    "🌙 Makan Malam",
}

MEAL_EMOJI = {
    "breakfast": "🌅",
    "lunch":     "☀️",
    "snack":     "🍎",
    "dinner":    "🌙",
}


async def schedule_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id

    if not await user_exists(user_id):
        await update.message.reply_text("Kamu belum terdaftar! Ketik /start dulu 😊")
        return ConversationHandler.END

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data=meal)]
        for meal, label in MEAL_LABELS.items()
    ])

    await update.message.reply_text(
        "⏰ *Atur Jadwal Makan*\n\nMau atur jadwal yang mana?",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )
    return SELECT_MEAL


async def select_meal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    meal_type = query.data
    if meal_type not in VALID_MEALS:
        await query.edit_message_text("Pilihan tidak valid 😅")
        return ConversationHandler.END

    context.user_data["schedule_meal"] = meal_type
    meal_label = MEAL_LABELS[meal_type]

    await query.edit_message_text(
        f"⏰ *{meal_label}*\n\n"
        f"Jam berapa? Ketik dalam format *HH:MM*\n"
        f"Contoh: `07:30` atau `12:00`",
        parse_mode="Markdown",
    )
    return SET_TIME


async def set_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    time_text = update.message.text.strip()

    # Validate HH:MM format
    if not re.match(r"^\d{1,2}:\d{2}$", time_text):
        await update.message.reply_text(
            "Format waktu kurang tepat 😅 Gunakan format HH:MM, contoh: `07:30`",
            parse_mode="Markdown",
        )
        return SET_TIME

    try:
        h, m = time_text.split(":")
        h, m = int(h), int(m)
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError
    except ValueError:
        await update.message.reply_text("Waktu tidak valid 😅 Coba lagi:")
        return SET_TIME

    # Normalize to HH:MM
    time_str = f"{h:02d}:{m:02d}"
    meal_type = context.user_data.get("schedule_meal")

    await set_meal_schedule(user_id, meal_type, time_str, enabled=True)

    meal_label = MEAL_LABELS.get(meal_type, meal_type)
    await update.message.reply_text(
        f"✅ {meal_label} diset ke jam *{time_str}*!\n\n"
        f"Kamu akan dapat reminder otomatis setiap hari.\n"
        f"Lihat semua jadwal: /reminders",
        parse_mode="Markdown",
    )
    return ConversationHandler.END


async def cancel_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Pengaturan jadwal dibatalkan 😊")
    return ConversationHandler.END


async def reminders_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id

    if not await user_exists(user_id):
        await update.message.reply_text("Kamu belum terdaftar! Ketik /start dulu 😊")
        return

    schedules = await get_meal_schedules(user_id)

    if not schedules:
        await update.message.reply_text(
            "Belum ada jadwal makan yang diset 😊\n"
            "Gunakan /schedule untuk mengatur jadwal reminder!",
        )
        return

    msg = "⏰ *Jadwal Makanmu:*\n\n"
    for s in schedules:
        emoji = MEAL_EMOJI.get(s["meal_type"], "•")
        label = MEAL_LABELS.get(s["meal_type"], s["meal_type"])
        status = "✅" if s["enabled"] else "❌"
        msg += f"{status} {emoji} {label}: `{s['time']}`\n"

    msg += "\nGunakan /schedule untuk mengubah jadwal."

    await update.message.reply_text(msg, parse_mode="Markdown")


def build_schedule_handlers():
    conv = ConversationHandler(
        entry_points=[CommandHandler("schedule", schedule_command)],
        states={
            SELECT_MEAL: [CallbackQueryHandler(select_meal, pattern="^(breakfast|lunch|snack|dinner)$")],
            SET_TIME:    [MessageHandler(filters.TEXT & ~filters.COMMAND, set_time)],
        },
        fallbacks=[CommandHandler("cancel", cancel_schedule)],
        allow_reentry=True,
        per_message=False,
    )
    return [
        conv,
        CommandHandler("reminders", reminders_command),
    ]
