"""
services/ai_service.py
Abstraction layer for Google Gemini LLM.
Application code must only use this service — never call the SDK directly.
"""

import asyncio
import io
import json
import logging
import re
from datetime import datetime
from typing import Optional

import google.generativeai as genai
from PIL import Image

from config.settings import GEMINI_API_KEY, GEMINI_MODEL, BOT_NAME, MAX_CONVERSATION_HISTORY, APP_TIMEZONE

logger = logging.getLogger(__name__)

# System instruction for Ginchiee's personality
SYSTEM_INSTRUCTION = f"""Kamu adalah {BOT_NAME}, sahabat dekat (bestie) sekaligus health companion yang super ramah, hangat, excited, dan asik banget!

Kepribadianmu:
- Super friendly, hangat, ceria, dan excited seperti bestie yang selalu antusias nemenin ngobrol.
- Nada bicara santai ala obrolan teman sebaya (pakai kata "aku", "kamu", dan emoji yang gemas/ekspresif kayak ✨💖🥰😆🙌).
- Senang banget kalau diajak ngobrol apa aja: cerita random, gosip, curhat tentang hari-hari, hal lucu, maupun keluh kesah.
- Tanggap dengan penuh antusias, seru, suportif, dan penuh empati. Jangan kaku atau terdengar seperti robot/customer service!
- Kalau pengguna bercerita hal random/lucu/gosip, tanggapi dengan seru dan jangan memaksakan kembali ke topik diet/kesehatan kalau pengguna sedang tidak membahasnya.

Peran sebagai Health Companion:
- Supportif dan tidak pernah menghakimi (never judgmental) terhadap pilihan makan atau gaya hidup pengguna.
- Tidak strict dan tidak suka menceramahi.
- Jika pengguna ingin mencatat makanan, bertanya kalori, jadwal kegiatan, atau kesehatan, bantu dengan ceria, santai, dan solutif.
- Fokus pada semangat, kenyamanan, dan konsistensi.

Batasan Medis (PENTING):
- JANGAN mendiagnosis penyakit medis secara klinis.
- JANGAN memberikan resep obat keras.
- Untuk keluhan medis yang serius, sarankan konsultasi ke dokter atau profesional dengan nada peduli dan lembut.
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

        # Build robust alternating conversation history for Gemini
        gemini_history = []
        if history:
            prev_role = None
            for entry in history[-MAX_CONVERSATION_HISTORY:]:
                content = (entry.get("content") or "").strip()
                # Skip empty or previous technical error messages
                if not content or "masalah teknis" in content:
                    continue
                role = "user" if entry.get("role") == "user" else "model"
                if role == prev_role:
                    continue
                if not gemini_history and role != "user":
                    continue
                gemini_history.append({
                    "role": role,
                    "parts": [content],
                })
                prev_role = role

            # Ensure last turn in history is not 'user', because send_message sends the next user turn
            if gemini_history and gemini_history[-1]["role"] == "user":
                gemini_history.pop()

        try:
            chat_session = self._model.start_chat(history=gemini_history)
            response = await asyncio.to_thread(chat_session.send_message, full_message)
            return response.text.strip()
        except Exception as e:
            logger.error(f"Gemini chat error: {e}")
            return "Maaf, aku lagi ada masalah teknis. Coba lagi ya! 🙏"

    async def generate_reminder(self, context: dict) -> str:
        """Generate an intelligent meal reminder message."""
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

    async def generate_activity_reminder(self, context: dict) -> str:
        """Generate a reminder message for a user activity/event."""
        prompt = _build_activity_reminder_prompt(context)
        try:
            response = await asyncio.to_thread(self._model.generate_content, prompt)
            return response.text.strip()
        except Exception as e:
            logger.error(f"Gemini activity reminder error: {e}")
            title = context.get("title", "kegiatan")
            remind_mins = context.get("remind_mins", 30)
            return f"⏰ Hei! {remind_mins} menit lagi kamu ada *{title}* lho! Jangan sampai kelewatan ya~ 🌸"

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
        adaptive_target: Optional[dict] = None,
        nutrition_history: Optional[list] = None,
    ) -> str:
        """Generate response after user logs food, aware of trends and adaptive target."""
        items_str = "\n".join(
            f"- {item['food_name']} {item['amount_g']}g → "
            f"{item['calories']} kcal, protein {item['protein_g']}g"
            for item in logged_items
        )

        progress_str = ""
        if daily_progress:
            p = daily_progress
            # Use adaptive target calories if available
            target_cal = (
                adaptive_target["calories"]
                if adaptive_target and adaptive_target.get("days_analysed", 0) >= 2
                else p["target"]["calories"]
            )
            progress_str = f"""
