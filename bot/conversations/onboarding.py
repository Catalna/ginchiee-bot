"""
bot/conversations/onboarding.py
Multi-step ConversationHandler for new user registration.

Steps:
  1. Ask name
  2. Ask age
  3. Ask gender
  4. Ask height
  5. Ask weight
  6. Ask goal (inline keyboard)
  7. Ask activity level (inline keyboard)
  8. Ask timezone
  9. Confirm & save
"""

import logging
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from services.user_service import create_or_update_user, create_or_update_diet_profile
from services.nutrition_service import get_nutrition_target

logger = logging.getLogger(__name__)

# States
(
    ASK_NAME,
    ASK_AGE,
    ASK_GENDER,
    ASK_HEIGHT,
    ASK_WEIGHT,
    ASK_GOAL,
    ASK_ACTIVITY,
    ASK_TIMEZONE,
    CONFIRM,
) = range(9)


GOAL_OPTIONS = [
    ("🔻 Turun Berat Badan", "LOSE_WEIGHT"),
    ("⚖️ Jaga Berat Badan", "MAINTAIN_WEIGHT"),
    ("💪 Naik Berat Badan", "GAIN_WEIGHT"),
]

ACTIVITY_OPTIONS = [
    ("🛋️ Jarang Gerak (Sedentary)", "SEDENTARY"),
    ("🚶 Sedikit Aktif (Light)", "LIGHT"),
    ("🏃 Cukup Aktif (Moderate)", "MODERATE"),
    ("⚡ Aktif (Active)", "ACTIVE"),
    ("🔥 Sangat Aktif (Very Active)", "VERY_ACTIVE"),
]

TIMEZONE_OPTIONS = [
    ("WIB (Jakarta/Surabaya)", "Asia/Jakarta"),
    ("WITA (Bali/Makassar)", "Asia/Makassar"),
    ("WIT (Papua/Ambon)", "Asia/Jayapura"),
]


def _make_keyboard(options: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data=value)]
        for label, value in options
    ])


# ── Step handlers ─────────────────────────────────────────────────────────────

