"""
services/ai_service.py
Abstraction layer for Google Gemini LLM.
Application code must only use this service — never call the SDK directly.

Two models are held deliberately:
  * `_model`     — carries Ginchiee's personality (system instruction + few-shot
                  history preamble) and is used for anything the user reads.
  * `_json_model` — a raw model constrained to JSON output, used for parsing
                  tasks (food items, activity slots) where personality would
                  only corrupt the payload.
"""

import asyncio
import io
import json
import logging
import re
from datetime import datetime
from typing import Any, Optional

import google.generativeai as genai
import pytz
from PIL import Image

from config.settings import (
    AI_MAX_OUTPUT_TOKENS,
    AI_TEMPERATURE,
    AI_TOP_P,
    APP_TIMEZONE,
    GEMINI_API_KEY,
    GEMINI_MODEL,
    MAX_CONVERSATION_HISTORY,
)
from services.personality import (
    PERSONA_SYSTEM_INSTRUCTION,
    anti_examples_block,
    sanitize_reply,
)

logger = logging.getLogger(__name__)

#: Instruction given to the raw model when it must emit parseable JSON.
JSON_SYSTEM_INSTRUCTION = (
    "You are a strict data extraction engine. You only ever reply with one "
    "valid JSON value (object or array) and nothing else. No markdown, no code "
    "fences, no explanation, no prose before or after."
)


