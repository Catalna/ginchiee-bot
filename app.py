"""
app.py
Hugging Face Spaces entry point for Ginchiee Bot.
Runs a minimal Python HTTP server on port 7860 to keep the Space alive,
while running the Telegram bot in the background via asyncio.
"""

import os
import sys
import time
import asyncio
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime

import spaces

from main import build_app

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("app")


@spaces.GPU
def zero_gpu_runtime_probe() -> None:
    """Register this Gradio Space as ZeroGPU-compatible without reserving a GPU."""


# ── Telegram Bot Worker ────────────────────────────────────────────────────────
def start_bot_worker():
    logger.info("Starting Telegram bot worker thread...")
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        app = build_app()
        app.run_polling(stop_signals=None, drop_pending_updates=False)
    except Exception as e:
        logger.error(f"FATAL: Telegram bot crashed: {e}", exc_info=True)


# ── Minimal HTTP Server (Keeps HF Space alive) ────────────────────────────────
START_TIME = datetime.now()

class StatusHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        uptime = datetime.now() - START_TIME
        hours, rem = divmod(int(uptime.total_seconds()), 3600)
        mins, secs = divmod(rem, 60)

        html = f"""<!DOCTYPE html>
<html lang="id">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="refresh" content="30">
    <title>Ginchiee Bot Status</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: 'Segoe UI', sans-serif; background: #0f172a; color: #e2e8f0; display: flex; justify-content: center; align-items: center; min-height: 100vh; }}
        .card {{ background: #1e293b; border-radius: 20px; padding: 40px; max-width: 600px; width: 90%; box-shadow: 0 20px 60px rgba(0,0,0,0.5); text-align: center; }}
        .emoji {{ font-size: 64px; margin-bottom: 16px; }}
        h1 {{ font-size: 32px; font-weight: 700; color: #f8fafc; margin-bottom: 8px; }}
        .subtitle {{ color: #94a3b8; margin-bottom: 30px; }}
        .badge {{ display: inline-block; background: #22c55e; color: white; padding: 6px 20px; border-radius: 9999px; font-weight: 700; font-size: 16px; margin-bottom: 30px; }}
        .stats {{ background: #0f172a; border-radius: 12px; padding: 20px; margin: 20px 0; text-align: left; }}
        .stat {{ display: flex; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid #1e293b; color: #94a3b8; }}
        .stat:last-child {{ border-bottom: none; }}
        .stat-val {{ color: #e2e8f0; font-weight: 600; }}
        .note {{ font-size: 12px; color: #475569; margin-top: 20px; }}
    </style>
</head>
<body>
    <div class="card">
        <div class="emoji">🌸</div>
        <h1>Ginchiee Bot</h1>
        <p class="subtitle">AI Health & Diet Companion</p>
        <div class="badge">● Online 24/7</div>
        <div class="stats">
            <div class="stat"><span>Status</span><span class="stat-val">🟢 Aktif & Polling Telegram</span></div>
            <div class="stat"><span>Uptime</span><span class="stat-val">{hours}j {mins}m {secs}d</span></div>
            <div class="stat"><span>Server Time</span><span class="stat-val">{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</span></div>
            <div class="stat"><span>Bot Thread</span><span class="stat-val">{'🟢 Alive' if bot_thread.is_alive() else '🔴 Stopped'}</span></div>
        </div>
        <p class="note">Halaman ini auto-refresh setiap 30 detik · Dibuat dengan ❤️ untuk menjaga Space tetap hidup</p>
    </div>
</body>
</html>"""

        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass  # suppress access logs


if __name__ == "__main__":
    # 1. Launch Telegram bot in background thread
    bot_thread = threading.Thread(target=start_bot_worker, daemon=True)
    bot_thread.start()

    # 2. Start HTTP server on main thread (blocks forever — keeps HF Space alive)
    port = int(os.getenv("PORT", "7860"))
    logger.info(f"Starting status HTTP server on port {port}...")
    server = HTTPServer(("0.0.0.0", port), StatusHandler)
    logger.info(f"Ginchiee status page live at http://0.0.0.0:{port} 🌸")
    server.serve_forever()
