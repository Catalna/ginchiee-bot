"""
bot/handlers/message_handler.py
Free-form chat, driven by the intent router.

Flow per message:
    1. If a pending draft (e.g. an activity missing its date) exists, merge the
       message into it before routing.
    2. Route the message to exactly one intent.
    3. Execute directly (LOG_FOOD, VIEW_AGENDA, CHAT) or stage a confirmation
       for anything that changes data (corrections, deletes, renames, activities).

Confirmation state lives in `context.user_data`, keyed by an opaque token with a
TTL, so a stale inline button can never re-trigger an old action and a container
restart simply invalidates whatever was pending.
"""

import inspect
import logging
import re
import secrets
import time
from datetime import date, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, MessageHandler, filters

from services.activity_service import (
    add_activity,
    delete_activity,
    find_activity_by_reference,
    format_activity_datetime,
    get_all_user_activities,
    get_today_activities,
    get_upcoming_activities,
    mark_activity_done,
    unmark_activity_done,
    update_activity,
)
from services.intent_service import (
    ACTION_ADD_ACTIVITY,
    ACTION_CHANGE_NAME,
    ACTION_CHAT,
    ACTION_CORRECT_FOOD,
    ACTION_DELETE_ACTIVITY,
    ACTION_DELETE_FOOD,
    ACTION_LOG_FOOD,
    ACTION_MARK_DONE,
    ACTION_UPDATE_ACTIVITY,
    ACTION_VIEW_AGENDA,
    Intent,
    route_message,
)
from services.nutrition_service import (
    delete_food_log_by_name,
    delete_last_food_log,
    delete_todays_food_logs,
    get_daily_consumed,
    get_daily_progress,
    get_nutrition_history,
    get_recent_food_logs,
    get_today_food_logs,
    log_food_items,
    replace_food_log,
)
from services.personality import strip_markdown_for_telegram
from services.user_service import (
    get_diet_profile,
    get_meal_schedules,
    get_recent_conversations,
    get_user,
    save_conversation,
    update_user_name,
    user_exists,
)

logger = logging.getLogger(__name__)

#: How long a staged confirmation stays valid.
CONFIRM_TTL_MINUTES = 10

#: context.user_data keys.
KEY_PENDING = "ginchiee_pending_confirm"
KEY_DRAFT = "ginchiee_slots_draft"

#: Questions used to collect missing activity slots.
SLOT_QUESTIONS = {
    "title": "judulnya apa nih?",
    "date": "hari apa tanggal berapa?",
    "time": "jam berapa?",
}

#: Slots an activity needs, in the order they should be asked for.
_ACTIVITY_SLOT_ORDER = ("title", "date", "time")

#: Kept for backwards compatibility with older call sites.
_REQUIRED_ACTIVITY_SLOTS = _ACTIVITY_SLOT_ORDER


# ── Formatting helpers ────────────────────────────────────────────────────────

def _md(value) -> str:
    """Escape user-controlled text for legacy Markdown messages."""
    return strip_markdown_for_telegram(str(value))


def _fmt_activity(activity: dict) -> str:
    return f"{activity.get('title')} ({format_activity_datetime(activity.get('activity_dt'))})"


