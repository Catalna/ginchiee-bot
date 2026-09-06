"""
services/ai_service.py
Abstraction layer for Google Gemini LLM.
Application code must only use this service — never call the SDK directly.
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional

import google.generativeai as genai

from config.settings import GEMINI_API_KEY, GEMINI_MODEL, BOT_NAME, MAX_CONVERSATION_HISTORY

logger = logging.getLogger(__name__)

# System instruction for Ginchiee's personality
SYSTEM_INSTRUCTION = f"""Kamu adalah {BOT_NAME}, AI diet companion & health partner yang membantu pengguna menjaga pola makan, nutrisi, aktivitas fisik harian, dan kebiasaan hidup sehat.

Kepribadianmu:
- Casual, ramah, dan seru seperti sahabat dekat (gunakan bahasa Indonesia santai: "aku", "kamu", emoji yang relevan)
- Penuh empati, suportif, dan tidak pernah menghakimi
- Humoris dan playful, tapi tetap informatif dan bermanfaat

Fokus Topik:
- Pola makan, pencatatan makanan, estimasi kalori & makronutrisi (protein, karbo, lemak)
- Aktivitas harian, olahraga, hidrasi (minum air), istirahat, dan motivasi gaya hidup sehat
- Jika pengguna curhat, merasa lelah, malas ("mager"), atau bercanda (misal: "aku ngambek", "au ah", "capek bgt"), respon dengan hangat, empati, dan ceria, lalu kaitkan kembali secara natural ke kesehatan/energi mereka (misal: mengingatkan minum air, istirahat, atau makan yang bergizi).
- Jika pengguna meminta hal yang sepenuhnya di luar topik (seperti membuat kode program, analisa politik, trading crypto, dll.), tolak secara halus dan lucu, lalu arahkan kembali ke topik kesehatan, makanan, atau aktivitas harian.

Batasan Medis (PENTING):
- JANGAN mendiagnosis penyakit
- JANGAN memberikan resep obat atau instruksi medis klinis
- JANGAN mengklaim diri sebagai dokter
- Jika ada keluhan medis atau gejala penyakit serius, selalu sarankan untuk berkonsultasi langsung ke dokter atau ahli gizi profesional.
"""


class AIService:
    def __init__(self):
        genai.configure(api_key=GEMINI_API_KEY)
        self._model = genai.GenerativeModel(
            model_name=GEMINI_MODEL,
            system_instruction=SYSTEM_INSTRUCTION,
        )
        self._raw_model = genai.GenerativeModel(model_name=GEMINI_MODEL)

    async def chat(
        self,
        message: str,
        context: Optional[dict] = None,
        history: Optional[list[dict]] = None,
    ) -> str:
        """
        Generate a conversational response with user context.

        Args:
            message: User's message
            context: Dict containing user profile, today's nutrition, etc.
            history: List of {'role': 'user'|'model', 'content': str}
        """
        # Build context string
        context_block = _build_context_block(context) if context else ""

        # Build prompt with context prepended
        full_message = f"{context_block}\n\nPesan pengguna: {message}" if context_block else message

        # Build conversation history for Gemini
        gemini_history = []
        if history:
            for entry in history[-MAX_CONVERSATION_HISTORY:]:
                role = "user" if entry["role"] == "user" else "model"
                gemini_history.append({
                    "role": role,
                    "parts": [entry["content"]],
                })

        try:
            chat_session = self._model.start_chat(history=gemini_history)
            response = await asyncio.to_thread(chat_session.send_message, full_message)
            return response.text.strip()
        except Exception as e:
            logger.error(f"Gemini chat error: {e}")
            return "Maaf, aku lagi ada masalah teknis. Coba lagi ya! 🙏"

    async def generate_reminder(self, context: dict) -> str:
        """Generate an intelligent reminder message."""
        prompt = _build_reminder_prompt(context)
        try:
            response = await asyncio.to_thread(self._model.generate_content, prompt)
            return response.text.strip()
        except Exception as e:
            logger.error(f"Gemini reminder error: {e}")
            meal_labels = {
                "breakfast": "Sarapan",
                "lunch": "Makan siang",
                "snack": "Snack",
                "dinner": "Makan malam",
            }
            meal = meal_labels.get(context.get("meal_type", ""), "Makan")
            return f"🍽️ {meal} time! Jangan lupa makan ya 😊"

    async def generate_raw(self, prompt: str) -> str:
        """Generate raw text without personality (for parsing tasks)."""
        try:
            response = await asyncio.to_thread(self._raw_model.generate_content, prompt)
            return response.text.strip()
        except Exception as e:
            logger.error(f"Gemini raw generation error: {e}")
            return "[]"

    async def generate_food_response(
        self,
        logged_items: list[dict],
        daily_progress: Optional[dict],
        context: Optional[dict] = None,
    ) -> str:
        """Generate response after user logs food."""
        items_str = "\n".join(
            f"- {item['food_name']} {item['amount_g']}g → "
            f"{item['calories']} kcal, protein {item['protein_g']}g"
            for item in logged_items
        )

        progress_str = ""
        if daily_progress:
            p = daily_progress
            progress_str = f"""
