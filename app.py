"""
app.py
Hugging Face Spaces entry point for Ginchiee Bot.
Runs Gradio status dashboard and background Telegram bot worker.
"""

import os
import sys
import time
import asyncio
import logging
import threading
from datetime import datetime
import gradio as gr
from main import run_bot_async

logger = logging.getLogger("app")


def start_bot_worker():
    logger.info("Initializing Telegram bot in async worker thread...")
    try:
        asyncio.run(run_bot_async())
    except Exception as e:
        logger.error(f"FATAL: Telegram bot async loop crashed: {e}", exc_info=True)


# Start Telegram bot in background daemon thread
bot_thread = threading.Thread(target=start_bot_worker, daemon=True)
bot_thread.start()


def get_live_status():
    is_alive = bot_thread.is_alive()
    status = "🟢 AKTIF (Polling Telegram 24/7)" if is_alive else "🔴 Offline"
    return f"Status: {status} | Waktu Server: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"


with gr.Blocks(title="Ginchiee — AI Diet Companion") as demo:
    with gr.Column():
        gr.Markdown(
            """
            # 🌸 Ginchiee Bot
            ### *AI Health & Diet Companion*
            
            Ginchiee aktif di Telegram untuk membantu kamu mencatat nutrisi harian, mengelola jadwal kegiatan, dan memberikan rekomendasi porsi adaptif secara pintar! ✨
            """
        )

        status_display = gr.Textbox(
            value=get_live_status,
            label="Live Bot Status",
            interactive=False,
        )

        refresh_btn = gr.Button("🔄 Cek Status Bot", variant="primary")
        refresh_btn.click(fn=get_live_status, outputs=status_display)

        gr.Markdown(
            """
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
    demo.launch(server_name="0.0.0.0", server_port=7860, show_error=True)
    while True:
        time.sleep(3600)