# ── Entry point ───────────────────────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return

    user_id = update.effective_user.id
    text = (update.message.text or "").strip()

    logger.info("Incoming message from user %s: %r", user_id, text)

    if not text:
        return

    if not await user_exists(user_id):
        await update.message.reply_text(
            "halo! kayaknya kita belum kenalan nih~\n"
            "ketik /start untuk mulai ya"
        )
        return

    ai_service = context.bot_data.get("ai_service")
    if ai_service is None:
        logger.error("ai_service missing from bot_data")
        await update.message.reply_text("aku lagi error internal nih, coba lagi sebentar ya")
        return

    try:
        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id, action="typing"
        )
    except Exception as exc:
        logger.debug("Failed to send typing action: %s", exc)

    draft = context.user_data.get(KEY_DRAFT)
    pending_note = _draft_note(draft) if draft else ""

    intent = await route_message(
        ai_service=ai_service,
        message=text,
        profile=await _profile_block(user_id),
        recent_activities=await get_all_user_activities(user_id, limit=10),
        recent_food=await get_recent_food_logs(user_id, limit=6),
        pending_note=pending_note,
    )

    # A pending activity draft absorbs follow-up slot-filling messages.
    if draft:
        merged = _merge_draft(draft, intent, text)

        # Deterministic safety net: the model sometimes answers CHAT for a plain
        # "Hari Minggu, 11 Oktober 2026" while a draft is open, which used to
        # strand the user mid-flow. Extract dates/times locally so slot filling
        # never depends on the model remembering the draft.
        merged = _fill_slots_locally(merged, text)

        if _draft_complete(merged):
            context.user_data.pop(KEY_DRAFT, None)
            await _finish_activity_draft(update, context, user_id, merged, text)
            return

        # The user switched to an unrelated topic: drop the draft.
        if intent.action not in (ACTION_ADD_ACTIVITY,) and not _looks_like_slot_answer(text):
            context.user_data.pop(KEY_DRAFT, None)
        else:
            context.user_data[KEY_DRAFT] = merged
            await _ask_for_missing(update, merged)
            return

    if intent.action == ACTION_LOG_FOOD:
        await _execute_log_food(update, context, user_id, text, intent, ai_service)
        return

    if intent.action == ACTION_VIEW_AGENDA:
        await _show_agenda(update, user_id)
        await _remember(user_id, text, "menampilkan agenda")
        return

    if intent.action == ACTION_CHAT:
        await _handle_chat(update, context, user_id, text, ai_service)
        return

    # ── Actions that write data ──────────────────────────────────────────────
    #
    # Adding an activity behaves like logging food: it happens straight away.
    # Asking "are you sure?" before saving something the user just said out
    # loud, then asking again on the result, is what made this feel like double
    # work. Deletion and corrections stay behind a confirmation because those
    # are destructive and easy to trigger by accident.
    if intent.action == ACTION_ADD_ACTIVITY and intent.is_complete:
        await _apply_confirmed_action(
            user_id,
            intent,
            reply_fn=lambda message, keyboard: update.message.reply_text(
                message, parse_mode="Markdown", reply_markup=keyboard
            ),
        )
        await _remember(user_id, text, "kegiatan disimpan")
        return

    if not intent.is_complete:
        context.user_data[KEY_DRAFT] = {
            "action": intent.action,
            "slots": intent.slots,
            "missing": intent.missing,
            "started_at": time.time(),
        }
        await _ask_for_missing(
            update, {"slots": intent.slots, "missing": intent.missing}
        )
        return

    await _stage_confirmation(update, context, user_id, intent, text)


# ── Direct actions ────────────────────────────────────────────────────────────

async def _execute_log_food(update, context, user_id, text, intent, ai_service) -> None:
    from nutrition.parser import ParsedFoodItem

    items = [
        ParsedFoodItem(
            food_name=food["name"],
            amount_g=food["amount_g"],
            unit="porsi",
            amount_raw=f"{food['amount_g']:.0f}g",
            calories=food["calories"],
            protein_g=food["protein_g"],
            carbs_g=food["carbs_g"],
            fat_g=food["fat_g"],
        )
        for food in intent.slots.get("foods", [])
    ]

    logged = await log_food_items(user_id, items)

    profile = await get_diet_profile(user_id)
    progress = await get_daily_progress(user_id) if profile else None
    history = await get_nutrition_history(user_id, days=5) if profile else []

    response = await ai_service.generate_food_response(
        logged_items=logged,
        daily_progress=progress,
        nutrition_history=history,
    )

    lines = ["✅ *oke, aku catat ya!*"]
    total_cal = 0
    for item in logged:
        total_cal += item["calories"]
        lines.append(
            f"• {_md(str(item['food_name']).capitalize())} ({item['amount_g']:.0f}g) — "
            f"{item['calories']:.0f} kcal (P: {item['protein_g']:.1f}g | "
            f"K: {item['carbs_g']:.1f}g | L: {item['fat_g']:.1f}g)"
        )
    lines.append(f"\n🔥 *Total: {total_cal:.0f} kcal*")
    lines.append("\n_/today buat lihat progress lengkap_")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    await update.message.reply_text(response)
    await _remember(user_id, text, "\n".join(lines) + "\n" + response)


async def _handle_chat(update, context, user_id, text, ai_service) -> None:
    history = await get_recent_conversations(user_id)
    chat_context = await _build_chat_context(user_id)

    response = await ai_service.chat(message=text, context=chat_context, history=history)
    await update.message.reply_text(response)
    await _remember(user_id, text, response)


async def _show_agenda(update, user_id: int) -> None:
    today = await get_today_activities(user_id)
    upcoming = await get_upcoming_activities(user_id, days=7)
    today_ids = {a["id"] for a in today}
    future = [a for a in upcoming if a["id"] not in today_ids]

    if not today and not future:
        await update.message.reply_text(
            "agendamu masih kosong nih. coba bilang aja kayak "
            "'ingetin aku besok jam 10 meeting' 😊"
        )
        return

    lines = ["📅 *agenda kamu*\n"]
    buttons: list[list[InlineKeyboardButton]] = []

    def _entry(act: dict) -> str:
        marker = "✅" if act.get("done") else "📌"
        return (
            f"{marker} *{_md(act.get('title'))}*\n   🕒 "
            f"{format_activity_datetime(act.get('activity_dt'))}"
        )

    if today:
        lines.append("✨ *hari ini:*")
        for act in today:
            lines.append(_entry(act))
            buttons.append(_activity_buttons(act))

    if future:
        lines.append("\n🗓️ *7 hari ke depan:*")
        for act in future:
            lines.append(_entry(act))
            buttons.append(_activity_buttons(act))

    lines.append(
        "\n💡 _ketuk tombol di bawah buat tandai selesai atau hapus_"
    )

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
    )


