"""
bot/handlers/activity_handler.py
Commands for managing user activities stored in local DB.
Replaces calendar_handler.py (no longer uses Google Calendar).

Commands:
  /agenda   - View today + upcoming activities
  /jadwal   - Alias for /agenda
  /tambahkegiatan [deskripsi] - Add a new activity via command
  /hapuskegiatan  - List activities with inline delete buttons
"""

import logging
from datetime import datetime
import pytz
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CommandHandler, CallbackQueryHandler, ContextTypes

from config.settings import APP_TIMEZONE
from services.activity_service import (
    add_activity,
    get_today_activities,
    get_upcoming_activities,
    get_all_user_activities,
    delete_activity,
    parse_activity_from_text,
)
from services.user_service import user_exists

logger = logging.getLogger(__name__)

_TZ = pytz.timezone(APP_TIMEZONE)


INDONESIAN_DAYS = {
    "Monday": "Senin", "Tuesday": "Selasa", "Wednesday": "Rabu",
    "Thursday": "Kamis", "Friday": "Jumat", "Saturday": "Sabtu", "Sunday": "Minggu"
}
INDONESIAN_MONTHS = {
    1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "Mei", 6: "Jun",
    7: "Jul", 8: "Agu", 9: "Sep", 10: "Okt", 11: "Nov", 12: "Des"
}


def _format_activity_item(item: dict) -> str:
    """Format a single activity dict to friendly markdown string."""
    title = item.get("title", "Kegiatan tanpa judul")
    activity_dt = item.get("activity_dt", "")
    desc = item.get("description", "")

    time_str = ""
    if activity_dt:
        try:
            dt = datetime.fromisoformat(activity_dt)
            day_name = INDONESIAN_DAYS.get(dt.strftime("%A"), dt.strftime("%A"))
            month_name = INDONESIAN_MONTHS.get(dt.month, dt.strftime("%b"))
            time_str = f"{day_name}, {dt.day} {month_name} • {dt.strftime('%H:%M')}"
        except Exception:
            time_str = activity_dt[:16].replace("T", " ")

    desc_str = f"\n   📝 _{desc}_" if desc else ""
    return f"📌 *{title}*\n   🕒 {time_str} WIB{desc_str}\n"


async def agenda_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """View today's and upcoming activities."""
    user_id = update.effective_user.id

    if not await user_exists(user_id):
        await update.message.reply_text("Kamu belum terdaftar! Ketik /start dulu ya 😊")
        return

    try:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    except Exception:
        pass

    today = await get_today_activities(user_id)
    upcoming = await get_upcoming_activities(user_id, days=7)

    msg_parts = ["📅 *AGENDA & JADWAL KAMU*\n"]

    if today:
        msg_parts.append("✨ *Hari Ini:*")
        for act in today:
            msg_parts.append(_format_activity_item(act))
    else:
        msg_parts.append("✨ *Hari Ini:* Belum ada kegiatan khusus, santai dulu! ☕\n")

    # Filter out today events from upcoming
    today_ids = {a["id"] for a in today}
    future = [a for a in upcoming if a["id"] not in today_ids]

    if future:
        msg_parts.append("🗓️ *7 Hari ke Depan:*")
        for act in future:
            msg_parts.append(_format_activity_item(act))

    msg_parts.append(
        "\n💡 _Tips: Ketik langsung seperti:\n"
        "'Ingetin besok jam 3 sore ada kontrol dokter' untuk tambah kegiatan!_"
    )

    await update.message.reply_text("\n".join(msg_parts), parse_mode="Markdown")


