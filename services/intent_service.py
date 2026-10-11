"""
services/intent_service.py
Single-call intent router for free-form chat.

Replaces the previous cascade of a 60+ keyword pre-filter plus a separate LLM
call per branch. Problems with the old design:

  * the keyword list matched desires, not actions ("pengen nasi goreng" got
    logged as if the user had eaten it);
  * every message cost 1-3 Gemini calls;
  * there was no way to express "that was wrong, fix it", because nothing in
    the flow owned corrections.

This module turns one message into exactly one structured intent:

    {
      "action": "LOG_FOOD",
      "slots": {"foods": [{"name": "nasi goreng", "amount_g": 200, ...}]},
      "missing": [],
      "reply": "nasi goreng lagi? oke oke~",
      "confidence": 0.95
    }

The router never touches the database; it only decides. Execution and
confirmation live in bot/handlers/message_handler.py.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional

import pytz

from config.settings import APP_TIMEZONE
from services.activity_service import format_activity_datetime  # noqa: F401

logger = logging.getLogger(__name__)

# ── Actions ──────────────────────────────────────────────────────────────────

ACTION_CHAT = "CHAT"
ACTION_LOG_FOOD = "LOG_FOOD"
ACTION_CORRECT_FOOD = "CORRECT_FOOD"
ACTION_DELETE_FOOD = "DELETE_FOOD"
ACTION_ADD_ACTIVITY = "ADD_ACTIVITY"
ACTION_UPDATE_ACTIVITY = "UPDATE_ACTIVITY"
ACTION_DELETE_ACTIVITY = "DELETE_ACTIVITY"
ACTION_VIEW_AGENDA = "VIEW_AGENDA"
ACTION_MARK_DONE = "MARK_DONE"
ACTION_CHANGE_NAME = "CHANGE_NAME"
ACTION_UPDATE_PROFILE = "UPDATE_PROFILE"

#: Actions that change data and therefore need a confirmation step.
#: LOG_FOOD is deliberately excluded: it is the highest-frequency action and
#: correcting it is cheap and explicit (see CORRECT_FOOD / DELETE_FOOD).
ACTIONS_NEEDING_CONFIRMATION = {
    ACTION_CORRECT_FOOD,
    ACTION_DELETE_FOOD,
    ACTION_ADD_ACTIVITY,
    ACTION_UPDATE_ACTIVITY,
    ACTION_DELETE_ACTIVITY,
    ACTION_MARK_DONE,
    ACTION_CHANGE_NAME,
    ACTION_UPDATE_PROFILE,
}

ACTIONS_WITH_SLOTS = {
    ACTION_ADD_ACTIVITY: ("title", "date", "time"),
    ACTION_UPDATE_ACTIVITY: ("target", "title", "date", "time"),
    ACTION_LOG_FOOD: ("items",),
    ACTION_CORRECT_FOOD: ("target", "items"),
}

ALL_ACTIONS = (
    ACTION_CHAT,
    ACTION_LOG_FOOD,
    ACTION_CORRECT_FOOD,
    ACTION_DELETE_FOOD,
    ACTION_ADD_ACTIVITY,
    ACTION_UPDATE_ACTIVITY,
    ACTION_DELETE_ACTIVITY,
    ACTION_VIEW_AGENDA,
    ACTION_MARK_DONE,
    ACTION_CHANGE_NAME,
    ACTION_UPDATE_PROFILE,
)

INDONESIAN_DAYS = {
    "Monday": "Senin", "Tuesday": "Selasa", "Wednesday": "Rabu",
    "Thursday": "Kamis", "Friday": "Jumat", "Saturday": "Sabtu", "Sunday": "Minggu",
}


@dataclass
class Intent:
    """A single routed decision."""

    action: str = ACTION_CHAT
    slots: dict = field(default_factory=dict)
    missing: list = field(default_factory=list)
    reply: str = ""
    confidence: float = 0.0
    raw: Optional[dict] = None

    @property
    def needs_confirmation(self) -> bool:
        return self.action in ACTIONS_NEEDING_CONFIRMATION

    @property
    def is_chat(self) -> bool:
        return self.action == ACTION_CHAT

    @property
    def is_complete(self) -> bool:
        """True when no required slot is still missing."""
        return not self.missing


# ── Context for the router ───────────────────────────────────────────────────

def _now_local() -> datetime:
    return datetime.now(pytz.timezone(APP_TIMEZONE))


def _route_context_block(profile: Optional[dict], recent_activities: list, recent_food: list) -> str:
    """Only the grounding facts the router needs to resolve references."""
    lines = []
    now = _now_local()
    day = INDONESIAN_DAYS.get(now.strftime("%A"), now.strftime("%A"))
    lines.append(f"Waktu sekarang: {day}, {now.strftime('%d %B %Y %H:%M')} WIB")

    # Ground relative dates ("besok", "lusa") so the router never has to guess.
    tz = pytz.timezone(APP_TIMEZONE)
    for offset in range(0, 8):
        future = tz.localize(now.replace(tzinfo=None) + timedelta(days=offset))
        label = INDONESIAN_DAYS.get(future.strftime("%A"), future.strftime("%A"))
        prefix = {0: "hari ini", 1: "besok", 2: "lusa"}.get(offset, label)
        lines.append(f"- {prefix}: {future.strftime('%Y-%m-%d')} ({label})")

    if profile:
        lines.append(f"Nama user: {profile.get('name')}")

    if recent_activities:
        lines.append("Kegiatan milik user yang tersimpan:")
        for act in recent_activities[:10]:
            dt = act.get("activity_dt")
            when = (
                format_activity_datetime(dt)
                if not isinstance(dt, str)
                else dt[:16].replace("T", " ")
            )
            lines.append(f"- id {act['id']}: {act.get('title')} ({when}) done={bool(act.get('done'))}")

    if recent_food:
        lines.append(
            "Arsip makanan SUDAH tercatat hari ini (gunakan HANYA untuk mencocokkan "
            "tujuan koreksi/hapus. Pesan user yang menceritakan makanan tetap dianggap "
            "catatan BARU meski namanya sama dengan arsip ini):"
        )
        for item in recent_food[:8]:
            lines.append(
                f"- id {item['id']}: {item.get('food_name')} "
                f"{item.get('amount_g'):.0f}g ({item.get('calories'):.0f} kcal)"
            )

    return "\n".join(lines)


def build_route_prompt(
    message: str,
    profile: Optional[dict],
    recent_activities: list,
    recent_food: list,
    pending_note: str = "",
) -> str:
    """The routing prompt. One call, one JSON object back."""
    context_block = _route_context_block(profile, recent_activities, recent_food)

    pending_part = f"\n\n{pending_note}" if pending_note else ""

    return f"""Kamu adalah router intent untuk bot kesehatan bernama Ginchiee.