def _activity_buttons(act: dict) -> list[InlineKeyboardButton]:
    """Per-activity actions shown under the agenda listing."""
    activity_id = act["id"]
    if act.get("done"):
        return [
            InlineKeyboardButton(
                "↩️ batalkan selesai", callback_data=f"undone:{activity_id}"
            )
        ]
    return [
        InlineKeyboardButton("✅ selesai", callback_data=f"done:{activity_id}"),
        InlineKeyboardButton("🗑️ hapus", callback_data=f"del_activity:{activity_id}"),
    ]


async def _build_chat_context(user_id: int) -> dict:
    profile = await get_diet_profile(user_id)
    user = await get_user(user_id)

    return {
        "profile": {
            **(dict(profile) if profile else {}),
            "name": user["name"] if user else "User",
        },
        "target": None,
        "consumed": await get_daily_consumed(user_id),
        "food_logs": await get_today_food_logs(user_id),
        "schedules": await get_meal_schedules(user_id),
        "upcoming_activities": await get_upcoming_activities(user_id, days=7),
        "nutrition_history": await get_nutrition_history(user_id, days=7),
        "adaptive_target": None,
    }


async def _profile_block(user_id: int) -> dict:
    profile = await get_diet_profile(user_id)
    user = await get_user(user_id)
    if not profile:
        return {"name": user["name"] if user else "User"}
    return {**dict(profile), "name": user["name"] if user else "User"}


async def _remember(user_id: int, user_text: str, bot_text: str) -> None:
    await save_conversation(user_id, "user", user_text)
    await save_conversation(user_id, "model", bot_text)


# ── Slot filling ──────────────────────────────────────────────────────────────

def _draft_note(draft: dict) -> str:
    missing = draft.get("missing") or []
    if not missing:
        return ""
    slots = draft.get("slots") or {}
    joined_questions = " lalu ".join(SLOT_QUESTIONS.get(slot, slot) for slot in missing)
    return (
        f"\nKamu sedang melengkapi data kegiatan BARU "
        f"({slots.get('title') or 'judul belum ada'}). "
        f"Masih kurang: {', '.join(missing)} (pertanyaan: {joined_questions}). "
        f"Jika pesan user menjawab itu (misalnya angka jam atau tanggal), "
        f"gabungkan ke draft dan balas action ADD_ACTIVITY dengan slots yang sudah lengkap. "
        f"PENTING: judul kegiatan BARU hanya boleh diambil dari pesan user, "
        f"JANGAN pernah menyalin judul dari daftar 'Kegiatan milik user yang tersimpan'. "
        f"Kalau user berganti topik, balas action sesuai topik baru."
    )


def _looks_like_slot_answer(text: str) -> bool:
    """Whether a message plausibly supplies a missing slot value.

    Used to decide whether to keep an open draft alive. Deliberately narrow:
    while a draft is open, a bare "besok" or "jam 3" is part of it, but a
    sentence about something else should be allowed to start a new topic.
    """
    stripped = (text or "").strip()
    if not stripped or stripped.startswith("/"):
        return False

    lowered = stripped.lower()

    # Signals that the user is changing the subject entirely.
    for cue in ("hadeh", "lupakan", "udahan", "stop", "batal", "nggajadi", "nggak jadi"):
        if cue in lowered:
            return False

    # Anything short is treated as an answer to the pending question.
    if len(stripped.split()) <= 8:
        return True

    # Longer messages still count when they carry a concrete date/time.
    return bool(
        _CLOCK_HOUR.search(lowered)
        or _BARE_TIME.search(lowered)
        or _DATE_DAY_MONTH.search(lowered)
        or _DATE_NUMERIC.search(lowered)
        or any(word in lowered for word in ("besok", "lusa", "hari ini", "minggu", "senin",
                                            "selasa", "rabu", "kamis", "jumat", "sabtu"))
    )


#: Words that carry no meaning of their own when answering "jam berapa?".
_SCHEDULE_FILLER = {
    "jam", "pukul", "hari", "tanggal", "tgl", "nih", "ya", "dong", "aja", "saja",
    "aku", "kamu", "mau", "ada", "ginchiee", "oke", "oh", "eh", "yang", "ini",
    "itu", "buat", "buatin", "to", "dari", "di", "ke", "jadi", "deh", "sih",
    "pagi", "siang", "sore", "malam", "petang", "subuh", "dini", "wib",
    "besok", "lusa", "minggu", "senin", "selasa", "rabu", "kamis", "jumat",
    "sabtu", "oktober", "okt", "januari", "jan", "februari", "feb", "maret",
    "mar", "april", "apr", "mei", "juni", "jun", "juli", "jul", "agustus",
    "agu", "september", "sep", "november", "nov", "desember", "des",
}


