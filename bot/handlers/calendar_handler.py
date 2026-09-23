"""
bot/handlers/calendar_handler.py
Telegram commands for Google Calendar: /agenda, /jadwal, /calendar
"""

import logging
from datetime import datetime
import pytz
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from config.settings import APP_TIMEZONE
from services.calendar_service import (
    get_today_events,
    get_upcoming_events,
    is_user_calendar_connected,
)
from services.user_service import user_exists

logger = logging.getLogger(__name__)


def _format_event_item(item: dict) -> str:
    """Format single event dictionary to friendly markdown string."""
    summary = item.get("summary", "Acara tanpa judul")
    start = item.get("start", {})
    tz = pytz.timezone(APP_TIMEZONE)

    start_str = ""
    if date_time_str := start.get("dateTime"):
        dt = datetime.fromisoformat(date_time_str).astimezone(tz)
        start_str = dt.strftime("%A, %d %b • %H:%M")
    elif date_str := start.get("date"):
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        start_str = dt.strftime("%A, %d %b (Seharian)")

    location = item.get("location")
    loc_str = f" 📍 _{location}_" if location else ""

    return f"📌 *{summary}*\n   🕒 {start_str}{loc_str}\n"


async def agenda_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """View today's and upcoming agenda from the user's Google Calendar."""
    user_id = update.effective_user.id

    if not await user_exists(user_id):
        await update.message.reply_text("Kamu belum terdaftar! Ketik /start dulu ya 😊")
        return

    # Check if user has connected their own Google Calendar
    if not await is_user_calendar_connected(user_id):
        await update.message.reply_text(
            "📅 *Google Calendar Belum Terhubung*\n\n"
            "Hubungkan Google Calendar kamu dulu ya!\n\n"
            "Ketik /connectcalendar untuk memulai proses koneksi yang mudah 🌸\n\n"
            "_Setelah terhubung, aku bisa lihat & buat jadwal langsung di Google Calendar kamu!_",
            parse_mode="Markdown",
        )
        return

    try:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    except Exception as e:
        logger.debug(f"Failed to send typing chat action: {e}")

    try:
        today_events = await get_today_events(user_id)
        upcoming_events = await get_upcoming_events(user_id, days=5, max_results=8)

        msg_parts = ["📅 *AGENDA & JADWAL KAMU*\n"]

        if today_events:
            msg_parts.append("✨ *Hari Ini:*")
            for ev in today_events:
                msg_parts.append(_format_event_item(ev))
        else:
            msg_parts.append("✨ *Hari Ini:* Tidak ada jadwal khusus, saatnya santai! ☕\n")

        # Filter out today's events from upcoming
        today_ids = {e.get("id") for e in today_events}
        future_events = [e for e in upcoming_events if e.get("id") not in today_ids]

        if future_events:
            msg_parts.append("🗓️ *Beberapa Hari ke Depan:*")
            for ev in future_events:
                msg_parts.append(_format_event_item(ev))

        msg_parts.append(
            "\n💡 _Tips: Kamu bisa ketik langsung seperti:\n"
            "'Ingetin besok jam 3 sore ada kontrol gigi' untuk buat jadwal baru!_"
        )

        await update.message.reply_text("\n".join(msg_parts), parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Error fetching calendar agenda for user {user_id}: {e}")
        await update.message.reply_text(
            "Waduh, gagal mengambil agenda dari Google Calendar 😅\n"
            "Coba lagi nanti ya! Kalau masalah terus, coba /connectcalendar ulang.",
        )


def build_calendar_handlers():
    return [
        CommandHandler("agenda", agenda_command),
        CommandHandler("jadwal", agenda_command),
        CommandHandler("calendar", agenda_command),
    ]