Tugasmu: tentukan apa yang benar-benar dimaksud user, lalu balas HANYA satu objek JSON.

{context_block}{pending_part}

INSTRUKSI PENTING:
- Balas HANYA satu objek JSON. Tanpa markdown, tanpa komentar, tanpa teks lain.
- Semua teks di dalam JSON harus Bahasa Indonesia santai.

Pesan user: "{message}"

Pilih TEPAT SATU action:

1. LOG_FOOD - user mencatat makanan yang SUDAH dimakan/diminum.
   Isi: {{"foods": [{{"name": "nasi goreng", "amount_g": 200, "calories": 320, "protein_g": 8, "carbs_g": 45, "fat_g": 10}}]}}
   Wajib isi calories. Jika user tidak sebut gram, perkirakan porsi wajar.
   Contoh: "tadi aku makan nasi goreng", "aku minum susu 1 gelas", "habis makan ayam geprek",
   "itu ga termasuk, kurang 1 makanan lagi", "oh iya tadi aku juga ngemil biskuit",
   "hitungin kalor sate 10 tusuk dan lontong", "aku makan sate 10 tusuk".
   "hitungin kalor X" dianggap mencatat makanan: di bot ini menghitung kalori
   berarti mencatat dan menghitung, bukan sekadar tanya angka.
   CATATAN: ini adalah catatan BARU. Jangan berubah jadi CHAT hanya karena namanya
   sama dengan makanan yang sudah ada di arsip.
   JANGAN pilih ini untuk niat/keinginan (mis. "pengen nasi goreng", "mau makan sushi nanti") -
   itu CHAT.