def _content_beyond_schedule(text: str) -> str:
    """Strip date/time tokens, weekday names, and filler words.

    Returns whatever is left, which is only meaningful when it is empty: the
    message then said nothing that could be a title. "Jam 9 pagi ginchiee"
    leaves nothing, "ya betul, exam software engineer" leaves the title.
    """
    cleaned = text or ""

    for pattern in (_CLOCK_HOUR, _BARE_TIME, _DATE_DAY_MONTH, _DATE_NUMERIC):
        cleaned = pattern.sub(" ", cleaned)

    for word in _SCHEDULE_FILLER:
        cleaned = re.sub(
            r"\b" + re.escape(word) + r"\b", " ", cleaned, flags=re.IGNORECASE
        )

    # Digits left over from a partial match ("12", "Okt").
    cleaned = re.sub(r"\b\d+\b", " ", cleaned)

    leftover = re.sub(r"\s+", " ", cleaned).strip()
    # Punctuation-only leftovers ("Hari Minggu,") are not content.
    if not re.search(r"[a-z0-9]", leftover, re.IGNORECASE):
        return ""
    return leftover


def _is_pure_schedule_answer(text: str) -> bool:
    """True when the message only supplies a date/time (and no title at all).

    Enforced locally so a prompt reword cannot undo it: a model-supplied title
    is simply not accepted for these messages.
    """
    lowered = (text or "").lower()

    has_schedule_token = bool(
        _CLOCK_HOUR.search(lowered)
        or _BARE_TIME.search(lowered)
        or _DATE_DAY_MONTH.search(lowered)
        or _DATE_NUMERIC.search(lowered)
        or any(
            word in lowered
            for word in ("besok", "lusa", "hari ini", "minggu", "senin", "selasa",
                         "rabu", "kamis", "jumat", "sabtu")
        )
    )
    if not has_schedule_token:
        return False

    return not _content_beyond_schedule(text)


def _merge_draft(draft: dict, intent: Intent, text: str = "") -> dict:
    """Fold newly-detected slot values into a pending draft."""
    merged = {
        "action": ACTION_ADD_ACTIVITY,
        "slots": dict(draft.get("slots") or {}),
        "missing": list(draft.get("missing") or []),
        "started_at": draft.get("started_at", time.time()),
    }

    incoming = intent.slots or {}

    # A pure schedule answer ("Jam 9 pagi ginchiee") must never supply a title.
    # Without this, the model copied the title of an already-saved activity
    # from the context list and a brand-new event was created with the old
    # name. This is enforced locally so prompt wording cannot undo it.
    accept_title = not _is_pure_schedule_answer(text) if text else True

    # Only fill blanks; never overwrite what the user already provided.
    for key in ("title", "date", "time", "description", "remind_mins", "target"):
        if key == "title" and not accept_title:
            continue
        value = incoming.get(key)
        if value in (None, "", 0):
            continue
        if not merged["slots"].get(key):
            merged["slots"][key] = value

    merged["missing"] = [
        slot for slot in _ACTIVITY_SLOT_ORDER if not merged["slots"].get(slot)
    ]
    return merged


def _draft_complete(draft: dict) -> bool:
    return not (draft.get("missing") or [])


# ── Deterministic slot extraction ─────────────────────────────────────────────
#
# Filling the date/time of a half-finished activity used to depend entirely on
# the model, which answered CHAT for plain "Hari Minggu, 11 Oktober 2026" and
# left the user stranded mid-flow. The clock is interchangeable, the schedule is
# not, so parse it locally.

_TIME_WORDS = {
    "pagi": 9, "subuh": 5, "dini": 5, "siang": 12, "sore": 15,
    "petang": 17, "malam": 20, "begadang": 23,
}

_DAY_WORDS = {
    "senin": 0, "selasa": 1, "rabu": 2, "kamis": 3,
    "jumat": 4, "jum'at": 4, "sabtu": 5, "minggu": 6,
}

_MONTH_WORDS = {
    "januari": 1, "februari": 2, "maret": 3, "april": 4, "mei": 5, "juni": 6,
    "juli": 7, "agustus": 8, "september": 9, "oktober": 10, "november": 11, "desember": 12,
}

_BARE_TIME = re.compile(r"\b([01]?\d|2[0-3])[.:]([0-5]\d)\b")
_CLOCK_HOUR = re.compile(r"\b(?:jam|pukul)\s+([01]?\d|2[0-3])(?:[.:]([0-5]\d))?\b")
_DATE_NUMERIC = re.compile(r"\b(\d{1,2})[-/](\d{1,2})(?:[-/](\d{2,4}))?\b")
_DATE_DAY_MONTH = re.compile(
    r"\b(\d{1,2})\s+([a-z]+)\s*(?:(\d{4}))?\b", re.IGNORECASE
)


