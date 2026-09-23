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
import sys

from telegram import BotCommand, Update
from telegram.error import TimedOut, NetworkError
from telegram.ext import Application, ContextTypes
from telegram.request import HTTPXRequest

from config.settings import TELEGRAM_BOT_TOKEN, LOG_LEVEL
from database.connection import init_db, close_db
from database.migrations import run_migrations
from services.ai_service import AIService
from services.scheduler_service import start_scheduler, stop_scheduler

# ── Handlers ──────────────────────────────────────────────────────────────────
from bot.conversations.onboarding import build_onboarding_conversation
from bot.handlers.profile_handler import build_profile_handlers
from bot.handlers.food_handler import build_food_handlers
from bot.handlers.today_handler import build_today_handlers
from bot.handlers.schedule_handler import build_schedule_handlers
from bot.handlers.calendar_handler import build_calendar_handlers
from bot.handlers.connect_calendar_handler import build_connect_calendar_handlers
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
logger = logging.getLogger(__name__)


# ── Bot Commands Menu ─────────────────────────────────────────────────────────

BOT_COMMANDS = [
    BotCommand("start",              "Mulai & setup profil"),
    BotCommand("agenda",             "Lihat jadwal & agenda Google Calendar"),
    BotCommand("connectcalendar",    "Hubungkan Google Calendar kamu"),
    BotCommand("disconnectcalendar", "Putus koneksi Google Calendar"),
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

    for handler in build_calendar_handlers():
        app.add_handler(handler)

    # Calendar OAuth2 connect/disconnect handlers
    for handler in build_connect_calendar_handlers():
        app.add_handler(handler)

    for handler in build_help_handlers():
        app.add_handler(handler)

    # 4. Free-form message handler (must be LAST)
    for handler in build_message_handlers():
        app.add_handler(handler)

    # ── Start polling ──────────────────────────────────────────────────────────
    logger.info("Bot is running! Press Ctrl+C to stop.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
