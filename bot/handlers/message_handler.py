"""
bot/handlers/message_handler.py
Handles free-form text messages (natural language conversation).
Auto-detects food logging intent vs. general conversation.
"""

import logging
from datetime import datetime
from telegram import Update
from telegram.ext import ContextTypes, MessageHandler, filters

from services.user_service import (
    get_user,
    get_diet_profile,
    get_meal_schedules,
    save_conversation,
    get_recent_conversations,
    user_exists,
)
from services.nutrition_service import (
    get_daily_consumed,
    get_nutrition_target,
    get_today_food_logs,
    log_food_from_text,
    get_daily_progress,
    get_nutrition_history,
    get_adaptive_target,
)
from services.activity_service import (
    add_activity,
    get_upcoming_activities,
    get_all_user_activities,
    delete_activity,
    parse_activity_from_text,
    parse_cancel_from_text,
)

logger = logging.getLogger(__name__)

# Keywords that suggest food logging intent
FOOD_LOG_KEYWORDS = [
    "makan", "minum", "habis makan", "baru makan", "udah makan", "lg makan", "lagi makan",
    "sarapan", "lunch", "dinner", "snack", "cemilan", "ngemil", "sarap",
    "nasi", "ayam", "telur", "ikan", "tempe", "tahu", "daging", "sapi", "kambing", "bebek",
    "mie", "bihun", "kwetiau", "pasta", "spaghetti", "roti", "susu", "kopi", "teh", "jus",
    "buah", "sayur", "pizza", "burger", "bakso", "soto", "sate", "rendang", "gulai",
    "sambal", "sambel", "pepes", "opor", "rawon", "pecel", "geprek", "penyet",
    "udang", "cumi", "kepiting", "seafood", "martabak", "gorengan", "kentang",
    "gw makan", "aku makan", "saya makan", "tadi makan", "mkn",
    "gram", "100g", "150g", "200g", "250g", "porsi", "potong", "butir", "mangkok", "mangkuk", "piring", "gelas", "sendok", "sdm",
]


def _looks_like_food_log(text: str) -> bool:
    """Heuristic: does this message describe eating something?"""
    text_lower = text.lower()
    return any(kw in text_lower for kw in FOOD_LOG_KEYWORDS)


def _looks_like_cancel_intent(text: str) -> bool:
    """Fast pre-filter: does this message likely express a cancellation?"""
    text_lower = text.lower()
    cancel_keywords = [
        "ga jadi", "gak jadi", "tidak jadi", "batal", "batalin", "batalkan",
        "cancel", "cancelled", "dicancel", "dibatalkan", "hapus", "hapusin",
        "ga ada", "gak ada", "tidak ada", "ga datang", "gak datang",
        "ga hadir", "gak hadir", "ga bisa", "gak bisa",
    ]
    return any(kw in text_lower for kw in cancel_keywords)