2. CORRECT_FOOD - user MENGOREKSI makanan yang sudah tercatat.
   Isi: {{"target": "nama makanan / kata kunci dari arsip", "foods": [...]}}
   target harus cocok dengan salah satu makanan di arsip di atas.
   Contoh: "salah, yang bener nasi gorengnya 150g", "bukan ayam, ayam bakar ya",
   "yang aku makan tadi nasi putih, bukan nasi goreng", "kok 1 porsi aja? aku makan 2 porsi".
   Bedakan dengan ADD: "kurang 1 makanan lagi" = tambah item (LOG_FOOD),
   "yang bener itu X" = ganti isi yang sudah tertulis (CORRECT_FOOD).

3. DELETE_FOOD - user ingin HAPUS catatan makanan.
   Isi: {{"target": "semua" atau "terakhir" atau nama makanan}}
   Contoh: "hapus nasi goreng dari catatan", "gajadi deh nasi tadi hapus aja", "hapus semua makananku hari ini".

4. ADD_ACTIVITY - user ingin MENYIMPAN kegiatan/jadwal/acara baru, atau minta
   diingatkan sesuatu. Isi:
   {{"title": "...", "date": "YYYY-MM-DD", "time": "HH:MM", "description": "...", "remind_mins": 30}}
   Slot wajib: title, date, time. Kalau user belum menyebut salah satunya,
   kosongkan field tsb (biarkan "") dan laporkan di "missing".
   Untuk "besok jam 3 sore" pakai date/tanggal besok dan time 15:00 (sore = 15:00-18:00).

   WAJIB pilih ADD_ACTIVITY kalau pesan mengandung salah satu pemicu ini,
   TERLEPAS dari berapa informasi yang ada:
   - "ingetin aku...", "ingetin ya...", "tolong ingetin...", "bisa ingetin aku?"
   - "jangan lupa aku...", "pengingat..."
   - "masukkan ke jadwal/kegiatan/agenda", "catat jadwal", "simpen jadwal"
   - pernyataan rencana yang punya hari/tanggal/jam: "aku ada X besok jam 10",
     "aku mau X jam 2 sore", "besok ada X", "lusa ada X jam 3"
   Kalau hanya pemicu tanpa detail (mis. "ingetin aku ya"), tetap ADD_ACTIVITY
   dengan missing berisi semua slot yang belum diketahui. JANGAN jatuh ke CHAT.

   URUTAN KATA TIDAK PENTING. Semua ini sama saja:
   - "aku mau ngechat pacar jam 2 sore"
   - "jam 2 sore aku mau ngechat pacar"
   - "jam 2 sore yaa, aku mau ngechat pacar"

   JUDUL HARUS BARU. Judul kegiatan hanya boleh berasal dari pesan user.
   DILARANG KERAS menyalin judul dari "Kegiatan milik user yang tersimpan" di
   atas — daftar itu hanya untuk melacak kegiatan yang mau diubah/dihapus.
   Kalau user belum menyebut judulnya, title dibiarkan "" dan masuk ke missing.

5. UPDATE_ACTIVITY - user ingin MENGUBAH kegiatan yang sudah ada.
   Isi: {{"target": "nama/judul kegiatan", "title": "...", "date": "...", "time": "...", "description": "...", "remind_mins": 30}}
   Field yang tidak berubah boleh dikosongkan tapi tetap cantumkan key-nya.

6. DELETE_ACTIVITY - user ingin MEMBATALKAN/MENGHAPUS kegiatan.
   Isi: {{"target": "nama/judul kegiatan"}}
   Contoh: "ga jadi aku ngedate", "cancel meeting besok", "hapus jadwal dokter".

7. MARK_DONE - menandai kegiatan SUDAH SELESAI.
   Isi: {{"target": "nama/judul kegiatan"}}
   Contoh: "meeting-nya udah kelar", "aku udah kontrol dokter tadi".

8. CHANGE_NAME - user ingin GANTI NAMA PANGGILAN di profil.
   Isi: {{"name": "nama baru"}}
   Contoh: "panggil aku Bella aja", "ganti nama aku ya", "namaku sekarang Gyna".

9. VIEW_AGENDA - user minta MELIHAT jadwal/kegiatan/agenda.
   Isi: {{}}
   Pemicu: "agenda aku apa aja", "ada jadwal apa", "list kegiatan aku",
   "apa aja yang harus aku lakukan", "kegiatan minggu ini apa", "jadwalku dong".

10. CHAT - selain semua di atas: ngobrol santai, tanya jawab, curhat, cerita,
    pujian, atau menanyakan progress makanan (bukan mencatat).
    Isi: {{}}