async def tambah_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /tambahkegiatan [deskripsi natural language]
    Parse the description using AI and save the activity.
    """
    user_id = update.effective_user.id

    if not await user_exists(user_id):
        await update.message.reply_text("Kamu belum terdaftar! Ketik /start dulu ya 😊")
        return

    text = " ".join(context.args) if context.args else ""

    if not text:
        await update.message.reply_text(
            "✏️ *Tambah Kegiatan*\n\n"
            "Tulis deskripsi kegiatan setelah command, contoh:\n"
            "`/tambahkegiatan besok jam 10 meeting sama tim`\n"
            "`/tambahkegiatan lusa jam 3 sore kontrol dokter`\n\n"
            "_Atau langsung cerita ke aku tanpa command, aku bisa auto-detect!_ 😊",
            parse_mode="Markdown",
        )
        return

    try:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    except Exception:
        pass

    ai_service = context.bot_data["ai_service"]
    parsed = await parse_activity_from_text(text, ai_service)

    if not parsed:
        await update.message.reply_text(
            "Hmm, aku kurang paham maksudnya 😅\n"
            "Coba tulis lebih jelas ya, misal:\n"
            "`/tambahkegiatan besok jam 9 pagi ada rapat`",
            parse_mode="Markdown",
        )
        return

    activity_id = await add_activity(
        user_id=user_id,
        title=parsed["title"],
        activity_dt=parsed["activity_dt"],
        description=parsed.get("description", ""),
        remind_mins=parsed.get("remind_mins", 30),
    )

    dt_str = parsed["activity_dt"][:16].replace("T", " ")
    remind = parsed.get("remind_mins", 30)

    await update.message.reply_text(
        f"✅ *Kegiatan tersimpan!*\n\n"
        f"📌 *{parsed['title']}*\n"
        f"🕒 {dt_str} WIB\n"
        f"⏰ Akan aku ingatkan {remind} menit sebelumnya\n\n"
        f"_Ketik /agenda untuk lihat semua kegiatanmu!_ 🌸",
        parse_mode="Markdown",
    )


async def hapus_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/hapuskegiatan — Show list of upcoming activities with delete buttons."""
    user_id = update.effective_user.id

    if not await user_exists(user_id):
        await update.message.reply_text("Kamu belum terdaftar! Ketik /start dulu ya 😊")
        return

    activities = await get_all_user_activities(user_id)

    if not activities:
        await update.message.reply_text(
            "Kamu belum punya kegiatan yang tersimpan! 😊\n"
            "Tambah dengan ketik langsung atau /tambahkegiatan",
        )
        return

    keyboard = []
    text_parts = ["🗑️ *Pilih kegiatan yang ingin dihapus:*\n"]

    for act in activities:
        dt_str = act["activity_dt"][:16].replace("T", " ")
        label = f"❌ {act['title']} ({dt_str})"
        keyboard.append([
            InlineKeyboardButton(label, callback_data=f"del_activity:{act['id']}")
        ])
        text_parts.append(f"• *{act['title']}* — {dt_str}")

    keyboard.append([InlineKeyboardButton("🚫 Batal", callback_data="del_activity:cancel")])

    await update.message.reply_text(
        "\n".join(text_parts),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def handle_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline button presses from /hapuskegiatan."""
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    data = query.data  # "del_activity:<id>" or "del_activity:cancel"

    if data == "del_activity:cancel":
        await query.edit_message_text("Oke, tidak ada yang dihapus! 😊")
        return

    try:
        activity_id = int(data.split(":")[1])
    except (ValueError, IndexError):
        await query.edit_message_text("Terjadi kesalahan, coba lagi ya.")
        return

    deleted = await delete_activity(activity_id, user_id)

    if deleted:
        await query.edit_message_text(
            "✅ Kegiatan berhasil dihapus! 🗑️\n"
            "Ketik /agenda untuk lihat sisa kegiatanmu."
        )
    else:
        await query.edit_message_text("Hmm, kegiatan tidak ditemukan atau sudah dihapus 😅")


def build_activity_handlers():
    return [
        CommandHandler("agenda", agenda_command),
        CommandHandler("jadwal", agenda_command),
        CommandHandler("tambahkegiatan", tambah_command),
        CommandHandler("hapuskegiatan", hapus_command),
        CallbackQueryHandler(handle_delete_callback, pattern=r"^del_activity:"),
    ]