Progress hari ini:
- Kalori: {p['consumed']['calories']}/{target_cal} kcal ({p['percentage']['calories']}%)
- Protein: {p['consumed']['protein_g']}/{p['target']['protein_g']}g ({p['percentage']['protein_g']}%)
- Karbs: {p['consumed']['carbs_g']}/{p['target']['carbs_g']}g ({p['percentage']['carbs_g']}%)
- Lemak: {p['consumed']['fat_g']}/{p['target']['fat_g']}g ({p['percentage']['fat_g']}%)"""

        # Build adaptive target insight
        adaptive_str = ""
        if adaptive_target and adaptive_target.get("days_analysed", 0) >= 2:
            adj = adaptive_target["adjustment_kcal"]
            trend = adaptive_target.get("trend_label", "")
            sign = "+" if adj >= 0 else ""
            adaptive_str = (
                f"\nTarget adaptif hari ini: {adaptive_target['calories']} kcal "
                f"(penyesuaian {sign}{adj} kcal karena tren: {trend})"
            )

        # Build history snippet
        history_str = ""
        if nutrition_history:
            recent = nutrition_history[:3]
            rows = ", ".join(
                f"{h['date']}: {h['calories']:.0f} kcal" for h in recent
            )
            history_str = f"\nRiwayat 3 hari terakhir: {rows}"

        prompt = f"""Pengguna baru saja mencatat makanan berikut:
{items_str}
{progress_str}{adaptive_str}{history_str}

Buat response yang friendly, informatif, dan sesuai kepribadianmu.
- Komentari makanan yang baru dicatat.
- Berikan update progress hari ini vs target (gunakan target adaptif jika ada).
- Jika ada tren kelebihan/kekurangan dari histori, berikan saran porsi yang bijak dan natural (jangan kaku/ceramah).
- Gunakan emoji yang relevan. Maksimal 5-6 kalimat."""

        try:
            response = await asyncio.to_thread(self._model.generate_content, prompt)
            return response.text.strip()
        except Exception as e:
            logger.error(f"Gemini food response error: {e}")
            total_cal = sum(i["calories"] for i in logged_items)
            return f"✅ Makanan tercatat! Total {total_cal:.0f} kcal. Mantap! 💪"

    async def analyze_image(
        self,
        image_bytes: bytes,
        caption: str = "",
        context: Optional[dict] = None,
    ) -> dict:
        """
        Analyze an image sent by user using Gemini Multimodal.
        Classifies as 'FOOD', 'ACTIVITY', or 'GENERAL'.
        """
        now = datetime.now()
        now_str = now.strftime('%Y-%m-%d %H:%M')
        
        context_str = _build_context_block(context) if context else ""
        caption_info = f'\nCaption dari pengguna: "{caption}"' if caption else ""
        
        prompt = f"""Kamu adalah {BOT_NAME}, sahabat dekat sekaligus health companion yang cerdas.
Waktu saat ini: {now_str} WIB.
{context_str}
{caption_info}

Tugas:
Lihat dan analisa gambar yang dikirim pengguna ini secara teliti.
Tentukan kategori gambar tersebut dan kembalikan HANYA format JSON valid murni (tanpa markdown codeblock, tanpa teks lain).

Kategori yang mungkin:
1. "FOOD" — Jika gambar menampilkan makanan, minuman, hidangan, camilan, buah, sayur, menu restoran, atau struk makan.
   Format JSON:
   {{
     "category": "FOOD",
     "items": [
       {{
         "food": "nama makanan jelas (misal: Nasi putih / Ayam bakar / Es teh manis)",
         "amount_g": 150,
         "unit": "porsi",
         "calories": 250,
         "protein_g": 25.0,
         "carbs_g": 5.0,
         "fat_g": 12.0
       }}
     ],
     "comment": "komentar pendek hangat, santai & suportif khas {BOT_NAME} tentang makanan ini (1-2 kalimat)"
   }}

2. "ACTIVITY" — Jika gambar berupa poster acara, flyer event, tiket, screenshot jadwal/kalender, undangan, atau pengumuman kegiatan.
   Format JSON:
   {{
     "category": "ACTIVITY",
     "title": "judul singkat kegiatan/acara",
     "description": "deskripsi atau lokasi jika ada",
     "activity_dt": "YYYY-MM-DDTHH:MM:SS",
     "remind_mins": 30,
     "comment": "komentar singkat {BOT_NAME} yang mengajak/mengingatkan acara ini"
   }}

