"""
bot/handlers/activity_handler.py
Activity commands: /agenda, /jadwal, /tambahkegiatan, /hapuskegiatan.

Natural-language activity handling lives in the intent router
(services/intent_service.py + bot/handlers/message_handler.py), so these
commands are thin conveniences that route through the same pipeline instead of
duplicating parsing logic.
"""

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from services.activity_service import (
    delete_activity,
    format_activity_datetime,
    get_all_user_activities,
    get_today_activities,
    get_upcoming_activities,
)
from services.intent_service import ACTION_ADD_ACTIVITY, Intent, route_message
from services.personality import strip_markdown_for_telegram
from services.user_service import user_exists

logger = logging.getLogger(__name__)


def _md(value) -> str:
    return strip_markdown_for_telegram(str(value))


def _format_activity_item(item: dict) -> str:
    title = item.get("title", "Kegiatan tanpa judul")
    activity_dt = item.get("activity_dt", "")
    desc = item.get("description", "")
    time_str = format_activity_datetime(activity_dt) if activity_dt else ""
    desc_str = f"\n   📝 _{desc}_" if desc else ""
    return f"📌 *{_md(title)}*\n   🕒 {time_str} WIB{desc_str}\n"


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
    today_ids = {a["id"] for a in today}
    future = [a for a in upcoming if a["id"] not in today_ids]

    msg_parts = ["📅 *AGENDA & JADWAL KAMU*\n"]

    if today:
        msg_parts.append("✨ *Hari Ini:*")
        for act in today:
            marker = "✅" if act.get("done") else "📌"
            msg_parts.append(_format_activity_item(act).replace("📌", marker, 1))
    else:
        msg_parts.append("✨ *Hari Ini:* Belum ada kegiatan khusus, santai dulu! ☕\n")

    if future:
        msg_parts.append("🗓️ *7 Hari ke Depan:*")
        for act in future:
            marker = "✅" if act.get("done") else "📌"
            msg_parts.append(_format_activity_item(act).replace("📌", marker, 1))

    msg_parts.append(
        "\n💡 _Tips: Ketik langsung seperti:\n"
        "'Ingetin besok jam 3 sore ada kontrol dokter' untuk tambah kegiatan!_"
    )

    await update.message.reply_text("\n".join(msg_parts), parse_mode="Markdown")


async def tambah_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/tambahkegiatan [deskripsi] — route through the intent router."""
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

    ai_service = context.bot_data.get("ai_service")
    if ai_service is None:
        await update.message.reply_text("aku lagi error internal nih, coba lagi ya")
        return

    intent = await route_message(
        ai_service=ai_service,
        message=text,
        profile={"user_id": user_id},
        recent_activities=await get_all_user_activities(user_id, limit=10),
        recent_food=[],
    )

    if intent.action != ACTION_ADD_ACTIVITY or not intent.slots.get("title"):
        await update.message.reply_text(
            "Hmm, aku kurang paham maksudnya 😅\n"
            "Coba tulis lebih jelas ya, misalnya:\n"
            "`/tambahkegiatan besok jam 9 pagi ada rapat`",
            parse_mode="Markdown",
        )
        return

    if not intent.is_complete:
        missing = intent.missing or ["date", "time"]
        context.user_data["ginchiee_slots_draft"] = {
            "action": ACTION_ADD_ACTIVITY,
            "slots": intent.slots,
            "missing": missing,
            "started_at": __import__("time").time(),
        }
        questions = {
            "title": "judulnya apa nih?",
            "date": "hari apa tanggal berapa?",
            "time": "jam berapa?",
        }
        await update.message.reply_text(
            f"boleh! 📌 *{_md(intent.slots.get('title'))}*\n\n"
            f"{questions.get(missing[0], missing[0])}",
            parse_mode="Markdown",
        )
        return

    # Reuse the confirmation pipeline so both paths behave identically.
    from bot.handlers.message_handler import (
        _stage_confirmation,
    )

    await _stage_confirmation(
        update, context, user_id,
        Intent(action=ACTION_ADD_ACTIVITY, slots=intent.slots, missing=[]),
        text,
    )


async def hapus_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/hapuskegiatan — list upcoming activities with inline delete buttons."""
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
        dt_str = format_activity_datetime(act.get("activity_dt"))
        label = f"❌ {act['title']} ({dt_str})"
        keyboard.append([
            InlineKeyboardButton(label, callback_data=f"del_activity:{act['id']}")
        ])
        text_parts.append(f"• *{_md(act['title'])}* — {dt_str}")

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