class AIService:
    """Wrapper around the Gemini SDK with personality and JSON-mode models."""

    def __init__(self):
        if not GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY is empty; cannot configure Gemini.")

        genai.configure(api_key=GEMINI_API_KEY)

        # Generation parameters matter more than they look: defaults produce
        # flat, formal, over-long replies.
        self._generation_config = genai.GenerationConfig(
            temperature=AI_TEMPERATURE,
            top_p=AI_TOP_P,
            max_output_tokens=AI_MAX_OUTPUT_TOKENS,
            candidate_count=1,
        )
        # Structured extraction needs determinism, not creativity.
        self._json_generation_config = genai.GenerationConfig(
            temperature=0.1,
            top_p=0.8,
            max_output_tokens=1200,
            candidate_count=1,
            response_mime_type="application/json",
        )

        persona = PERSONA_SYSTEM_INSTRUCTION + anti_examples_block()

        self._model = genai.GenerativeModel(
            model_name=GEMINI_MODEL,
            system_instruction=persona,
            generation_config=self._generation_config,
        )
        self._json_model = genai.GenerativeModel(
            model_name=GEMINI_MODEL,
            system_instruction=JSON_SYSTEM_INSTRUCTION,
            generation_config=self._json_generation_config,
        )
        # Backwards-compatible alias for the raw/plain model.
        self._raw_model = self._json_model

        self._timezone = pytz.timezone(APP_TIMEZONE)

    # ── Personality-aware chat ──────────────────────────────────────────────

    async def chat(
        self,
        message: str,
        context: Optional[dict] = None,
        history: Optional[list[dict]] = None,
    ) -> str:
        """Generate a conversational response with user context.

        Args:
            message: User's message
            context: Dict containing user profile, today's nutrition, etc.
            history: List of {'role': 'user'|'model', 'content': str}
        """
        context_block = _build_context_block(context) if context else ""

        # The context block is personal memory, not a report to read out.
        user_turn = (
            f"{context_block}\n\nPesan pengguna: {message}"
            if context_block else message
        )

        gemini_history = self._build_chat_history(history)

        try:
            chat_session = self._model.start_chat(history=gemini_history)
            response = await asyncio.to_thread(chat_session.send_message, user_turn)
            text = getattr(response, "text", "") or ""
            text = text.strip()
            if not text:
                logger.warning("Empty chat response from Gemini.")
                return _fallback_chat_reply()

            return sanitize_reply(text)
        except Exception as exc:
            logger.error("Gemini chat error: %s", exc, exc_info=True)
            return _fallback_chat_reply()

    def _build_chat_history(self, history: Optional[list[dict]]) -> list[dict]:
        """Alternating, model-last history with the few-shot preamble.

        Gemini requires strictly alternating roles and refuses a history whose
        final turn is 'user' (the next turn appends to it). Consecutive turns
        from the same role are dropped rather than triggering an API error.
        """
        from services.personality import few_shot_as_history

        gemini_history: list[dict] = list(few_shot_as_history())

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
                if not gemini_history or gemini_history[-1]["role"] != role:
                    # Only append when it keeps the alternation valid.
                    if not gemini_history and role != "user":
                        continue
                    gemini_history.append({"role": role, "parts": [content]})
                    prev_role = role

        # Ensure the last history turn is not 'user'.
        while gemini_history and gemini_history[-1]["role"] == "user":
            gemini_history.pop()

        return gemini_history

    # ── Reminders ───────────────────────────────────────────────────────────

    async def generate_reminder(self, context: dict) -> str:
        """Generate an intelligent meal reminder message."""
        prompt = _build_reminder_prompt(context)
        try:
            response = await asyncio.to_thread(self._model.generate_content, prompt)
            text = (getattr(response, "text", "") or "").strip()
            if not text:
                return _fallback_reminder(context.get("meal_type", ""))
            return sanitize_reply(text, max_sentences=4)
        except Exception as exc:
            logger.error("Gemini reminder error: %s", exc, exc_info=True)
            return _fallback_reminder(context.get("meal_type", ""))

    async def generate_activity_reminder(self, context: dict) -> str:
        """Generate a reminder message for a user activity/event."""
        prompt = _build_activity_reminder_prompt(context)
        try:
            response = await asyncio.to_thread(self._model.generate_content, prompt)
            text = (getattr(response, "text", "") or "").strip()
            if not text:
                return _fallback_activity_reminder(context)
            return sanitize_reply(text, max_sentences=3)
        except Exception as exc:
            logger.error("Gemini activity reminder error: %s", exc, exc_info=True)
            return _fallback_activity_reminder(context)

    # ── Structured extraction (JSON mode) ──────────────────────────────────

    async def generate_json(self, prompt: str, *, retry: bool = True) -> Optional[Any]:
        """Run a prompt expecting a JSON reply. Returns None on failure.

        Returns None (not a sentinel string) so callers cannot accidentally
        mistype the result, and so a parse failure is always detectable.
        """
        try:
            response = await asyncio.to_thread(self._json_model.generate_content, prompt)
            raw = (getattr(response, "text", "") or "").strip()
            return _parse_json_reply(raw)
        except Exception as exc:
            logger.warning("Gemini JSON call failed: %s", exc)

        if retry:
            try:
                logger.info("Retrying Gemini JSON call with an explicit reminder.")
                follow_up = (
                    f"{prompt}\n\nBalas HANYA dengan satu JSON valid. "
                    "Tanpa markdown, tanpa teks lain."
                )
                response = await asyncio.to_thread(self._json_model.generate_content, follow_up)
                raw = (getattr(response, "text", "") or "").strip()
                return _parse_json_reply(raw)
            except Exception as exc:
                logger.error("Gemini JSON retry failed: %s", exc, exc_info=True)

        return None

    async def generate_raw(self, prompt: str) -> str:
        """Backwards-compatible raw generation. Returns "" on failure."""
        result = await self.generate_json(prompt)
        if result is None:
            return ""
        if isinstance(result, str):
            return result
        return json.dumps(result, ensure_ascii=False)

    # ── Food logging response ──────────────────────────────────────────────

    async def generate_food_response(
        self,
        logged_items: list[dict],
        daily_progress: Optional[dict],
        context: Optional[dict] = None,
        adaptive_target: Optional[dict] = None,
        nutrition_history: Optional[list] = None,
    ) -> str:
        """React to what the user just logged.

        The numbers are already delivered in the summary message, so this only
        has to feel like a person reacting — not re-read a nutrition report.
        """
        if not logged_items:
            return _fallback_chat_reply()

        items_str = "\n".join(
            f"- {item['food_name']} {item['amount_g']:.0f}g"
            for item in logged_items
        )
        names = ", ".join(item["food_name"] for item in logged_items)

        progress_str = ""
        if daily_progress:
            p = daily_progress
            target_cal = (
                adaptive_target["calories"]
                if adaptive_target and adaptive_target.get("days_analysed", 0) >= 2
                else p["target"]["calories"]
            )
            # Only a nudge, never a table — the summary message carries the numbers.
            progress_str = (
                f"\nKalori tadi {p['consumed']['calories']:.0f} kcal, "
                f"sekitar {max(0, target_cal - p['consumed']['calories']):.0f} kcal "
                f"tersisa buat hari ini (target {target_cal} kcal)."
            )

        adaptive_str = ""
        if adaptive_target and adaptive_target.get("days_analysed", 0) >= 2:
            adaptive_str = (
                f"\nCatatan: beberapa hari terakhir tren {adaptive_target['trend_label']}."
            )

        history_str = ""
        if nutrition_history:
            history_str = (
                "\nRiwayat 3 hari terakhir: "
                + ", ".join(f"{h['date']}: {h['calories']:.0f} kcal" for h in nutrition_history[:3])
            )

        prompt = f"""Pengguna baru saja mencatat: {names}.
{items_str}{progress_str}{adaptive_str}{history_str}

Tugasmu: tanggapi seperti bestie, BUKAN seperti asisten.
- Komentari makanannya secara singkat (1-2 kalimat), boleh excited atau lucu.
- Kalau ada info sisa kalori, sisipkan secara natural dalam satu kalimat.
- Kalau ada tren kelebihan/kekurangan, kasih saran santai (jangan ceramah).
- JANGAN tulis ulang daftar makanan beserta angka satu per satu.
- JANGAN pakai bullet point, tabel, atau daftar bernomor.
- Maksimal 3 kalimat, maksimal 2 emoji.
- Mulai langsung dengan reaksinya, jangan dengan "Tentu!" atau "Baik!"."""

        try:
            response = await asyncio.to_thread(self._model.generate_content, prompt)
            text = (getattr(response, "text", "") or "").strip()
            if text:
                return sanitize_reply(text)
        except Exception as exc:
            logger.error("Gemini food response error: %s", exc, exc_info=True)

        total_cal = sum(item["calories"] for item in logged_items)
        return sanitize_reply(f"noted ya! total {total_cal:.0f} kcal nih, simpel banget")

    # ── Multimodal image analysis ──────────────────────────────────────────

    async def analyze_image(
        self,
        image_bytes: bytes,
        caption: str = "",
        context: Optional[dict] = None,
    ) -> dict:
        """Analyze an image sent by the user, classifying FOOD / ACTIVITY / GENERAL."""
        now = datetime.now(self._timezone)
        now_str = now.strftime("%Y-%m-%d %H:%M")

        context_str = _build_context_block(context) if context else ""
        caption_info = f'\nCaption dari pengguna: "{caption}"' if caption else ""

        prompt = _build_image_prompt(now_str, context_str, caption_info)

        try:
            img = Image.open(io.BytesIO(image_bytes))
            response = await asyncio.to_thread(
                self._json_model.generate_content, [img, prompt]
            )
            raw = (getattr(response, "text", "") or "").strip()
            data = _parse_json_reply(raw)
            if isinstance(data, dict) and data.get("category"):
                return data
        except Exception as exc:
            logger.error("Error analyzing image with Gemini: %s", exc, exc_info=True)

        return {
            "category": "GENERAL",
            "comment": "Wah fotonya seru! Tapi aku agak kesulitan mengenali detailnya nih~",
        }