Contoh keputusan:
- "halo" -> {{"action":"CHAT","slots":{{}},"missing":[],"reply":"halo juga!","confidence":0.99}}
- "tadi makan nasi 200g" -> {{"action":"LOG_FOOD","slots":{{"foods":[{{"name":"nasi putih","amount_g":200,"calories":260,"protein_g":5,"carbs_g":56,"fat_g":1}}]}},"missing":[],"reply":"nasi 200g oke noted!","confidence":0.95}}
- "pengen nasi goreng" -> {{"action":"CHAT","slots":{{}},"missing":[],"reply":"wah pengen nasi goreng nih~","confidence":0.9}}
- "buatin jadwal meeting" -> {{"action":"ADD_ACTIVITY","slots":{{"title":"meeting","date":"","time":""}},"missing":["date","time"],"reply":"boleh! hari dan jam berapa nih?","confidence":0.85}}
- "salah, yang bener nasi gorengnya 150g" -> {{"action":"CORRECT_FOOD","slots":{{"target":"nasi goreng","foods":[{{"name":"nasi goreng","amount_g":150,"calories":240,"protein_g":6,"carbs_g":42,"fat_g":8}}]}},"missing":[],"reply":"oh oke, aku perbaiki ya!","confidence":0.9}}

Balas dengan field yang selalu ada: action, slots, missing, reply, confidence.
`reply` adalah tanggapan singkat ala Ginchiee (maks 2 emoji, tanpa bullet, tanpa "Tentu!")."""


# ── Parsing & fallbacks ───────────────────────────────────────────────────────

_VALID_ACTIONS = set(ALL_ACTIONS)

_REQUIRED_SLOTS = {
    ACTION_LOG_FOOD: ("foods",),
    ACTION_CORRECT_FOOD: ("target", "foods"),
    ACTION_DELETE_FOOD: ("target",),
    ACTION_ADD_ACTIVITY: ("title", "date", "time"),
    ACTION_UPDATE_ACTIVITY: ("target",),
    ACTION_DELETE_ACTIVITY: ("target",),
    ACTION_MARK_DONE: ("target",),
    ACTION_CHANGE_NAME: ("name",),
}


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or isinstance(value, bool):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_int(value: Any, default: int = 30) -> int:
    try:
        if value is None or isinstance(value, bool):
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _normalise_foods(raw: Any) -> list[dict]:
    """Validate the LLM's food list, dropping unusable entries."""
    foods: list[dict] = []
    if not isinstance(raw, list):
        return foods

    for entry in raw:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        foods.append({
            "name": name,
            "amount_g": _coerce_float(entry.get("amount_g"), 100.0) or 100.0,
            "calories": _coerce_float(entry.get("calories"), 0.0),
            "protein_g": _coerce_float(entry.get("protein_g"), 0.0),
            "carbs_g": _coerce_float(entry.get("carbs_g"), 0.0),
            "fat_g": _coerce_float(entry.get("fat_g"), 0.0),
        })

    return foods


def parse_intent(payload: Any) -> Optional[Intent]:
    """Turn a raw model payload into an Intent, or None if unusable."""
    if not isinstance(payload, dict):
        return None

    action = str(payload.get("action") or ACTION_CHAT).strip().upper()
    if action not in _VALID_ACTIONS:
        logger.warning("Router returned unknown action %r, falling back to CHAT", action)
        action = ACTION_CHAT

    raw_slots = payload.get("slots")
    slots: dict = dict(raw_slots) if isinstance(raw_slots, dict) else {}

    confidence = _coerce_float(payload.get("confidence"), 0.0)
    reply = str(payload.get("reply") or "").strip()
    missing_raw = payload.get("missing")
    missing = [str(m) for m in missing_raw] if isinstance(missing_raw, list) else []

    # Normalise the payloads we actually consume.
    if action in (ACTION_LOG_FOOD, ACTION_CORRECT_FOOD):
        slots["foods"] = _normalise_foods(slots.get("foods"))
    if action in (ACTION_ADD_ACTIVITY, ACTION_UPDATE_ACTIVITY, ACTION_DELETE_ACTIVITY,
                  ACTION_MARK_DONE, ACTION_CORRECT_FOOD, ACTION_DELETE_FOOD):
        slots["target"] = str(slots.get("target") or "").strip()
    if action == ACTION_CHANGE_NAME:
        slots["name"] = str(slots.get("name") or "").strip()

    intent = Intent(
        action=action,
        slots=slots,
        missing=missing,
        reply=reply,
        confidence=confidence,
        raw=payload,
    )

    # A delete/mark/correct action with no target at all cannot be executed.
    if action in (ACTION_DELETE_FOOD, ACTION_CORRECT_FOOD, ACTION_DELETE_ACTIVITY,
                  ACTION_MARK_DONE, ACTION_UPDATE_ACTIVITY) and not slots.get("target"):
        logger.warning("Router gave %s without a target; treating as CHAT", action)
        intent.action = ACTION_CHAT
        intent.missing = []

    if action in (ACTION_LOG_FOOD, ACTION_CORRECT_FOOD) and not slots.get("foods"):
        # "sudah makan nasi goreng" but no usable food extracted.
        logger.warning("Router gave %s with no usable foods; treating as CHAT", action)
        intent.action = ACTION_CHAT
        intent.missing = []
        intent.slots = {}

    return intent


