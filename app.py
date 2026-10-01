"""
app.py
Hugging Face Spaces entry point for Ginchiee Bot.
Runs the Telegram bot in background and serves a Gradio status dashboard.
"""

import os
import threading
import gradio as gr
from main import main as run_telegram_bot

# Start Telegram Bot in a background daemon thread
def start_bot_thread():
    try:
        run_telegram_bot()
    except Exception as e:
        print(f"Bot execution error: {e}")

bot_thread = threading.Thread(target=start_bot_thread, daemon=True)
bot_thread.start()

# Gradio Web Dashboard for Hugging Face
custom_css = """
.container { max-width: 800px; margin: auto; padding: 20px; }
.header { text-align: center; margin-bottom: 20px; }
.status-badge { display: inline-block; background: #22c55e; color: white; padding: 4px 12px; border-radius: 9999px; font-weight: bold; }
"""

with gr.Blocks(title="Ginchiee — AI Diet Companion", css=custom_css, theme=gr.themes.Soft()) as demo:
    with gr.Column(elem_classes="container"):
        gr.Markdown(
            """
            # 🌸 Ginchiee Bot
            ### *AI Health & Diet Companion*
            
            <div style="margin: 15px 0;">
                <span class="status-badge">● Online 24/7</span>
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
    demo.launch(server_name="0.0.0.0", server_port=7860)