# ── Fallbacks ────────────────────────────────────────────────────────────────

_MEAL_LABELS = {
    "breakfast": "Sarapan",
    "lunch": "Makan siang",
    "snack": "Snack",
    "dinner": "Makan malam",
}


def _fallback_chat_reply() -> str:
    return "waduh, aku lagi gagal mikir nih. coba ulangi sekali lagi ya"


def _fallback_reminder(meal_type: str) -> str:
    meal = _MEAL_LABELS.get(meal_type, "Makan")
    return f"{meal} time! jangan lupa makan ya"


def _fallback_activity_reminder(context: dict) -> str:
    title = context.get("title", "kegiatan")
    remind_mins = context.get("remind_mins", 30)
    return f"hei! {remind_mins} menit lagi kamu ada {title} lho, jangan sampai kelewatan ya"


# ── JSON helpers ─────────────────────────────────────────────────────────────

def _parse_json_reply(raw: str) -> Optional[Any]:
    """Extract a JSON value from a model reply, tolerating code fences."""
    if not raw:
        return None

    candidate = raw.strip()
    candidate = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", candidate)
    candidate = re.sub(r"\s*```$", "", candidate.strip())
    candidate = candidate.strip()

    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    # Fall back to the widest JSON-looking slice.
    for pattern in (r"\{.*\}", r"\[.*\]"):
        match = re.search(pattern, candidate, re.DOTALL)
        if not match:
            continue
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            continue

    logger.warning("Could not parse JSON from model reply: %s", raw[:200])
    return None


