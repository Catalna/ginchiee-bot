"""
config/settings.py
Centralized configuration loader.
"""

import os
from dotenv import load_dotenv

load_dotenv()


# ── Telegram ──────────────────────────────────────────────────────────────────

TELEGRAM_BOT_TOKEN: str = os.environ["TELEGRAM_BOT_TOKEN"]

# ── Gemini ────────────────────────────────────────────────────────────────────

GEMINI_API_KEY: str = os.environ["GEMINI_API_KEY"]
GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")

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

# ── Google Calendar ───────────────────────────────────────────────────────────

# OAuth2 client secrets file (Desktop app type) dari Google Cloud Console
GOOGLE_OAUTH_CLIENT_SECRETS_FILE: str = os.getenv(
    "GOOGLE_OAUTH_CLIENT_SECRETS_FILE", "config/oauth_client_secrets.json"
)
# Default calendar ID untuk operasi kalender user (biasanya "primary")
GOOGLE_CALENDAR_ID: str = os.getenv("GOOGLE_CALENDAR_ID", "primary")
