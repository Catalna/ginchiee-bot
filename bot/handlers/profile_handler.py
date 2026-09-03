"""
bot/handlers/profile_handler.py
/profile  — view current profile and nutrition targets
/setprofile — re-run profile setup
"""

import logging
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from services.user_service import get_user, get_diet_profile, has_diet_profile
from services.nutrition_service import get_nutrition_target

logger = logging.getLogger(__name__)

GOAL_LABELS = {
    "LOSE_WEIGHT": "Turun Berat Badan 🔻",
    "MAINTAIN_WEIGHT": "Jaga Berat Badan ⚖️",
    "GAIN_WEIGHT": "Naik Berat Badan 💪",
}

ACTIVITY_LABELS = {
    "SEDENTARY": "🛋️ Jarang Gerak",
    "LIGHT": "🚶 Sedikit Aktif",
    "MODERATE": "🏃 Cukup Aktif",
    "ACTIVE": "⚡ Aktif",
    "VERY_ACTIVE": "🔥 Sangat Aktif",
}


async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id

    user = await get_user(user_id)
    if not user:
        await update.message.reply_text(
            "Kamu belum terdaftar! Ketik /start dulu ya 😊"
        )
        return

    profile = await get_diet_profile(user_id)
    if not profile:
        await update.message.reply_text(
            "Profil dietmu belum lengkap. Ketik /setprofile untuk melengkapi! 😊"
        )
        return

    target = await get_nutrition_target(user_id)

    goal_str = GOAL_LABELS.get(profile["goal"], profile["goal"])
    activity_str = ACTIVITY_LABELS.get(profile["activity_level"], profile["activity_level"])

    bmi = profile["weight_kg"] / ((profile["height_cm"] / 100) ** 2)
    bmi_category = _bmi_category(bmi)

    target_text = ""
    if target:
        target_text = (
            f"\n\n🎯 *Target Harian:*\n"
            f"• Kalori: `{target.calories:,}` kcal\n"
            f"• Protein: `{target.protein_g}g`\n"
            f"• Karbs: `{target.carbs_g}g`\n"
            f"• Lemak: `{target.fat_g}g`\n"
            f"• Air: `{target.water_ml:.0f}ml`"
        )

    await update.message.reply_text(
        f"👤 *Profil: {user['name']}*\n\n"
        f"• Umur: {profile['age']} tahun\n"
        f"• Jenis Kelamin: {'👦 Laki-laki' if profile['gender'] == 'male' else '👧 Perempuan'}\n"
        f"• Tinggi: {profile['height_cm']} cm\n"
        f"• Berat: {profile['weight_kg']} kg\n"
        f"• BMI: `{bmi:.1f}` ({bmi_category})\n"
        f"• Tujuan: {goal_str}\n"
        f"• Aktivitas: {activity_str}\n"
        f"• Timezone: {user['timezone']}"
        f"{target_text}\n\n"
        f"_Gunakan /setprofile untuk memperbarui profil._",
        parse_mode="Markdown",
    )


async def setprofile_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Redirect user to use /start for profile setup."""
    await update.message.reply_text(
        "Untuk memperbarui profil, gunakan /start 😊\n"
        "Kamu bisa re-enter semua data dari awal."
    )


def _bmi_category(bmi: float) -> str:
    if bmi < 18.5:
        return "Kurus"
    elif bmi < 25.0:
        return "Normal ✅"
    elif bmi < 30.0:
        return "Kelebihan berat"
    else:
        return "Obesitas"


def build_profile_handlers():
    return [
        CommandHandler("profile", profile_command),
        CommandHandler("setprofile", setprofile_command),
    ]
