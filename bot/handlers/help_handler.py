"""
bot/handlers/help_handler.py
/help — Show all available commands.
"""

import logging
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

logger = logging.getLogger(__name__)

HELP_TEXT = """
🤖 *Ginchiee — AI Diet Companion*

*📋 Perintah Tersedia:*

/start — Mulai & setup profil
/profile — Lihat profil & target nutrisimu
/setprofile — Perbarui profil
/today — Lihat progress nutrisi hari ini
/log [makanan] — Catat makanan yang dimakan
/food [makanan] — Alias untuk /log
/schedule — Atur jadwal makan & reminder
/reminders — Lihat jadwal makanmu
/help — Tampilkan bantuan ini

*💬 Ngobrol Bebas:*
Kamu juga bisa langsung ngobrol sama aku!
Ceritain apa yang kamu makan, tanya soal diet, atau sekedar curhat soal pola makan~

*📝 Contoh Log Makanan:*
• `/log nasi 200g ayam 150g`
• `/log makan siang: bakso 1 porsi`
• `/log telur rebus 2 butir dan susu 1 gelas`

*💡 Tips:*
Kamu bisa nulis natural tanpa format khusus, aku akan bantu parsing-nya!

_Ginchiee adalah AI companion, bukan dokter. Untuk masalah kesehatan serius, konsultasikan ke dokter atau ahli gizi ya!_ 🙏
"""


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")


def build_help_handlers():
    return [CommandHandler("help", help_command)]