def _extract_time(text: str) -> str | None:
    """Pull an HH:MM out of free text, including 'jam 3 sore'."""
    lowered = text.lower()

    match = _CLOCK_HOUR.search(lowered)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        # "jam 3 sore" means 15:00, not 03:00.
        for word, shift in sorted(_TIME_WORDS.items(), key=lambda kv: -kv[1]):
            if word in lowered and hour < 12 and shift >= 12:
                hour += 12
                break
        return f"{hour:02d}:{minute:02d}"

    match = _BARE_TIME.search(lowered)
    if match:
        return f"{int(match.group(1)):02d}:{int(match.group(2)):02d}"

    for word, hour in sorted(_TIME_WORDS.items(), key=lambda kv: -kv[1]):
        if word in lowered and "jam" not in lowered and "pukul" not in lowered:
            return f"{hour:02d}:00"
    return None


def _extract_date(text: str, today: date) -> str | None:
    """Pull a YYYY-MM-DD out of free text."""
    lowered = text.lower()

    # "Hari Minggu, 11 Oktober 2026" / "11 Oktober"
    match = _DATE_DAY_MONTH.search(lowered)
    if match:
        day = int(match.group(1))
        month = _MONTH_WORDS.get(match.group(2))
        if month:
            year = int(match.group(3)) if match.group(3) else today.year
            try:
                return date(year, month, day).isoformat()
            except ValueError:
                pass

    # "11/10", "11-10", or "11-10-2026". Day/month order (Indonesian habit).
    match = _DATE_NUMERIC.search(lowered)
    if match:
        day, month = int(match.group(1)), int(match.group(2))
        raw_year = match.group(3)
        if raw_year:
            year = int(raw_year)
            if year < 100:
                year += 2000
        else:
            year = today.year
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            pass

    # Weekday names resolve to the NEXT occurrence, so a reminder is never
    # silently scheduled in the past.
    for word, weekday in _DAY_WORDS.items():
        if word in lowered:
            delta = (weekday - today.weekday()) % 7
            delta = delta or 7
            return (today + timedelta(days=delta)).isoformat()

    if "hari ini" in lowered:
        return today.isoformat()
    if "besok" in lowered:
        return (today + timedelta(days=1)).isoformat()
    if "lusa" in lowered:
        return (today + timedelta(days=2)).isoformat()

    return None


def _fill_slots_locally(draft: dict, text: str) -> dict:
    """Fill empty date/time slots from the raw message without calling the model."""
    slots = dict(draft.get("slots") or {})
    today = _today_for_draft()

    if not slots.get("time"):
        found = _extract_time(text)
        if found:
            slots["time"] = found

    if not slots.get("date"):
        found = _extract_date(text, today)
        if found:
            slots["date"] = found

    draft = dict(draft)
    draft["slots"] = slots
    draft["missing"] = [
        slot for slot in _REQUIRED_ACTIVITY_SLOTS if not slots.get(slot)
    ]
    return draft


def _today_for_draft() -> date:
    return date.today()


async def _finish_activity_draft(update, context, user_id, draft, text) -> None:
    """Save a fully-specified activity and reply with its undo/delete buttons."""
    await _apply_confirmed_action(
        user_id,
        Intent(
            action=ACTION_ADD_ACTIVITY,
            slots=draft["slots"],
            missing=[],
        ),
        reply_fn=lambda message, keyboard: update.message.reply_text(
            message, parse_mode="Markdown", reply_markup=keyboard
        ),
    )
    await _remember(user_id, text, "kegiatan disimpan")


async def _ask_for_missing(update: Update, draft: dict) -> None:
    """Ask for whatever an activity is still missing, in Ginchiee's voice.

    Shows what is already known so the user can see the draft taking shape,
    instead of the same "boleh, aku catatin~" line on every turn.
    """
    slots = draft.get("slots") or {}
    missing = draft.get("missing") or list(_REQUIRED_ACTIVITY_SLOTS)

    known: list[str] = []
    if slots.get("title"):
        known.append(f"📌 *{_md(slots['title'])}*")
    if slots.get("date"):
        known.append(f"🗓️ {slots['date']}")
    elif slots.get("time"):
        known.append(f"🕒 {slots['time']}")

    # Ask for the highest-priority missing slot, and list the rest so the user
    # knows exactly what is still outstanding.
    question = SLOT_QUESTIONS.get(missing[0], missing[0])
    if len(missing) > 1:
        lines = [
            "oke, ini yang aku tangkep sejauh ini:",
            *known,
            "",
            f"{question}",
            "",
            f"_masih kurang: {', '.join(SLOT_QUESTIONS.get(m, m) for m in missing)}_",
        ]
    else:
        lines = [
            "oke, ini yang aku tangkep sejauh ini:",
            *known,
            "",
            f"{question}",
        ]

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    await _remember(
        update.effective_user.id, update.message.text or "", "\n".join(lines)
    )


