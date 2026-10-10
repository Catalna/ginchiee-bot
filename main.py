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

import logging
import os
import sys
import threading
import warnings
from http.server import BaseHTTPRequestHandler, HTTPServer

# Suppress known non-critical library warnings
warnings.filterwarnings("ignore", category=FutureWarning, module="google.api_core")
try:
    from telegram.warnings import PTBUserWarning
    warnings.filterwarnings("ignore", category=PTBUserWarning)
except ImportError:
    pass

from telegram import BotCommand, Update
from telegram.error import NetworkError, TimedOut
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)
from telegram.request import HTTPXRequest

from config.settings import (
    ALLOWED_USER_IDS,
    GEMINI_MODEL,
    LOG_LEVEL,
    TELEGRAM_BOT_TOKEN,
)
from database.connection import close_db, init_db
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
from bot.handlers.activity_handler import build_activity_handlers
from bot.handlers.food_handler import build_food_handlers
from bot.handlers.help_handler import build_help_handlers
from bot.handlers.message_handler import (
    handle_confirm_callback,
    handle_done_callback,
    handle_message,
)
from bot.handlers.photo_handler import build_photo_handlers
from bot.handlers.profile_handler import build_profile_handlers
from bot.handlers.schedule_handler import build_schedule_handlers
from bot.handlers.today_handler import build_today_handlers

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
    try:
        logger.info("Initializing database...")
        await init_db()
        await run_migrations()
        logger.info("Database initialized.")
    except Exception as e:
        logger.critical(f"FATAL: Database initialization failed: {e}", exc_info=True)
        raise

    try:
        # Init AI service
        ai_service = AIService()
        application.bot_data["ai_service"] = ai_service
        logger.info(
            "AI service initialized (model=%s, temp=%.2f, max_tokens=%d).",
            GEMINI_MODEL, ai_service._generation_config.temperature,
            ai_service._generation_config.max_output_tokens,
        )
    except Exception as e:
        logger.critical(f"FATAL: AI service initialization failed: {e}", exc_info=True)
        raise

    try:
        # Set bot command menu
        await application.bot.set_my_commands(BOT_COMMANDS)
        logger.info("Bot commands menu set.")
    except Exception as e:
        logger.warning(f"Could not set bot commands: {e}")

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

async def is_allowed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Gate every update behind the optional ALLOWED_USER_IDS allow-list.

    When the list is empty (the default) everybody is allowed, which keeps local
    development friction-free. Set ALLOWED_USER_IDS to a comma-separated list of
    Telegram user ids to restrict the bot to just those people.
    """
    if not ALLOWED_USER_IDS:
        return True

    user = update.effective_user
    if user is None:
        return False

    if user.id in ALLOWED_USER_IDS:
        return True

    logger.warning(
        "Blocked update from unauthorised user %s (@%s)",
        user.id, user.username or "?",
    )
    try:
        await update.effective_message.reply_text(
            "maaf, bot ini belum terbuka untuk publik ya"
        )
    except Exception:
        pass
    return False


# Backwards-compatible helper for callers that want to gate a single handler.
async def guarded(handler):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await is_allowed(update, context):
            await handler(update, context)

    return wrapper


async def _allow_list_gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reject updates from users outside ALLOWED_USER_IDS.

    Registered with `TypeHandler` at group -1 so it runs before every real
    handler; raising ApplicationHandlerStop stops all further processing.
    """
    if not await is_allowed(update, context):
        raise ApplicationHandlerStop()


def build_app() -> Application:
    """Build and configure the Telegram Application with all handlers."""
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

    # Allow-list first, before any real handler (no-op when the list is empty).
    app.add_handler(TypeHandler(Update, _allow_list_gate), group=-1)

    # ── Register handlers (order matters) ─────────────────────────────────────
    app.add_handler(build_onboarding_conversation())

    for handler in build_schedule_handlers():
        app.add_handler(handler)

    for handler in build_profile_handlers():
        app.add_handler(handler)

    for handler in build_food_handlers():
        app.add_handler(handler)

    for handler in build_today_handlers():
        app.add_handler(handler)

    for handler in build_activity_handlers():
        app.add_handler(handler)

    for handler in build_help_handlers():
        app.add_handler(handler)

    for handler in build_photo_handlers():
        app.add_handler(handler)

    # Inline buttons: confirmations for data-changing actions, plus the
    # per-activity done/delete buttons.
    app.add_handler(CallbackQueryHandler(handle_confirm_callback, pattern=r"^confirm:"))
    app.add_handler(CallbackQueryHandler(handle_done_callback, pattern=r"^done:"))

    # Free-form text is handled by the intent router.
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    return app


def main() -> None:
    """CLI entry point (also used by the Docker image)."""
    logger.info("Starting Ginchiee Bot...")

    # The health endpoint makes the container pass platform liveness checks
    # (HF Spaces / Render / plain `docker run`).
    try:
        port = int(os.getenv("PORT", "7860"))
    except ValueError:
        port = 7860
    start_health_server(port)

    app = build_app()
    logger.info("Bot is running! Press Ctrl+C to stop.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()