Progress hari ini:
- Kalori: {p['consumed']['calories']}/{p['target']['calories']} kcal ({p['percentage']['calories']}%)
- Protein: {p['consumed']['protein_g']}/{p['target']['protein_g']}g ({p['percentage']['protein_g']}%)
- Karbs: {p['consumed']['carbs_g']}/{p['target']['carbs_g']}g ({p['percentage']['carbs_g']}%)
- Lemak: {p['consumed']['fat_g']}/{p['target']['fat_g']}g ({p['percentage']['fat_g']}%)"""

        prompt = f"""Pengguna baru saja mencatat makanan berikut:
{items_str}
{progress_str}

Buat response yang friendly, informatif, dan sesuai kepribadianmu. 
Komentari makanan yang dicatat, lalu berikan update progress hari ini, dan berikan saran singkat jika perlu.
Gunakan emoji yang relevan. Jangan terlalu panjang (maksimal 5-6 kalimat)."""

        try:
            response = await asyncio.to_thread(self._model.generate_content, prompt)
            return response.text.strip()
        except Exception as e:
            logger.error(f"Gemini food response error: {e}")
            total_cal = sum(i["calories"] for i in logged_items)
            return f"✅ Makanan tercatat! Total {total_cal:.0f} kcal. Mantap! 💪"


# ── Helper Functions ──────────────────────────────────────────────────────────

def _build_context_block(context: dict) -> str:
    """Build a context string to prepend to user messages."""
    parts = []

    now = datetime.now()
    parts.append(f"[Context - {now.strftime('%A, %d %B %Y %H:%M')}]")

    if profile := context.get("profile"):
        parts.append(
            f"Profil: {profile.get('name')}, "
            f"umur {profile.get('age')} tahun, "
            f"BB {profile.get('weight_kg')}kg, "
            f"TB {profile.get('height_cm')}cm, "
            f"tujuan: {profile.get('goal')}"
        )

    if target := context.get("target"):
        parts.append(
            f"Target harian: {target['calories']} kcal, "
            f"protein {target['protein_g']}g, "
            f"karbs {target['carbs_g']}g, "
            f"lemak {target['fat_g']}g"
        )

    if consumed := context.get("consumed"):
        parts.append(
            f"Sudah dikonsumsi hari ini: {consumed['calories']} kcal, "
            f"protein {consumed['protein_g']}g, "
            f"karbs {consumed['carbs_g']}g, "
            f"lemak {consumed['fat_g']}g"
        )

    if food_logs := context.get("food_logs"):
        foods = ", ".join(
            f"{f['food_name']} ({f['amount_g']}g)" for f in food_logs[:5]
        )
        parts.append(f"Makanan hari ini: {foods}")

    if schedules := context.get("schedules"):
        sched_str = ", ".join(
            f"{s['meal_type']} jam {s['time']}" for s in schedules if s.get("enabled")
        )
        if sched_str:
            parts.append(f"Jadwal makan: {sched_str}")

    return "\n".join(parts)


def _build_reminder_prompt(context: dict) -> str:
    meal_labels = {
        "breakfast": "Sarapan",
        "lunch": "Makan Siang",
        "snack": "Snack",
        "dinner": "Makan Malam",
    }
    meal_type = context.get("meal_type", "makan")
    meal_label = meal_labels.get(meal_type, meal_type.capitalize())
    user_name = context.get("user_name", "kamu")

    protein_consumed = context.get("protein_consumed", 0)
    protein_target = context.get("protein_target", 0)
    calories_consumed = context.get("calories_consumed", 0)
    calories_target = context.get("calories_target", 0)

    return f"""Buat reminder makan untuk pengguna bernama {user_name}.

Waktu: {meal_label}
Protein hari ini: {protein_consumed}g dari target {protein_target}g
Kalori hari ini: {calories_consumed} kcal dari target {calories_target} kcal

Buat pesan reminder yang:
- Singkat (2-4 kalimat)
- Friendly dan sedikit bercanda
- Sebutkan info nutrisi yang relevan
- Gunakan emoji yang sesuai
- Berikan satu saran singkat terkait makanan yang bisa dipilih

Jangan mulai dengan "Halo" atau menyebut nama pengguna di awal."""
