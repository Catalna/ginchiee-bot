"""
bot/handlers/photo_handler.py
Handles photo messages sent by users.
Uses Gemini Vision to classify & process photos (food, event poster, general).
"""

import logging
from datetime import datetime
from telegram import Update
from telegram.ext import ContextTypes, MessageHandler, filters

from services.user_service import (
    user_exists,
    get_user,
    get_diet_profile,
    get_meal_schedules,
    save_conversation,
)
from services.nutrition_service import (
    get_nutrition_target,
    get_daily_consumed,
    get_today_food_logs,
    get_daily_progress,
    log_food_from_photo_items,
    get_nutrition_history,
    get_adaptive_target,
)
from services.activity_service import (
    add_activity,
    get_upcoming_activities,
)

logger = logging.getLogger(__name__)


async def _build_chat_context(user_id: int) -> dict:
    """Build context dict for AI service."""
    user = await get_user(user_id)
    profile = await get_diet_profile(user_id)
    target = await get_nutrition_target(user_id)
    consumed = await get_daily_consumed(user_id)
    food_logs = await get_today_food_logs(user_id)
    schedules = await get_meal_schedules(user_id)
    upcoming_activities = await get_upcoming_activities(user_id, days=3)
    history = await get_nutrition_history(user_id, days=5)
    adaptive = await get_adaptive_target(user_id)

    return {
        "profile": {
            **dict(profile),
            "name": user["name"] if user else "User",
        } if profile else {"name": user["name"] if user else "User"},
        "target": {
            "calories": target.calories,
            "protein_g": target.protein_g,
            "carbs_g": target.carbs_g,
            "fat_g": target.fat_g,
        } if target else None,
        "consumed": consumed,
        "food_logs": food_logs[-5:] if food_logs else [],
        "schedules": schedules,
        "upcoming_activities": upcoming_activities,
        "nutrition_history": history,
        "adaptive_target": adaptive,
    }


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Main handler for photo messages."""
    user_id = update.effective_user.id

    if not await user_exists(user_id):
        await update.message.reply_text(
            "Halo! 👋 Kenalan dulu yuk sebelum kirim foto~\n"
            "Ketik /start untuk mulai ya! 😊"
        )
        return

    ai_service = context.bot_data["ai_service"]
    caption = update.message.caption.strip() if update.message.caption else ""

    # Send typing status
    try:
        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id, action="typing"
        )
    except Exception as e:
        logger.debug(f"Failed to send typing chat action: {e}")

    # Download highest resolution photo
    try:
        photo = update.message.photo[-1]
        photo_file = await context.bot.get_file(photo.file_id)
        image_bytes = await photo_file.download_as_bytearray()
    except Exception as e:
        logger.error(f"Failed to download photo from Telegram: {e}")
        await update.message.reply_text("Waduh, gagal mengunduh fotonya. Coba kirim lagi ya! 🙏")
        return

    chat_context = await _build_chat_context(user_id)

    # Analyze image with Gemini Vision
    result = await ai_service.analyze_image(
        image_bytes=bytes(image_bytes),
        caption=caption,
        context=chat_context,
    )

    category = result.get("category", "GENERAL").upper()

    # 1. FOOD CATEGORY
    if category == "FOOD" and result.get("items"):
        items = result.get("items", [])
        logged_items = await log_food_from_photo_items(user_id, items)

        summary_lines = ["📸 *Keliatannya enak banget nih!* 😋\n\n*✅ Berhasil dicatat:*"]
        total_cal = 0
        total_prot = 0
        for item in logged_items:
            total_cal += item["calories"]
            total_prot += item["protein_g"]
            summary_lines.append(
                f"• {item['food_name'].capitalize()} ({item['amount_g']:.0f}g) "
                f"— {item['calories']:.0f} kcal (P: {item['protein_g']:.1f}g | K: {item['carbs_g']:.1f}g | L: {item['fat_g']:.1f}g)"
            )

        summary_lines.append(f"\n🔥 *Total: {total_cal:.0f} kcal | Protein: {total_prot:.1f}g*")

        comment = result.get("comment", "")
        if comment:
            summary_lines.append(f"\n💬 {comment}")

        summary_lines.append("\n_Ketik /today untuk lihat progress harianmu!_ 🌸")
        response_text = "\n".join(summary_lines)

        await update.message.reply_text(response_text, parse_mode="Markdown")
        user_log_text = f"[Kirim Foto Makanan] {caption}" if caption else "[Kirim Foto Makanan]"
        await save_conversation(user_id, "user", user_log_text)
        await save_conversation(user_id, "model", response_text)
        return

    # 2. ACTIVITY / POSTER CATEGORY
    if category == "ACTIVITY" and result.get("title"):
        title = result.get("title", "Kegiatan dari Foto")
        activity_dt = result.get("activity_dt", datetime.now().isoformat())
        desc = result.get("description", "")
        remind = result.get("remind_mins", 30)

        activity_id = await add_activity(
            user_id=user_id,
            title=title,
            activity_dt=activity_dt,
            description=desc,
            remind_mins=remind,
        )

        dt_str = activity_dt
        try:
            dt_obj = datetime.fromisoformat(activity_dt)
            dt_str = dt_obj.strftime("%A, %d %b %Y • %H:%M")
        except Exception:
            dt_str = activity_dt[:16].replace("T", " ")

        desc_line = f"📝 _{desc}_\n" if desc else ""
        comment = result.get("comment", "")
        comment_line = f"\n💬 {comment}\n" if comment else ""

        response_text = (
            f"📸 *Aku nemu jadwal dari foto ini!* 🗓️\n\n"
            f"✅ *Sudah dicatat ke agendamu:*\n"
            f"📌 *{title}*\n"
            f"{desc_line}"
            f"🕒 {dt_str} WIB\n"
            f"⏰ Aku ingatkan {remind} menit sebelumnya 🔔\n"
            f"{comment_line}\n"
            f"_Ketik /agenda untuk lihat semua kegiatanmu!_ 🌸"
        )

        await update.message.reply_text(response_text, parse_mode="Markdown")
        user_log_text = f"[Kirim Foto Jadwal/Poster] {caption}" if caption else "[Kirim Foto Jadwal/Poster]"
        await save_conversation(user_id, "user", user_log_text)
        await save_conversation(user_id, "model", response_text)
        return

    # 3. GENERAL PHOTO CATEGORY
    comment = result.get("comment", "Fotonya bagus banget! ✨")
    await update.message.reply_text(comment)
    user_log_text = f"[Kirim Foto] {caption}" if caption else "[Kirim Foto]"
    await save_conversation(user_id, "user", user_log_text)
    await save_conversation(user_id, "model", comment)


def build_photo_handlers():
    return [
        MessageHandler(
            filters.PHOTO,
            handle_photo,
        )
    ]
