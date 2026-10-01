"""
bot/handlers/today_handler.py
/today — Show daily nutrition summary, 7-day history, and adaptive target.
"""

import logging
from datetime import datetime
import pytz
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from config.settings import APP_TIMEZONE
from services.nutrition_service import (
    get_daily_progress,
    get_today_food_logs,
    get_nutrition_history,
    get_adaptive_target,
)
from services.user_service import user_exists, has_diet_profile

logger = logging.getLogger(__name__)

_TZ = pytz.timezone(APP_TIMEZONE)

INDONESIAN_DAYS = {
    "Monday": "Sen", "Tuesday": "Sel", "Wednesday": "Rab",
    "Thursday": "Kam", "Friday": "Jum", "Saturday": "Sab", "Sunday": "Min",
}


def _progress_bar(pct: int, length: int = 10) -> str:
    filled = min(int(pct / 100 * length), length)
    bar = "█" * filled + "░" * (length - filled)
    return f"[{bar}] {pct}%"


def _trend_emoji(calories: float, target: float) -> str:
    """Return emoji indicating if that day was over/under/on target."""
    ratio = calories / target if target else 0
    if ratio > 1.10:
        return "🔴"  # significantly over
    elif ratio > 1.03:
        return "🟡"  # slightly over
    elif ratio < 0.70:
        return "⚪"  # very low (might be incomplete log)
    elif ratio < 0.97:
        return "🔵"  # slightly under
    else:
        return "🟢"  # on target


async def today_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id

    if not await user_exists(user_id):
        await update.message.reply_text("Kamu belum terdaftar! Ketik /start dulu 😊")
        return

    if not await has_diet_profile(user_id):
        await update.message.reply_text(
            "Profil dietmu belum lengkap 😅\nKetik /start untuk setup profil!"
        )
        return

    try:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    except Exception as e:
        logger.debug(f"Failed to send typing chat action: {e}")

    progress = await get_daily_progress(user_id)
    food_logs = await get_today_food_logs(user_id)
    history = await get_nutrition_history(user_id, days=7)
    adaptive = await get_adaptive_target(user_id)

    now = datetime.now(_TZ)
    today_str = now.strftime("%A, %d %B %Y")

    if not progress:
        await update.message.reply_text("Gagal mengambil data progress. Coba lagi nanti!")
        return

    t = progress["target"]
    c = progress["consumed"]
    p = progress["percentage"]

    # ── Active target: use adaptive if available and has data ──────────────────
    show_adaptive = adaptive and adaptive.get("days_analysed", 0) >= 2
    if show_adaptive:
        active_t = adaptive
        adj = adaptive["adjustment_kcal"]
        adj_sign = "+" if adj >= 0 else ""
        trend_label = adaptive.get("trend_label", "")
    else:
        active_t = t

    # Progress bars (vs active target)
    cal_bar   = _progress_bar(p["calories"])
    prot_bar  = _progress_bar(p["protein_g"])
    carbs_bar = _progress_bar(p["carbs_g"])
    fat_bar   = _progress_bar(p["fat_g"])

    # ── Today section ──────────────────────────────────────────────────────────
    lines = [
        f"📊 *Progress Hari Ini*",
        f"_{today_str}_\n",
    ]

    if show_adaptive:
        lines.append(
            f"🎯 *Target Adaptif:* {active_t['calories']} kcal "
            f"(`basis {active_t['base_calories']} kcal, {adj_sign}{adj} kcal`)\n"
            f"_📈 Tren {adaptive['days_analysed']} hari terakhir: {trend_label}_\n"
        )
    else:
        lines.append(f"🎯 *Target:* {t['calories']} kcal\n")

    lines += [
        f"🔥 *Kalori:* {c['calories']:.0f} / {active_t['calories']} kcal",
        f"`{cal_bar}`\n",
        f"🥩 *Protein:* {c['protein_g']:.0f} / {active_t['protein_g']}g",
        f"`{prot_bar}`\n",
        f"🍚 *Karbs:* {c['carbs_g']:.0f} / {active_t['carbs_g']}g",
        f"`{carbs_bar}`\n",
        f"🥑 *Lemak:* {c['fat_g']:.0f} / {active_t['fat_g']}g",
        f"`{fat_bar}`",
    ]

    # ── Food log list ──────────────────────────────────────────────────────────
    if food_logs:
        lines.append("\n🍽️ *Makanan Hari Ini:*")
        for log in food_logs:
            meal_emoji = {
                "breakfast": "🌅",
                "lunch": "☀️",
                "snack": "🍎",
                "dinner": "🌙",
            }.get(log.get("meal_type", ""), "•")
            lines.append(
                f"{meal_emoji} {log['food_name'].capitalize()} "
                f"({log['amount_g']:.0f}g) — {log['calories']:.0f} kcal"
            )
    else:
        lines.append(
            "\n_Belum ada makanan yang dicatat hari ini._\n"
            "_Gunakan /log untuk mencatat!_"
        )

    # ── 7-day history ─────────────────────────────────────────────────────────
    if history:
        base_cal = t["calories"]
        lines.append("\n\n📅 *Riwayat 7 Hari Terakhir:*")
        for h in history:
            try:
                d = datetime.strptime(h["date"], "%Y-%m-%d")
                day_short = INDONESIAN_DAYS.get(d.strftime("%A"), d.strftime("%a"))
                date_label = f"{day_short} {d.day}/{d.month}"
            except Exception:
                date_label = h["date"]

            emoji = _trend_emoji(h["calories"], base_cal)
            surplus = h["calories"] - base_cal
            surplus_str = f"+{surplus:.0f}" if surplus >= 0 else f"{surplus:.0f}"
            lines.append(
                f"{emoji} *{date_label}*: {h['calories']:.0f} kcal ({surplus_str})"
            )

        lines.append(
            "\n_🟢 sesuai target  🟡 sedikit lebih  🔴 jauh lebih  🔵 kurang  ⚪ data minim_"
        )
    else:
        lines.append("\n_Belum ada riwayat makan sebelumnya._")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


def build_today_handlers():
    return [CommandHandler("today", today_command)]