def compute_missing(intent: Intent) -> Intent:
    """Fill `missing` for slot-based actions so the handler can ask."""
    required = _REQUIRED_SLOTS.get(intent.action)
    if not required:
        intent.missing = []
        return intent

    already = set(intent.missing)
    for slot in required:
        if slot in (intent.slots or {}):
            value = intent.slots.get(slot)
            has_value = bool(value) if not isinstance(value, (int, float)) else value > 0
            if not has_value:
                already.add(slot)
        else:
            already.add(slot)

    # Foods are only "missing" if there is nothing at all to log.
    intent.missing = sorted(already)
    return intent


async def route_message(
    ai_service,
    message: str,
    profile: Optional[dict],
    recent_activities: list,
    recent_food: list,
    pending_note: str = "",
) -> Intent:
    """Route a message. Never raises: falls back to CHAT."""
    from services.ai_service import AIService  # noqa: F401  (typing clarity)

    prompt = build_route_prompt(message, profile, recent_activities, recent_food, pending_note)

    try:
        payload = await ai_service.generate_json(prompt)
    except Exception as exc:
        logger.error("Intent routing raised: %s", exc, exc_info=True)
        payload = None

    if payload is None:
        # Routing failed (network, quota, unparseable reply). Safer to say so
        # than to silently let a deletion request fall through to small talk —
        # the user must not be left thinking their action happened.
        logger.warning("Intent routing unavailable for %r; reporting busy", message[:60])
        return Intent(
            action=ACTION_CHAT,
            slots={},
            reply=(
                "maaf, aku lagi lembur di sisi server nih jadi belum bisa "
                "memproses itu dengan bener. coba kirim ulangi ya"
            ),
            confidence=0.0,
        )

    intent = parse_intent(payload)
    if intent is None:
        return Intent(action=ACTION_CHAT, slots={}, reply="", confidence=0.0)

    return compute_missing(intent)


def describe_intent(intent: Intent) -> str:
    """Short human-readable summary used for confirmation previews."""
    action = intent.action
    slots = intent.slots or {}

    if action == ACTION_LOG_FOOD:
        names = ", ".join(f.get("name", "makanan") for f in slots.get("foods", []))
        total = sum(f.get("calories", 0) for f in slots.get("foods", []))
        return f"catat makanan: {names} ({total:.0f} kcal)"
    if action == ACTION_CORRECT_FOOD:
        names = ", ".join(f.get("name", "makanan") for f in slots.get("foods", []))
        return f"perbaiki '{slots.get('target')}' jadi {names}"
    if action == ACTION_DELETE_FOOD:
        return f"hapus catatan makanan '{slots.get('target')}'"
    if action == ACTION_ADD_ACTIVITY:
        return f"tambah kegiatan '{slots.get('title')}'"
    if action == ACTION_UPDATE_ACTIVITY:
        return f"ubah kegiatan '{slots.get('target')}'"
    if action == ACTION_DELETE_ACTIVITY:
        return f"hapus kegiatan '{slots.get('target')}'"
    if action == ACTION_MARK_DONE:
        return f"tandai selesai '{slots.get('target')}'"
    if action == ACTION_CHANGE_NAME:
        return f"ganti nama jadi '{slots.get('name')}'"
    if action == ACTION_VIEW_AGENDA:
        return "lihat agenda"
    return "ngobrol"
