"""
bot/handlers/help_handler.py
/help — Show all available commands.
"""

import logging
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

logger = logging.getLogger(__name__)

HELP_TEXT = """
🤖 *Ginchiee — AI Companion & Health Partner*

*📋 Perintah Tersedia:*

/start — Mulai & setup profil
/profile — Lihat profil & target nutrisimu
/setprofile — Perbarui profil
/today — Lihat progress nutrisi hari ini
/log [makanan] — Catat makanan yang dimakan
/food [makanan] — Alias untuk /log
/schedule — Atur jadwal makan & reminder
/reminders — Lihat jadwal makanmu
/agenda — Lihat kegiatan & jadwal kamu
/jadwal — Alias untuk /agenda
/tambahkegiatan [deskripsi] — Tambah kegiatan baru
/hapuskegiatan — Hapus kegiatan yang tersimpan
/help — Tampilkan bantuan ini

*💬 Ngobrol Bebas:*
Kamu juga bisa langsung ngobrol sama aku!
Ceritain apa yang kamu makan, curhat, atau minta aku ingetin kegiatan~

*📸 Kirim Foto Langsung:*
• Foto makanan → Aku langsung scan & hitung nutrisinya!
• Foto poster / flyer acara → Aku catat ke agendamu!
• Foto bebas → Ngobrol seru bareng aku!

*📝 Contoh Log Makanan:*
• Kirim foto makanan langsung 📸
• `/log nasi 200g ayam 150g`
• Atau langsung ketik: _"tadi aku makan nasi goreng"_

*📅 Contoh Tambah Kegiatan:*
• Kirim foto poster/undangan acara 📸
• `/tambahkegiatan besok jam 9 ada rapat`
• Atau langsung ketik: _"ingetin aku besok jam 3 sore kontrol dokter"_

*💡 Tips:*
Aku bisa baca teks & foto natural tanpa format khusus!
Aku juga akan ingatkan kamu sebelum kegiatan berlangsung 🔔

_Ginchiee adalah AI companion, bukan dokter. Untuk masalah kesehatan serius, konsultasikan ke dokter atau ahli gizi ya!_ 🙏
"""


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")


def build_help_handlers():
    return [CommandHandler("help", help_command)]
