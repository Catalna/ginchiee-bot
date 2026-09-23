"""
bot/handlers/connect_calendar_handler.py
Handles Google Calendar OAuth2 connection flow per user.

Commands:
  /connectcalendar  — Start OAuth2 flow, send auth URL to user
  /calendarcode     — User pastes the authorization code here
  /disconnectcalendar — Remove user's calendar connection
"""

import logging

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters

from services.calendar_service import (
    is_oauth_configured,
    get_auth_url,
    exchange_code_for_tokens,
    save_user_token,
    delete_user_token,
    is_user_calendar_connected,
)
from services.user_service import user_exists

logger = logging.getLogger(__name__)

# ConversationHandler state
WAITING_FOR_CODE = 1


async def connect_calendar_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    /connectcalendar — Generate and send OAuth2 authorization URL.
    User must visit the URL, log in with Google, and paste the code back.
    """
    user_id = update.effective_user.id

    if not await user_exists(user_id):
        await update.message.reply_text("Kamu belum terdaftar! Ketik /start dulu ya 😊")
        return ConversationHandler.END

    # Check if OAuth2 client secrets file is configured
    if not is_oauth_configured():
        await update.message.reply_text(
            "⚙️ *Fitur Google Calendar Belum Dikonfigurasi*\n\n"
            "Admin perlu mengatur file `oauth_client_secrets.json` terlebih dahulu.\n"
            "Ikuti panduan setup di README ya! 🌸",
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    # Check if already connected
    if await is_user_calendar_connected(user_id):
        await update.message.reply_text(
            "✅ *Google Calendar kamu sudah terhubung!*\n\n"
            "Kamu bisa langsung tanya jadwal atau buat event baru.\n\n"
            "Kalau mau disconnect, ketik /disconnectcalendar 🌸",
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    try:
        auth_url = get_auth_url()
    except Exception as e:
        logger.error(f"Failed to generate auth URL: {e}")
        await update.message.reply_text(
            "Waduh, gagal membuat link login Google 😅\n"
            "Coba lagi nanti ya!",
        )
        return ConversationHandler.END

    await update.message.reply_text(
        "📅 *Hubungkan Google Calendar kamu!*\n\n"
        "Ikuti langkah berikut:\n\n"
        "1️⃣ Klik link di bawah untuk login ke akun Google kamu:\n"
        f"`{auth_url}`\n\n"
        "2️⃣ Pilih akun Google yang ingin dihubungkan\n\n"
        "3️⃣ Klik *'Izinkan'* / *'Allow'*\n\n"
        "4️⃣ Setelah itu akan muncul kode — *salin kode tersebut* dan kirim ke sini\n\n"
        "⏳ Kirimkan kodenya sekarang, atau ketik /cancel untuk batal.",
        parse_mode="Markdown",
        disable_web_page_preview=True,
    )

    return WAITING_FOR_CODE


async def receive_auth_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Receives the authorization code pasted by the user.
    Exchanges it for tokens and saves to DB.
    """
    user_id = update.effective_user.id
    auth_code = update.message.text.strip()

    # Remove any accidental /calendarcode prefix
    if auth_code.startswith("/calendarcode"):
        auth_code = auth_code.replace("/calendarcode", "").strip()

    if not auth_code:
        await update.message.reply_text(
            "Kodenya kosong nih 😅 Coba kirim ulang kode dari Google ya!"
        )
        return WAITING_FOR_CODE

    try:
        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id, action="typing"
        )
    except Exception as e:
        logger.debug(f"Failed to send typing chat action: {e}")

    try:
        token_data = exchange_code_for_tokens(auth_code)
        await save_user_token(user_id, token_data)

        await update.message.reply_text(
            "🎉 *Yeay! Google Calendar berhasil terhubung!*\n\n"
            "Sekarang kamu bisa:\n"
            "📅 Tanya jadwal: _\"ada jadwal apa besok?\"_\n"
            "📌 Buat event: _\"ingetin besok jam 3 sore ada meeting\"_\n"
            "📋 Lihat agenda: /agenda\n\n"
            "Semua event akan disimpan langsung ke Google Calendar kamu! 🌸",
            parse_mode="Markdown",
        )
        logger.info(f"User {user_id} successfully connected Google Calendar")

    except Exception as e:
        logger.error(f"Failed to exchange auth code for user {user_id}: {e}")
        err_str = str(e).lower()
        if "invalid_grant" in err_str or "invalid" in err_str:
            msg = (
                "❌ Kode tidak valid atau sudah kedaluwarsa.\n\n"
                "Kode Google hanya bisa dipakai sekali dan berlaku beberapa menit saja.\n"
                "Ketik /connectcalendar lagi untuk mendapatkan link baru ya!"
            )
        else:
            msg = (
                "❌ Gagal menghubungkan Google Calendar 😅\n\n"
                f"Error: `{e}`\n\n"
                "Pastikan kodenya benar dan coba lagi!"
            )
        await update.message.reply_text(msg, parse_mode="Markdown")

    return ConversationHandler.END


async def cancel_connect(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /cancel during the connection flow."""
    await update.message.reply_text(
        "Oke, koneksi Google Calendar dibatalkan. Ketik /connectcalendar kapan saja untuk mencoba lagi 😊"
    )
    return ConversationHandler.END


async def disconnect_calendar_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/disconnectcalendar — Remove user's Google Calendar connection."""
    user_id = update.effective_user.id

    if not await user_exists(user_id):
        await update.message.reply_text("Kamu belum terdaftar! Ketik /start dulu ya 😊")
        return

    if not await is_user_calendar_connected(user_id):
        await update.message.reply_text(
            "Google Calendar kamu belum terhubung kok! 😊\n"
            "Ketik /connectcalendar untuk menghubungkan."
        )
        return

    try:
        await delete_user_token(user_id)
        await update.message.reply_text(
            "🔌 *Google Calendar berhasil diputus.*\n\n"
            "Data kalendermu sudah dihapus dari bot ini.\n"
            "Ketik /connectcalendar untuk menghubungkan kembali kapan saja! 🌸",
            parse_mode="Markdown",
        )
        logger.info(f"User {user_id} disconnected Google Calendar")
    except Exception as e:
        logger.error(f"Failed to disconnect calendar for user {user_id}: {e}")
        await update.message.reply_text("Gagal memutus koneksi. Coba lagi nanti ya!")


def build_connect_calendar_handlers():
    """Return list of handlers for calendar connection flow."""
    # ConversationHandler for the OAuth code flow
    connect_conv = ConversationHandler(
        entry_points=[
            CommandHandler("connectcalendar", connect_calendar_command),
            CommandHandler("hubungkalender", connect_calendar_command),
        ],
        states={
            WAITING_FOR_CODE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_auth_code),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_connect),
        ],
        name="connect_calendar_conv",
        persistent=False,
    )

    disconnect_handler = CommandHandler("disconnectcalendar", disconnect_calendar_command)

    return [connect_conv, disconnect_handler]
