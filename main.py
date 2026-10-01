"""
main.py
Entry point for Ginchiee Bot — AI Diet Companion.

Startup sequence:
  1. Load config
  2. Init database & run migrations
  3. Init AI service
  4. Register Telegram handlers
  5. Start APScheduler
  6. Start polling
"""

import asyncio
import logging
import os
import sys
import threading
import warnings
from http.server import HTTPServer, BaseHTTPRequestHandler

# Suppress known non-critical library warnings
warnings.filterwarnings("ignore", category=FutureWarning, module="google.api_core")
try:
    from telegram.warnings import PTBUserWarning
    warnings.filterwarnings("ignore", category=PTBUserWarning)
except ImportError:
    pass

from telegram import BotCommand, Update
from telegram.error import TimedOut, NetworkError
from telegram.ext import Application, ContextTypes
from telegram.request import HTTPXRequest

from config.settings import TELEGRAM_BOT_TOKEN, LOG_LEVEL
from database.connection import init_db, close_db
from database.migrations import run_migrations
from services.ai_service import AIService
from services.scheduler_service import start_scheduler, stop_scheduler


# ── Health Check HTTP Server for Cloud Hosting (HF Spaces / Render) ───────────

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write("Ginchiee Bot is running 24/7! 🌸\n".encode("utf-8"))

    def log_message(self, format, *args):
        pass  # Suppress health check access logs


def start_health_server(port: int = 7860) -> None:
    """Run lightweight HTTP server in a background daemon thread."""
    try:
        server = HTTPServer(("0.0.0.0", port), HealthHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        logging.getLogger(__name__).info(f"Health check HTTP server started on port {port}")
    except Exception as e:
        logging.getLogger(__name__).warning(f"Could not start health check server: {e}")

# ── Handlers ──────────────────────────────────────────────────────────────────
from bot.conversations.onboarding import build_onboarding_conversation
from bot.handlers.profile_handler import build_profile_handlers
from bot.handlers.food_handler import build_food_handlers
from bot.handlers.today_handler import build_today_handlers
from bot.handlers.schedule_handler import build_schedule_handlers
from bot.handlers.activity_handler import build_activity_handlers
from bot.handlers.photo_handler import build_photo_handlers
from bot.handlers.help_handler import build_help_handlers
from bot.handlers.message_handler import build_message_handlers

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
# Silence httpx and httpcore logs to avoid exposing Telegram bot token in request URLs
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


# ── Bot Commands Menu ─────────────────────────────────────────────────────────

BOT_COMMANDS = [
    BotCommand("start",              "Mulai & setup profil"),
    BotCommand("agenda",             "Lihat kegiatan & jadwal kamu"),
    BotCommand("tambahkegiatan",     "Tambah kegiatan baru"),
    BotCommand("hapuskegiatan",      "Hapus kegiatan yang tersimpan"),
    BotCommand("profile",            "Lihat profil & target nutrisimu"),
    BotCommand("setprofile",         "Perbarui profil"),
    BotCommand("today",              "Progress nutrisi hari ini"),
    BotCommand("log",                "Catat makanan yang dimakan"),
    BotCommand("food",               "Alias untuk /log"),
    BotCommand("schedule",           "Atur jadwal makan & reminder"),
    BotCommand("reminders",          "Lihat jadwal makanmu"),
    BotCommand("help",               "Bantuan"),
]


# ── Post-init ─────────────────────────────────────────────────────────────────

async def post_init(application: Application) -> None:
    """Runs after application initializes, before polling starts."""
    # Init database
    await init_db()
    await run_migrations()
    logger.info("Database initialized.")

    # Init AI service
    ai_service = AIService()
    application.bot_data["ai_service"] = ai_service
    logger.info("AI service initialized.")

    # Set bot command menu
    await application.bot.set_my_commands(BOT_COMMANDS)
    logger.info("Bot commands menu set.")

    # Start scheduler
    start_scheduler(bot=application.bot, ai_service=ai_service)
    logger.info("Scheduler started.")


async def post_shutdown(application: Application) -> None:
    """Cleanup on shutdown."""
    stop_scheduler()
    await close_db()
    logger.info("Shutdown complete.")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log errors caused by Updates."""
    if isinstance(context.error, (TimedOut, NetworkError)):
        logger.warning(f"Telegram network issue (timeout/network error): {context.error}")
    else:
        logger.error("Exception while handling an update:", exc_info=context.error)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    logger.info("Starting Ginchiee Bot...")

    # Ensure event loop is active for this thread
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    # Start health check server only if explicitly enabled (e.g. standalone docker)
    if os.getenv("RUN_HEALTH_SERVER") == "1":
        port = int(os.getenv("PORT", "7860"))
        start_health_server(port)

    request = HTTPXRequest(
        connect_timeout=20.0,
        read_timeout=20.0,
        write_timeout=20.0,
        pool_timeout=20.0,
    )

    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .request(request)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    app.add_error_handler(error_handler)

    # ── Register handlers (order matters) ─────────────────────────────────────

    # 1. ConversationHandlers first (they have entry_points that catch /start)
    app.add_handler(build_onboarding_conversation())

    # 2. Schedule conversation handler
    for handler in build_schedule_handlers():
        app.add_handler(handler)

    # 3. Simple command handlers
    for handler in build_profile_handlers():
        app.add_handler(handler)

    for handler in build_food_handlers():
        app.add_handler(handler)

    for handler in build_today_handlers():
        app.add_handler(handler)

    # Activity handlers (replaces Google Calendar)
    for handler in build_activity_handlers():
        app.add_handler(handler)

    for handler in build_help_handlers():
        app.add_handler(handler)

    # Photo handler (multimodal vision)
    for handler in build_photo_handlers():
        app.add_handler(handler)

    # 4. Free-form message handler (must be LAST)
    for handler in build_message_handlers():
        app.add_handler(handler)

    # ── Start polling ──────────────────────────────────────────────────────────
    logger.info("Bot is running! Press Ctrl+C to stop.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
