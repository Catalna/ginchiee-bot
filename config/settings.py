"""
config/settings.py
Centralized configuration loader.

Every required credential is validated at import time so the process fails
loudly on startup instead of silently behaving differently in production.
"""

import os

from dotenv import load_dotenv

load_dotenv()


def _require(name: str, *aliases: str) -> str:
    """Return the first non-empty env var among name and aliases."""
    for candidate in (name, *aliases):
        value = os.getenv(candidate)
        if value:
            return value
    raise KeyError(
        f"Neither {name} nor any of its aliases {aliases} is set in the environment. "
        "Set it in .env (local) or as a Space Secret (Hugging Face)."
    )


# ── Telegram ──────────────────────────────────────────────────────────────────

TELEGRAM_BOT_TOKEN: str = _require("TELEGRAM_BOT_TOKEN", "BOT_TOKEN")

# ── Gemini ───────────────────────────────────────────────────────────────────

GEMINI_API_KEY: str = _require("GEMINI_API_KEY", "GEMINI_KEY")

GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-flash-lite-latest")

# Generation parameters. The defaults previously produced flat, formal,
# over-long replies; these bias the model toward short, warm, chatty output.
AI_TEMPERATURE: float = float(os.getenv("AI_TEMPERATURE", "0.95"))
AI_TOP_P: float = float(os.getenv("AI_TOP_P", "0.95"))
AI_MAX_OUTPUT_TOKENS: int = int(os.getenv("AI_MAX_OUTPUT_TOKENS", "220"))
AI_MAX_RETRIES: int = int(os.getenv("AI_MAX_RETRIES", "1"))

# ── Database ──────────────────────────────────────────────────────────────────

# Postgres connection string, e.g.
# postgresql://user:password@host/dbname?sslmode=require
DATABASE_URL: str = _require("DATABASE_URL")

# ── Logging ───────────────────────────────────────────────────────────────────

LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

# ── App ───────────────────────────────────────────────────────────────────────

APP_TIMEZONE: str = "Asia/Jakarta"

# Optional allow-list of Telegram user ids. When set, only these users may talk
# to the bot; leave empty to allow everyone.
_ALLOWED_IDS = os.getenv("ALLOWED_USER_IDS", "").strip()
ALLOWED_USER_IDS: frozenset[int] = (
    frozenset(int(part.strip()) for part in _ALLOWED_IDS.split(",") if part.strip())
    if _ALLOWED_IDS
    else frozenset()
)

# ── Personality ───────────────────────────────────────────────────────────────

BOT_NAME: str = "Ginchiee"
BOT_PERSONALITY: str = "playful_supportive"

# Style guardrails applied to every user-facing reply.
MAX_REPLY_SENTENCES: int = 3
MAX_REPLY_EMOJI: int = 2
MAX_REPLY_CHARS: int = 450

# ── Nutrition ─────────────────────────────────────────────────────────────────

WATER_TARGET_ML: int = 2500
MAX_CONVERSATION_HISTORY: int = 10