async def start_onboarding(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point — triggered by /start."""
    user = update.effective_user
    await update.message.reply_text(
        f"Halo! 👋 Aku *Ginchiee*, companion diet kamu~\n\n"
        f"Aku akan bantu kamu menjaga pola makan, kasih reminder makan, "
        f"dan ngobrol soal nutrisi! 🍎\n\n"
        f"Yuk kita mulai setup profil dulu!\n\n"
        f"*Siapa namamu?*\n_(kamu boleh pakai nama panggilan)_",
        parse_mode="Markdown",
    )
    return ASK_NAME


async def ask_age(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["name"] = update.message.text.strip()
    await update.message.reply_text(
        f"Hei *{context.user_data['name']}*! Senang kenalan~ 😊\n\n"
        f"*Berapa umurmu?* (contoh: 22)",
        parse_mode="Markdown",
    )
    return ASK_AGE


async def ask_gender(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        age = int(update.message.text.strip())
        if not (10 <= age <= 100):
            raise ValueError
        context.user_data["age"] = age
    except ValueError:
        await update.message.reply_text("Hmm, umurnya kurang valid nih 😅 Masukkan angka antara 10-100:")
        return ASK_AGE

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("👦 Laki-laki", callback_data="male"),
            InlineKeyboardButton("👧 Perempuan", callback_data="female"),
        ]
    ])
    await update.message.reply_text(
        "*Jenis kelaminmu?*",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )
    return ASK_GENDER


async def ask_height(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["gender"] = query.data

    await query.edit_message_text(
        "*Berapa tinggi badanmu?* (dalam cm, contoh: 170)",
        parse_mode="Markdown",
    )
    return ASK_HEIGHT


async def ask_weight(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        height = float(update.message.text.strip())
        if not (100 <= height <= 250):
            raise ValueError
        context.user_data["height_cm"] = height
    except ValueError:
        await update.message.reply_text("Tingginya kurang valid 😅 Masukkan dalam cm (contoh: 170):")
        return ASK_HEIGHT

    await update.message.reply_text(
        "*Berapa berat badanmu sekarang?* (dalam kg, contoh: 65)",
        parse_mode="Markdown",
    )
    return ASK_WEIGHT


async def ask_goal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        weight = float(update.message.text.strip())
        if not (20 <= weight <= 300):
            raise ValueError
        context.user_data["weight_kg"] = weight
    except ValueError:
        await update.message.reply_text("Beratnya kurang valid 😅 Masukkan dalam kg (contoh: 65):")
        return ASK_WEIGHT

    await update.message.reply_text(
        "*Apa tujuan dietmu?*",
        parse_mode="Markdown",
        reply_markup=_make_keyboard(GOAL_OPTIONS),
    )
    return ASK_GOAL


async def ask_activity(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["goal"] = query.data

    await query.edit_message_text(
        "*Seberapa aktif kamu sehari-hari?*\n\n"
        "_(Ini mempengaruhi perhitungan kalori harian kamu)_",
        parse_mode="Markdown",
        reply_markup=_make_keyboard(ACTIVITY_OPTIONS),
    )
    return ASK_ACTIVITY


async def ask_timezone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["activity_level"] = query.data

    await query.edit_message_text(
        "*Kamu di zona waktu mana?*",
        parse_mode="Markdown",
        reply_markup=_make_keyboard(TIMEZONE_OPTIONS),
    )
    return ASK_TIMEZONE


async def confirm_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    user_data = context.user_data
    user_data["timezone"] = query.data

    tg_user = update.effective_user
    user_id = tg_user.id

    # Save to database
    await create_or_update_user(
        user_id=user_id,
        name=user_data["name"],
        username=tg_user.username,
        timezone=user_data["timezone"],
    )
    await create_or_update_diet_profile(
        user_id=user_id,
        age=user_data["age"],
        gender=user_data["gender"],
        height_cm=user_data["height_cm"],
        weight_kg=user_data["weight_kg"],
        goal=user_data["goal"],
        activity_level=user_data["activity_level"],
    )

    # Calculate targets
    from services.nutrition_service import get_nutrition_target
    target = await get_nutrition_target(user_id)

    goal_labels = {
        "LOSE_WEIGHT": "Turun Berat Badan 🔻",
        "MAINTAIN_WEIGHT": "Jaga Berat Badan ⚖️",
        "GAIN_WEIGHT": "Naik Berat Badan 💪",
    }
    activity_labels = {
        "SEDENTARY": "Jarang Gerak",
        "LIGHT": "Sedikit Aktif",
        "MODERATE": "Cukup Aktif",
        "ACTIVE": "Aktif",
        "VERY_ACTIVE": "Sangat Aktif",
    }

    target_text = ""
    if target:
        target_text = (
            f"\n\n🎯 *Target Harianmu:*\n"
            f"• Kalori: `{target.calories:,}` kcal\n"
            f"• Protein: `{target.protein_g}g`\n"
            f"• Karbs: `{target.carbs_g}g`\n"
            f"• Lemak: `{target.fat_g}g`\n"
            f"• Air: `{target.water_ml:.0f}ml`"
        )

    await query.edit_message_text(
        f"✅ *Profil kamu sudah tersimpan!*\n\n"
        f"👤 *{user_data['name']}*\n"
        f"• Umur: {user_data['age']} tahun\n"
        f"• TB/BB: {user_data['height_cm']}cm / {user_data['weight_kg']}kg\n"
        f"• Tujuan: {goal_labels.get(user_data['goal'], user_data['goal'])}\n"
        f"• Aktivitas: {activity_labels.get(user_data['activity_level'], '')}"
        f"{target_text}\n\n"
        f"Sekarang kamu bisa:\n"
        f"• `/schedule` — Atur jadwal makan & reminder\n"
        f"• `/log` — Catat makanan\n"
        f"• `/today` — Lihat progress hari ini\n"
        f"• Atau ngobrol bebas sama aku! 😊",
        parse_mode="Markdown",
    )

    return ConversationHandler.END


async def cancel_onboarding(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "Setup dibatalkan. Kalau mau mulai lagi, ketik /start ya! 😊"
    )
    return ConversationHandler.END


# ── ConversationHandler factory ───────────────────────────────────────────────

def build_onboarding_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("start", start_onboarding)],
        states={
            ASK_NAME:     [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_age)],
            ASK_AGE:      [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_gender)],
            ASK_GENDER:   [CallbackQueryHandler(ask_height, pattern="^(male|female)$")],
            ASK_HEIGHT:   [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_weight)],
            ASK_WEIGHT:   [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_goal)],
            ASK_GOAL:     [CallbackQueryHandler(ask_activity, pattern="^(LOSE_WEIGHT|MAINTAIN_WEIGHT|GAIN_WEIGHT)$")],
            ASK_ACTIVITY: [CallbackQueryHandler(ask_timezone, pattern="^(SEDENTARY|LIGHT|MODERATE|ACTIVE|VERY_ACTIVE)$")],
            ASK_TIMEZONE: [CallbackQueryHandler(confirm_profile, pattern="^Asia/")],
        },
        fallbacks=[CommandHandler("cancel", cancel_onboarding)],
        allow_reentry=True,
    )