# ── Context builders ─────────────────────────────────────────────────────────

def _build_context_block(context: dict) -> str:
    """Build a context string to prepend to user messages."""
    parts = []

    now = datetime.now(pytz.timezone(APP_TIMEZONE))
    day_names = {
        "Monday": "Senin", "Tuesday": "Selasa", "Wednesday": "Rabu",
        "Thursday": "Kamis", "Friday": "Jumat", "Saturday": "Sabtu", "Sunday": "Minggu",
    }
    day_id = day_names.get(now.strftime("%A"), now.strftime("%A"))
    parts.append(f"[Catatan pribadi - {day_id}, {now.strftime('%d %B %Y %H:%M')} WIB]")

    if profile := context.get("profile"):
        parts.append(
            f"Profil: {profile.get('name')}, "
            f"umur {profile.get('age')} tahun, "
            f"BB {profile.get('weight_kg')}kg, "
            f"TB {profile.get('height_cm')}cm, "
            f"tujuan: {profile.get('goal')}"
        )

    if target := context.get("target"):
        # Callers may pass a partially-populated target (e.g. only calories),
        # so read defensively — a KeyError here would abort the whole reply.
        parts.append(
            f"Target harian: {target.get('calories', 0)} kcal, "
            f"protein {target.get('protein_g', 0)}g, "
            f"karbs {target.get('carbs_g', 0)}g, "
            f"lemak {target.get('fat_g', 0)}g"
        )

    if consumed := context.get("consumed"):
        parts.append(
            f"Sudah dikonsumsi hari ini: {consumed.get('calories', 0)} kcal, "
            f"protein {consumed.get('protein_g', 0)}g, "
            f"karbs {consumed.get('carbs_g', 0)}g, "
            f"lemak {consumed.get('fat_g', 0)}g"
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
            f"{a['title']} ({format_activity_time(a.get('activity_dt'))})"
            for a in activities[:5]
        )
        if act_str:
            parts.append(f"Kegiatan mendatang: {act_str}")

    if adaptive := context.get("adaptive_target"):
        days = adaptive.get("days_analysed", 0)
        if days >= 2:
            trend = adaptive.get("trend_label", "")
            adj = adaptive.get("adjustment_kcal", 0)
            sign = "+" if adj >= 0 else ""
            parts.append(
                f"Target adaptif hari ini: {adaptive.get('calories', 0)} kcal "
                f"(basis {adaptive.get('base_calories', 0)} kcal, "
                f"penyesuaian {sign}{adj} kcal) "
                f"(tren {days} hari terakhir: {trend})"
            )

    if history := context.get("nutrition_history"):
        hist_lines = [
            f"{h['date']}: {h['calories']} kcal "
            f"(P:{h['protein_g']}g C:{h['carbs_g']}g L:{h['fat_g']}g)"
            for h in history[:5]
        ]
        if hist_lines:
            parts.append("Riwayat makan 5 hari terakhir:\n" + "\n".join(hist_lines))

    # Instruction for how to treat the block above.
    parts.append(
        "Catatan di atas adalah ingatanmu tentang user. Pakai untuk menanggapi "
        "secara personal, JANGAN dijadikan laporan atau dibaca ulang ke user."
    )

    return "\n".join(parts)