3. "GENERAL" — Jika gambar bukan makanan ataupun poster acara (misal: pemandangan, selfie, kucing/hewan, barang, meme, dll).
   Format JSON:
   {{
     "category": "GENERAL",
     "comment": "tanggapan ramah, playful, dan natural khas sahabat baik tentang gambar ini (2-3 kalimat)"
   }}
"""
        try:
            img = Image.open(io.BytesIO(image_bytes))
            response = await asyncio.to_thread(self._raw_model.generate_content, [img, prompt])
            raw = response.text.strip()
            
            # Clean markdown codeblocks if present
            clean_raw = re.sub(r"^```[a-zA-Z]*\n", "", raw.strip(), flags=re.MULTILINE)
            clean_raw = re.sub(r"```$", "", clean_raw.strip(), flags=re.MULTILINE)
            
            json_match = re.search(r'\{.*\}', clean_raw, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                return data
        except Exception as e:
            logger.error(f"Error analyzing image with Gemini: {e}")

        return {
            "category": "GENERAL",
            "comment": "Wah fotonya seru! Tapi aku agak kesulitan mengenali detailnya nih~ 😅"
        }


# ── Helper Functions ──────────────────────────────────────────────────────────

def _build_context_block(context: dict) -> str:
    """Build a context string to prepend to user messages."""
    import pytz
    parts = []

    # Always use the app timezone (Asia/Jakarta / WIB) so AI knows the real local time
    _tz = pytz.timezone(APP_TIMEZONE)
    now = datetime.now(_tz)
    day_names = {
        "Monday": "Senin", "Tuesday": "Selasa", "Wednesday": "Rabu",
        "Thursday": "Kamis", "Friday": "Jumat", "Saturday": "Sabtu", "Sunday": "Minggu",
    }
    day_id = day_names.get(now.strftime("%A"), now.strftime("%A"))
    parts.append(
        f"[Context - {day_id}, {now.strftime('%d %B %Y %H:%M')} WIB]"
    )

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

    if activities := context.get("upcoming_activities"):
        act_str = "; ".join(
            f"{a['title']} ({a['activity_dt'][:16].replace('T', ' ')})" for a in activities[:5]
        )
        if act_str:
            parts.append(f"Kegiatan mendatang: {act_str}")

    if adaptive := context.get("adaptive_target"):
        days = adaptive.get("days_analysed", 0)
        trend = adaptive.get("trend_label", "")
        adj = adaptive.get("adjustment_kcal", 0)
        if days >= 2:
            sign = "+" if adj >= 0 else ""
            parts.append(
                f"Target adaptif hari ini: {adaptive['calories']} kcal "
                f"(basis {adaptive['base_calories']} kcal, penyesuaian {sign}{adj} kcal) "
                f"— tren {days} hari terakhir: {trend}"
            )

    if history := context.get("nutrition_history"):
        hist_lines = []
        for h in history[:5]:  # show last 5 days
            hist_lines.append(
                f"{h['date']}: {h['calories']} kcal "
                f"(P:{h['protein_g']}g C:{h['carbs_g']}g L:{h['fat_g']}g)"
            )
        if hist_lines:
            parts.append("Riwayat makan 5 hari terakhir:\n" + "\n".join(hist_lines))

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


def _build_activity_reminder_prompt(context: dict) -> str:
    title = context.get("title", "kegiatan")
    description = context.get("description", "")
    activity_dt = context.get("activity_dt", "")
    remind_mins = context.get("remind_mins", 30)
    user_name = context.get("user_name", "kamu")

    desc_info = f"\nDeskripsi: {description}" if description else ""
    time_str = activity_dt[:16].replace("T", " ") if activity_dt else ""

    return f"""Buat pesan pengingat kegiatan untuk {user_name}.

Kegiatan: {title}{desc_info}
Waktu: {time_str}
Pengingat: {remind_mins} menit sebelum kegiatan

Buat pesan pengingat yang:
- Singkat dan energik (2-3 kalimat)
- Hangat dan supportif seperti teman baik
- Sebutkan nama kegiatannya
- Gunakan emoji yang sesuai
- Tidak formal, santai aja

Jangan mulai dengan "Halo" atau menyebut nama pengguna di awal."""