# ── Confirmation ──────────────────────────────────────────────────────────────

def _new_token() -> str:
    return secrets.token_urlsafe(8)[:10]


async def _stage_confirmation(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    intent: Intent,
    text: str,
) -> None:
    """Build a preview, store it, and ask the user to confirm."""
    preview = await _build_preview(user_id, intent)

    if preview is None:
        await update.message.reply_text(
            "hmm, aku nggak nemu yang kamu maksud di catatanmu 😅 "
            "cek /agenda atau /today dulu ya"
        )
        return

    token = _new_token()
    context.user_data[KEY_PENDING] = {
        "token": token,
        "intent": intent.action,
        "slots": intent.slots,
        "preview_text": preview,
        "started_at": time.time(),
    }

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ iya, lanjut", callback_data=f"confirm:{token}:yes"),
            InlineKeyboardButton("❌ batal", callback_data=f"confirm:{token}:no"),
        ]
    ])

    await update.message.reply_text(
        f"jadi mau aku {preview}\n\nbener gitu? konfirmasi dulu ya~",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )
    await _remember(user_id, text, preview)


async def _build_preview(user_id: int, intent: Intent) -> str | None:
    """Human-readable 'what will change' preview. None if unresolvable."""
    slots = intent.slots or {}
    action = intent.action

    if action == ACTION_CHANGE_NAME:
        return f"ganti nama panggilan kamu jadi *{_md(slots.get('name'))}*"

    if action == ACTION_DELETE_FOOD:
        target = str(slots.get("target") or "").strip().lower()
        if target in ("semua", "all"):
            logs = await get_recent_food_logs(user_id, limit=100)
            if not logs:
                return None
            total = sum(float(item["calories"] or 0) for item in logs)
            return f"hapus *semua* catatan makanan hari ini ({len(logs)} item, {total:.0f} kcal)"
        if target in ("terakhir", "last"):
            logs = await get_recent_food_logs(user_id, limit=1)
            if not logs:
                return None
            return f"hapus catatan terakhir (*{_md(logs[0]['food_name'])}*)"
        logs = await get_recent_food_logs(user_id, limit=100)
        match = _match_food(logs, target)
        if not match:
            return None
        return f"hapus *{_md(match['food_name'])}* ({match['calories']:.0f} kcal) dari catatan kamu"

    if action == ACTION_CORRECT_FOOD:
        logs = await get_recent_food_logs(user_id, limit=100)
        match = _match_food(logs, str(slots.get("target") or ""))
        if not match:
            return None
        foods = slots.get("foods") or []
        new_text = ", ".join(
            f"{f['name']} {f['amount_g']:.0f}g ({f['calories']:.0f} kcal)" for f in foods
        )
        return (
            f"ganti catatan *{_md(match['food_name'])}* "
            f"({match['calories']:.0f} kcal) jadi {_md(new_text)}"
        )

    if action in (ACTION_ADD_ACTIVITY, ACTION_UPDATE_ACTIVITY):
        title = str(slots.get("title") or "").strip()
        if not title:
            return None
        when = _compose_datetime(slots.get("date"), slots.get("time"))
        if not when:
            return None

        if action == ACTION_UPDATE_ACTIVITY:
            existing = await find_activity_by_reference(user_id, str(slots.get("target") or ""))
            if not existing:
                return None

        verb = "tambah" if action == ACTION_ADD_ACTIVITY else "ubah"
        remind = int(slots.get("remind_mins", 30) or 30)
        return (
            f"{verb} kegiatan *{_md(title)}*\n"
            f"🕒 {format_activity_datetime(when)}\n"
            f"⏰ aku ingatkan {remind} menit sebelumnya"
        )

    if action == ACTION_DELETE_ACTIVITY:
        existing = await find_activity_by_reference(user_id, str(slots.get("target") or ""))
        if not existing:
            return None
        return (
            f"hapus kegiatan *{_md(existing.get('title'))}* "
            f"({format_activity_datetime(existing.get('activity_dt'))})"
        )

    if action == ACTION_MARK_DONE:
        existing = await find_activity_by_reference(user_id, str(slots.get("target") or ""))
        if not existing:
            return None
        return f"tandai kegiatan *{_md(existing.get('title'))}* sebagai selesai"

    return None


def _match_food(logs: list[dict], target: str) -> dict | None:
    needle = (target or "").strip().lower()
    if not needle:
        return None
    for log in logs:
        if needle in str(log.get("food_name", "")).lower():
            return log
    for log in logs:
        if str(log.get("food_name", "")).lower() in needle:
            return log
    return None


def _compose_datetime(date_str, time_str) -> str | None:
    """Combine 'YYYY-MM-DD' and 'HH:MM' into an ISO datetime string."""
    if not date_str or not time_str:
        return None
    date_part = str(date_str).strip()[:10]
    time_part = str(time_str).strip()[:5]
    if len(time_part) == 4 and ":" in time_part:
        hour, minute = time_part.split(":")
        time_part = f"{hour.zfill(2)}:{minute}"
    return f"{date_part}T{time_part}:00"


