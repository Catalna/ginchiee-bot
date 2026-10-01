"""
config/settings.py
Centralized configuration loader.
"""

import os
from dotenv import load_dotenv

load_dotenv()


# ── Telegram ──────────────────────────────────────────────────────────────────

TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN", "")
if not TELEGRAM_BOT_TOKEN:
    raise KeyError("Neither TELEGRAM_BOT_TOKEN nor BOT_TOKEN is set in environment variables.")

# ── Gemini ────────────────────────────────────────────────────────────────────

GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY") or os.getenv("GEMINI_KEY", "")
if not GEMINI_API_KEY:
    raise KeyError("GEMINI_API_KEY is not set in environment variables.")

GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# ── Database ──────────────────────────────────────────────────────────────────

DATABASE_PATH: str = os.getenv("DATABASE_PATH", "data/ginchiee.db")

# ── Logging ───────────────────────────────────────────────────────────────────

LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

# ── App ───────────────────────────────────────────────────────────────────────

APP_TIMEZONE: str = "Asia/Jakarta"

# ── Personality ───────────────────────────────────────────────────────────────

BOT_NAME: str = "Ginchiee"
BOT_PERSONALITY: str = "playful_supportive"

# ── Nutrition ─────────────────────────────────────────────────────────────────

WATER_TARGET_ML: int = 2500
MAX_CONVERSATION_HISTORY: int = 10
