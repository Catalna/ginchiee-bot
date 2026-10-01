"""
app.py
Hugging Face Spaces entry point for Ginchiee Bot.
Runs Gradio status dashboard in background and Telegram bot on main thread.
"""

import os
import sys
import logging
import gradio as gr
from main import main as run_telegram_bot

logger = logging.getLogger("app")

with gr.Blocks(title="Ginchiee — AI Diet Companion") as demo:
    with gr.Column():
        gr.Markdown(
            """
            # 🌸 Ginchiee Bot
            ### *AI Health & Diet Companion*
            
            <div style="margin: 15px 0;">
                <span style="background:#22c55e;color:white;padding:4px 14px;border-radius:9999px;font-weight:bold;font-size:14px;">● Online 24/7</span>
            </div>
            
            Ginchiee aktif di Telegram untuk membantu kamu mencatat nutrisi harian, mengelola jadwal kegiatan, dan memberikan rekomendasi porsi adaptif secara pintar! ✨
            
            ---
            
            ### 📱 Cara Menggunakan:
            1. Buka Telegram dan cari bot kamu.
            2. Ketik `/start` untuk memulai kenalan & setup profil.
            3. Kirim foto makanan atau catat dengan bahasa santai (*"tadi makan nasi padang lauk ayam gulai"*).
            4. Ketik `/today` untuk melihat progress nutrisi harian & target adaptif.
            
            ### 🛠️ Daftar Perintah:
            | Perintah | Fungsi |
            | :--- | :--- |
            | `/start` | Mulai & setup profil |
            | `/today` | Progress nutrisi & riwayat hari ini |
            | `/log [makanan]` | Catat makanan via teks |
            | `/agenda` | Lihat daftar kegiatan & jadwal |
            | `/tambahkegiatan` | Tambah agenda kegiatan baru |
            | `/profile` | Lihat target kalori & makro |
            | `/schedule` | Atur jadwal & reminder makan |
            | `/help` | Panduan lengkap |
            """
        )

if __name__ == "__main__":
    logger.info("1. Launching Gradio web dashboard...")
    demo.launch(server_name="0.0.0.0", server_port=7860, prevent_thread_lock=True)
    
    logger.info("2. Launching Telegram Bot on main process...")
    run_telegram_bot()