# ── Callback handlers ────────────────────────────────────────────────────────

async def handle_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.answer()

    data = query.data or ""
    try:
        _, token, decision = data.split(":", 2)
    except ValueError:
        await query.edit_message_text("tombolnya agak aneh nih, coba lagi ya")
        return

    pending = context.user_data.get(KEY_PENDING)
    if not pending or pending.get("token") != token:
        await query.edit_message_text(
            "konfirmasi ini sudah kadaluarsa. kirim ulangi aja ya"
        )
        return

    context.user_data.pop(KEY_PENDING, None)

    if decision != "yes":
        await query.edit_message_text("oke, aku batalkan. ada lagi? 😊")
        return

    intent = Intent(
        action=pending.get("intent", ACTION_CHAT),
        slots=pending.get("slots") or {},
        missing=[],
    )

    try:
        message, keyboard = await _apply_confirmed_action(
            update.effective_user.id, intent,
            reply_fn=lambda m, k: query.edit_message_text(
                m, parse_mode="Markdown", reply_markup=k
            ),
        )
    except Exception as exc:
        logger.error("Confirmed action failed: %s", exc, exc_info=True)
        message, keyboard = "aduh, gagal dieksekusi nih. coba lagi ya", None

    await query.edit_message_text(
        message, parse_mode="Markdown", reply_markup=keyboard
    )
    await _remember(update.effective_user.id, f"[konfirmasi {intent.action}]", message)


async def handle_done_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id

    try:
        activity_id = int((query.data or "").split(":", 1)[1])
    except (ValueError, IndexError):
        await query.edit_message_text("id-nya aneh, coba lagi ya")
        return

    updated = await mark_activity_done(activity_id, user_id)
    if not updated:
        await query.edit_message_text("hmm, kegiatannya nggak ketemu")
        return

    # Re-render the entry in place so the row now reads as completed.
    await query.edit_message_text(
        f"✅ *{_md(updated['title'])}* selesai ya. mantap! 🎉\n"
        f"🕒 {format_activity_datetime(updated['activity_dt'])}",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([_activity_buttons(updated)]),
    )
    await _remember(user_id, "[tombol tandai selesai]", f"{updated['title']} selesai")


async def handle_undone_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id

    try:
        activity_id = int((query.data or "").split(":", 1)[1])
    except (ValueError, IndexError):
        await query.edit_message_text("id-nya aneh, coba lagi ya")
        return

    updated = await unmark_activity_done(activity_id, user_id)
    if not updated:
        await query.edit_message_text("hmm, kegiatannya nggak ketemu")
        return

    await query.edit_message_text(
        f"📌 *{_md(updated['title'])}*\n"
        f"🕒 {format_activity_datetime(updated['activity_dt'])}\n\n"
        f"_oke, aku aktifin lagi ya_",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([_activity_buttons(updated)]),
    )
    await _remember(user_id, "[tombol batalkan selesai]", f"{updated['title']} aktif lagi")


async def _apply_confirmed_action(user_id: int, intent: Intent, reply_fn=None):
    """Run an action and return (message, keyboard_or_None).

    When `reply_fn` is given the result is also delivered through it, which
    lets the same body serve both a fresh chat reply (an activity added
    straight away) and the confirmation callback (which edits the button
    message itself and passes None).
    """
    message, keyboard = await _run_action(user_id, intent)

    if reply_fn is None:
        return message, keyboard

    try:
        result = reply_fn(message, keyboard)
        if inspect.isawaitable(result):
            await result
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to deliver action reply: %s", exc, exc_info=True)

    return message, keyboard