async def _build_chat_context(user_id: int) -> dict:
    """Build context dict for AI service."""
    user = await get_user(user_id)
    profile = await get_diet_profile(user_id)
    target = await get_nutrition_target(user_id)
    consumed = await get_daily_consumed(user_id)
    food_logs = await get_today_food_logs(user_id)
    schedules = await get_meal_schedules(user_id)
    upcoming_activities = await get_upcoming_activities(user_id, days=7)
    nutrition_history = await get_nutrition_history(user_id, days=7)
    adaptive_target = await get_adaptive_target(user_id)

    return {
        "profile": {
            **dict(profile),
            "name": user["name"] if user else "User",
        } if profile else {"name": user["name"] if user else "User"},
        "target": {
            "calories":  target.calories,
            "protein_g": target.protein_g,
            "carbs_g":   target.carbs_g,
            "fat_g":     target.fat_g,
        } if target else None,
        "consumed": consumed,
        "food_logs": food_logs[-5:] if food_logs else [],
        "schedules": schedules,
        "upcoming_activities": upcoming_activities,
        "nutrition_history": nutrition_history,
        "adaptive_target": adaptive_target,
    }


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Main message handler for free-form text."""
    user_id = update.effective_user.id
    text = update.message.text.strip()

    if not text:
        return

    # Check if registered
    if not await user_exists(user_id):
        await update.message.reply_text(
            "Halo! 👋 Kayaknya kita belum kenalan nih~\n"
            "Ketik /start untuk mulai ya! 😊"
        )
        return

    ai_service = context.bot_data["ai_service"]

    # Typing indicator (silent fail if network is slow/timeout)
    try:
        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id, action="typing"
        )
    except Exception as e:
        logger.debug(f"Failed to send typing chat action: {e}")

    # 1. Auto-detect food logging intent (fast keyword pre-filter)
    if _looks_like_food_log(text):
        await _handle_possible_food_log(update, context, user_id, text, ai_service)
        return

    # 2. Fast pre-filter: cancellation intent → ask AI to confirm & match
    if _looks_like_cancel_intent(text):
        handled = await _handle_possible_cancel(update, context, user_id, text, ai_service)
        if handled:
            return

    # 3. Try AI-powered activity detection (add or view)
    handled = await _handle_possible_activity(update, context, user_id, text, ai_service)
    if handled:
        return

    # 4. General AI conversation
    await _handle_general_chat(update, context, user_id, text, ai_service)


async def _handle_possible_activity(
    update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, text: str, ai_service
) -> bool:
    """
    Use AI to determine if the message is:
    - A request to ADD a new activity → save and confirm
    - A request to VIEW the agenda → show agenda
    Returns True if handled, False to fall through to general chat.
    """
    from bot.handlers.activity_handler import agenda_command

    text_lower = text.lower().strip()

    # Fast-path: pure view commands (exact matches or obvious view-only phrases)
    view_only_phrases = [
        "agenda", "jadwal", "lihat jadwal", "lihat agenda", "cek agenda", "cek jadwal",
        "jadwal hari ini", "agenda hari ini", "kegiatan hari ini", "jadwal besok", "agenda besok",
        "agenda minggu ini", "jadwal minggu ini", "kegiatan minggu ini",
        "ada agenda apa", "ada jadwal apa", "ada acara apa", "daftar agenda", "daftar kegiatan",
        "ada kegiatan apa", "kegiatan apa", "jadwalku", "agendaku",
    ]
    view_prefixes = ["lihat ", "liat ", "cek ", "tampilkan ", "daftar "]
    is_pure_view = (
        text_lower in view_only_phrases
        or any(text_lower.startswith(p) and any(n in text_lower for n in ["agenda", "jadwal", "kegiatan", "acara"]) for p in view_prefixes)
    )

    if is_pure_view:
        await agenda_command(update, context)
        return True

    # Ask AI to decide: is this an ADD request?
    parsed = await parse_activity_from_text(text, ai_service)
    if parsed:
        activity_id = await add_activity(
            user_id=user_id,
            title=parsed["title"],
            activity_dt=parsed["activity_dt"],
            description=parsed.get("description", ""),
            remind_mins=parsed.get("remind_mins", 30),
        )

        # Format datetime nicely
        dt_str = parsed["activity_dt"]
        try:
            dt_obj = datetime.fromisoformat(parsed["activity_dt"])
            day_names = {
                "Monday": "Senin", "Tuesday": "Selasa", "Wednesday": "Rabu",
                "Thursday": "Kamis", "Friday": "Jumat", "Saturday": "Sabtu", "Sunday": "Minggu",
            }
            month_names = {
                1: "Januari", 2: "Februari", 3: "Maret", 4: "April", 5: "Mei", 6: "Juni",
                7: "Juli", 8: "Agustus", 9: "September", 10: "Oktober", 11: "November", 12: "Desember",
            }
            day_id = day_names.get(dt_obj.strftime("%A"), dt_obj.strftime("%A"))
            month_id = month_names.get(dt_obj.month, dt_obj.strftime("%B"))
            dt_str = f"{day_id}, {dt_obj.day} {month_id} {dt_obj.year} • {dt_obj.strftime('%H:%M')}"
        except Exception:
            dt_str = parsed["activity_dt"][:16].replace("T", " ")

        remind = parsed.get("remind_mins", 30)
        desc = parsed.get("description", "")
        desc_line = f"📝 _{desc}_\n" if desc else ""

        reply_msg = (
            f"✅ *Oke, sudah aku simpan ya!*\n\n"
            f"📌 *{parsed['title']}*\n"
            f"{desc_line}"
            f"🕒 {dt_str} WIB\n"
            f"⏰ Aku akan ingatkan {remind} menit sebelumnya 🔔\n\n"
            f"_Ketik /agenda untuk melihat semua jadwalmu!_ 🌸"
        )

        await update.message.reply_text(reply_msg, parse_mode="Markdown")
        await save_conversation(user_id, "user", text)
        await save_conversation(user_id, "model", reply_msg)
        return True

    # AI said no activity intent → fall through
    return False


async def _handle_possible_cancel(
    update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, text: str, ai_service
) -> bool:
    """
    Use AI to detect natural language cancellation/deletion intent.
    e.g. "aku ga jadi ngedate", "cancel meeting besok", "acara ku dicancel".
    Returns True if handled, False to fall through.
    """
    # Fetch user's upcoming activities to give AI context
    user_activities = await get_all_user_activities(user_id, limit=20)
    if not user_activities:
        return False

    cancel_result = await parse_cancel_from_text(text, ai_service, user_activities)
    if not cancel_result:
        return False

    activity_id = cancel_result["activity_id"]
    title = cancel_result["title"]

    # Verify the activity belongs to this user before deleting
    deleted = await delete_activity(activity_id, user_id)
    if deleted:
        reply_msg = (
            f"✅ Oke, jadwal *{title}* sudah aku hapus dari agendamu ya! 🗑️\n\n"
            f"_Kalau berubah pikiran, ketik lagi acaranya dan aku akan simpan ulang~ 😊_"
        )
    else:
        reply_msg = (
            f"Hmm, aku nggak nemu kegiatan *{title}* di jadwalmu 😅\n"
            f"Mungkin sudah dihapus sebelumnya? Cek /agenda dulu ya!"
        )

    await update.message.reply_text(reply_msg, parse_mode="Markdown")
    await save_conversation(user_id, "user", text)
    await save_conversation(user_id, "model", reply_msg)
    return True

async def _handle_possible_food_log(
    update, context, user_id: int, text: str, ai_service
) -> None:
    """
    Try to parse and log food from a natural language message.
    If food items found → log them.
    If not → treat as conversation.
    """
    from services.nutrition_service import log_food_from_text, get_daily_progress
    from services.user_service import has_diet_profile

    # Save user message to history first
    await save_conversation(user_id, "user", text)

    logged_items = await log_food_from_text(
        user_id=user_id,
        text=text,
        ai_service=ai_service,
    )

    if logged_items:
        # Respond as food log
        has_profile = await has_diet_profile(user_id)
        progress = await get_daily_progress(user_id) if has_profile else None
        adaptive_target = await get_adaptive_target(user_id) if has_profile else None
        history = await get_nutrition_history(user_id, days=5) if has_profile else []

        response = await ai_service.generate_food_response(
            logged_items=logged_items,
            daily_progress=progress,
            adaptive_target=adaptive_target,
            nutrition_history=history,
        )

        # Show quick summary
        food_summary = "✅ *Aku catat ya!*\n"
        for item in logged_items:
            food_summary += (
                f"• {item['food_name'].capitalize()} ({item['amount_g']:.0f}g) "
                f"— {item['calories']:.0f} kcal\n"
            )

        await update.message.reply_text(food_summary, parse_mode="Markdown")
        await update.message.reply_text(response)
        await save_conversation(user_id, "model", response)
    else:
        # Treat as general chat
        await _handle_general_chat(update, context, user_id, text, ai_service)


async def _handle_general_chat(
    update, context, user_id: int, text: str, ai_service
) -> None:
    """Handle general conversational messages."""
    # Get conversation history
    history = await get_recent_conversations(user_id)

    # Build context
    chat_context = await _build_chat_context(user_id)

    # Generate response
    response = await ai_service.chat(
        message=text,
        context=chat_context,
        history=history,
    )

    # Save to conversation history
    await save_conversation(user_id, "user", text)
    await save_conversation(user_id, "model", response)

    await update.message.reply_text(response)


def build_message_handlers():
    return [
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_message,
        )
    ]