def format_activity_time(value: Any) -> str:
    """'2026-10-11T09:00:00+00:00' -> '2026-10-11 09:00'"""
    if not value:
        return ""
    if isinstance(value, datetime):
        return value.astimezone(pytz.timezone(APP_TIMEZONE)).strftime("%Y-%m-%d %H:%M")
    text = str(value)
    return text[:16].replace("T", " ")


def _build_reminder_prompt(context: dict) -> str:
    meal_type = context.get("meal_type", "makan")
    meal_label = _MEAL_LABELS.get(meal_type, str(meal_type).capitalize())
    user_name = context.get("user_name", "kamu")

    protein_consumed = context.get("protein_consumed", 0)
    protein_target = context.get("protein_target", 0)
    calories_consumed = context.get("calories_consumed", 0)
    calories_target = context.get("calories_target", 0)

    return f"""Buat reminder makan untuk {user_name}.

Waktu: {meal_label}
Protein hari ini: {protein_consumed}g dari target {protein_target}g
Kalori hari ini: {calories_consumed} kcal dari target {calories_target} kcal

Buat pesan reminder yang:
- Singkat, 2-4 kalimat, gaya chat
- Friendly dan sedikit bercanda
- Sisipkan info nutrisi yang relevan secara natural
- Maximal 2 emoji
- Beri satu saran makanan yang bisa dipilih

Jangan mulai dengan "Halo" atau menyebut nama user di awal.
Jangan pakai bullet point atau daftar bernomor.
Jangan tutup dengan "Semoga membantu"."""


def _build_activity_reminder_prompt(context: dict) -> str:
    title = context.get("title", "kegiatan")
    description = context.get("description", "")
    activity_dt = context.get("activity_dt", "")
    remind_mins = context.get("remind_mins", 30)
    user_name = context.get("user_name", "kamu")

    desc_info = f"\nDeskripsi: {description}" if description else ""
    time_str = format_activity_time(activity_dt)

    return f"""Buat pesan pengingat kegiatan untuk {user_name}.

Kegiatan: {title}{desc_info}
Waktu: {time_str}
Pengingat: {remind_mins} menit sebelum kegiatan

Buat pesan pengingat yang:
- Singkat dan energik (2-3 kalimat)
- Hangat dan supportif seperti teman baik
- Sebutkan nama kegiatannya
- Maximal 2 emoji
- Santai dan tidak formal

Jangan mulai dengan "Halo" atau menyebut nama user di awal.
Jangan pakai bullet point.
Jangan tutup dengan "Semoga membantu"."""


def _build_image_prompt(now_str: str, context_str: str, caption_info: str) -> str:
    return f"""Waktu saat ini: {now_str} WIB.
{context_str}
{caption_info}

Lihat gambar yang dikirim pengguna dan tentukan kategorinya.
Kembalikan HANYA satu objek JSON valid (tanpa markdown codeblock, tanpa teks lain).

Kategori yang mungkin:

1. "FOOD" - gambar menunjukkan makanan, minuman, hidangan, camilan, buah, sayur,
   menu restoran, atau struk makan.
   {{"category": "FOOD", "items": [{{"food": "nama makanan jelas", "amount_g": 150,
   "unit": "porsi", "calories": 250, "protein_g": 25.0, "carbs_g": 5.0, "fat_g": 12.0}}],
   "comment": "komentar singkat hangat kawaii (1-2 kalimat)"}}

2. "ACTIVITY" - gambar berupa poster acara, flyer event, tiket, screenshot jadwal,
   undangan, atau pengumuman kegiatan.
   {{"category": "ACTIVITY", "title": "judul singkat kegiatan",
   "description": "deskripsi atau lokasi jika ada",
   "activity_dt": "YYYY-MM-DDTHH:MM:SS", "remind_mins": 30,
   "comment": "komentar singkat yang mengajak acara ini"}}

3. "GENERAL" - gambar bukan makanan maupun poster acara (pemandangan, selfie, hewan, dll).
   {{"category": "GENERAL", "comment": "tanggapan ramah dan natural (2-3 kalimat)"}}

Berhati-hatilah dalam membaca tanggal dan jam pada poster/acara. Jika tidak bisa dipastikan,
isi activity_dt dengan waktu yang paling masuk akal (jam 09:00 jika hanya tanggal yang terbaca)."""