async def _run_action(user_id: int, intent: Intent):
    """Execute an action. Returns (message, keyboard_or_None)."""
    slots = intent.slots or {}
    action = intent.action

    if action == ACTION_CHANGE_NAME:
        updated = await update_user_name(user_id, str(slots.get("name")))
        if not updated:
            return "hmm, gagal ganti nama. coba lagi ya", None
        return (
            f"✅ oke, sekarang aku panggil kamu *{_md(updated['name'])}*! "
            f"boleh panggil aku Ginchiee ya~",
            None,
        )

    if action == ACTION_DELETE_FOOD:
        target = str(slots.get("target") or "").strip().lower()
        if target in ("semua", "all"):
            removed = await delete_todays_food_logs(user_id)
            if not removed:
                return "nggak ada catatan makanan hari ini", None
            total = sum(float(r.get("calories") or 0) for r in removed)
            return f"✅ sudah aku hapus {len(removed)} catatan makanan ({total:.0f} kcal)", None

        if target in ("terakhir", "last"):
            removed = await delete_last_food_log(user_id)
            if not removed:
                return "nggak ada catatan yang bisa dihapus", None
            return (
                f"✅ sudah aku hapus *{_md(removed['food_name'])}* "
                f"({removed['calories']:.0f} kcal)",
                None,
            )

        removed = await delete_food_log_by_name(user_id, str(slots.get("target")))
        if not removed:
            return "hmm, aku nggak nemu catatan itu. mungkin udah kehapus ya", None
        total = sum(float(r.get("calories") or 0) for r in removed)
        return (
            f"✅ sudah aku hapus *{_md(removed[0]['food_name'])}* ({total:.0f} kcal)",
            None,
        )

    if action == ACTION_CORRECT_FOOD:
        logs = await get_recent_food_logs(user_id, limit=100)
        match = _match_food(logs, str(slots.get("target") or ""))
        if not match:
            return "hmm, catatan yang mau diperbaiki nggak ketemu", None

        from nutrition.parser import ParsedFoodItem

        items = [
            ParsedFoodItem(
                food_name=f["name"],
                amount_g=f["amount_g"],
                unit="porsi",
                amount_raw=f"{f['amount_g']:.0f}g",
                calories=f["calories"],
                protein_g=f["protein_g"],
                carbs_g=f["carbs_g"],
                fat_g=f["fat_g"],
            )
            for f in slots.get("foods") or []
        ]
        result = await replace_food_log(user_id, match["id"], items)
        if not result or not result.get("items"):
            return "gagal memperbaiki catatannya. coba lagi ya", None

        old = result["old_totals"]
        new = result["totals"]
        names = ", ".join(_md(item["food_name"]) for item in result["items"])
        return (
            f"✅ udah aku perbaiki!\n\n"
            f"sebelumnya: {old['amount_g']:.0f}g ({old['calories']:.0f} kcal)\n"
            f"sekarang: {new['amount_g']:.0f}g ({new['calories']:.0f} kcal)\n"
            f"• {names}\n\n"
            f"_/today buat lihat total terbaru_",
            None,
        )

    if action == ACTION_ADD_ACTIVITY:
        when = _compose_datetime(slots.get("date"), slots.get("time"))
        if not when:
            return "tanggal atau jamnya masih kurang lengkap nih", None

        title = str(slots.get("title") or "").strip()
        if not title:
            return "judul kegiatannya apa nih? aku belum tangkep", None

        remind = int(slots.get("remind_mins", 30) or 30)
        activity_id = await add_activity(
            user_id=user_id,
            title=title,
            activity_dt=when,
            description=str(slots.get("description") or ""),
            remind_mins=remind,
        )

        message = (
            f"✅ oke, udah aku catat!\n\n"
            f"📌 *{_md(title)}*\n"
            f"🕒 {format_activity_datetime(when)} WIB\n"
            f"⏰ aku ingatkan {remind} menit sebelumnya 🔔"
        )
        return message, InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ selesai", callback_data=f"done:{activity_id}"),
            InlineKeyboardButton("🗑️ hapus", callback_data=f"del_activity:{activity_id}"),
        ]])

    if action == ACTION_UPDATE_ACTIVITY:
        existing = await find_activity_by_reference(user_id, str(slots.get("target") or ""))
        if not existing:
            return "kegiatan yang mau diubah nggak ketemu", None
        when = _compose_datetime(slots.get("date"), slots.get("time"))
        updated = await update_activity(
            activity_id=existing["id"],
            user_id=user_id,
            title=slots.get("title") or None,
            activity_dt=when,
            description=slots.get("description") or None,
            remind_mins=int(slots["remind_mins"]) if slots.get("remind_mins") else None,
        )
        if not updated:
            return "gagal memperbarui kegiatannya", None
        return (
            f"✅ udah aku ubah!\n\n📌 *{_md(updated['title'])}*\n"
            f"🕒 {format_activity_datetime(updated['activity_dt'])} WIB",
            None,
        )

    if action == ACTION_DELETE_ACTIVITY:
        existing = await find_activity_by_reference(user_id, str(slots.get("target") or ""))
        if not existing:
            return "kegiatan yang mau dihapus nggak ketemu", None
        deleted = await delete_activity(existing["id"], user_id)
        if not deleted:
            return "gagal menghapus kegiatan itu", None
        return f"✅ oke, jadwal *{_md(existing.get('title'))}* sudah aku hapus 🗑️", None

    if action == ACTION_MARK_DONE:
        existing = await find_activity_by_reference(user_id, str(slots.get("target") or ""))
        if not existing:
            return "kegiatannya nggak ketemu", None
        updated = await mark_activity_done(existing["id"], user_id)
        if not updated:
            return "gagal menandai kegiatannya", None
        return f"✅ *{_md(updated['title'])}* ditandai selesai. mantap! 🎉", None

    return "aksi itu belum aku kenal nih", None


def build_message_handlers():
    return [
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message),
    ]


