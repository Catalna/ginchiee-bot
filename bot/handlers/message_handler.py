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

import logging
import secrets
import time

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

_REQUIRED_ACTIVITY_SLOTS = ("title", "date", "time")


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
        if intent.action == ACTION_ADD_ACTIVITY or _looks_like_slot_answer(text):
            merged = _merge_draft(draft, intent)
            if _draft_complete(merged):
                context.user_data.pop(KEY_DRAFT, None)
                await _stage_confirmation(
                    update, context, user_id,
                    Intent(action=ACTION_ADD_ACTIVITY, slots=merged["slots"], missing=[]),
                    text,
                )
            else:
                context.user_data[KEY_DRAFT] = merged
                await _ask_for_missing(update, merged)
            return

        # Different topic: drop the stale draft and continue with the new intent.
        context.user_data.pop(KEY_DRAFT, None)

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

    # Everything below changes data and must be confirmed first.
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
    if today:
        lines.append("✨ *hari ini:*")
        for act in today:
            marker = "✅" if act.get("done") else "📌"
            lines.append(
                f"{marker} *{_md(act.get('title'))}*\n   🕒 "
                f"{format_activity_datetime(act.get('activity_dt'))}"
            )

    if future:
        lines.append("\n🗓️ *7 hari ke depan:*")
        for act in future:
            marker = "✅" if act.get("done") else "📌"
            lines.append(
                f"{marker} *{_md(act.get('title'))}*\n   🕒 "
                f"{format_activity_datetime(act.get('activity_dt'))}"
            )

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


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
        f"\nKamu sedang melengkapi data kegiatan "
        f"({slots.get('title') or 'judul belum ada'}). "
        f"Masih kurang: {', '.join(missing)} (pertanyaan: {joined_questions}). "
        f"Jika pesan user menjawab itu (misalnya angka jam atau tanggal), "
        f"gabungkan ke draft dan balas action ADD_ACTIVITY dengan slots yang sudah lengkap. "
        f"Kalau user berganti topik, balas action sesuai topik baru."
    )


def _looks_like_slot_answer(text: str) -> bool:
    """Whether a message plausibly supplies a missing slot value."""
    stripped = text.strip()
    if not stripped:
        return False
    if stripped.startswith("/"):
        return False
    words = stripped.split()
    if len(words) <= 6:
        return True
    # A longer sentence can still be "besok jam 3 sore ya meetingnya di kafe".
    return any(token.isdigit() for token in stripped) if len(words) <= 14 else False


def _merge_draft(draft: dict, intent: Intent) -> dict:
    """Fold newly-detected slot values into a pending draft."""
    merged = {
        "action": ACTION_ADD_ACTIVITY,
        "slots": dict(draft.get("slots") or {}),
        "missing": list(draft.get("missing") or []),
        "started_at": draft.get("started_at", time.time()),
    }

    incoming = intent.slots or {}
    # Only fill blanks; never overwrite what the user already provided.
    for key in ("title", "date", "time", "description", "remind_mins", "target"):
        value = incoming.get(key)
        if value in (None, "", 0):
            continue
        if not merged["slots"].get(key):
            merged["slots"][key] = value

    merged["missing"] = [
        slot for slot in _REQUIRED_ACTIVITY_SLOTS if not merged["slots"].get(slot)
    ]
    return merged


def _draft_complete(draft: dict) -> bool:
    return not (draft.get("missing") or [])


async def _ask_for_missing(update: Update, draft: dict) -> None:
    slots = draft.get("slots") or {}
    missing = draft.get("missing") or list(_REQUIRED_ACTIVITY_SLOTS)

    lines = ["boleh, aku catatin~"]
    if slots.get("title"):
        lines.append(f"\n📌 *{_md(slots['title'])}*")

    question = SLOT_QUESTIONS.get(missing[0], missing[0])
    lines.append(f"\n{question}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    await _remember(update.effective_user.id, update.message.text or "", "\n".join(lines))


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
            update.effective_user.id, intent
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

    await query.edit_message_text(
        f"✅ *{_md(updated['title'])}* selesai ya. mantap! 🎉",
        parse_mode="Markdown",
    )
    await _remember(user_id, "[tombol tandai selesai]", f"{updated['title']} selesai")


async def _apply_confirmed_action(user_id: int, intent: Intent):
    """Run the staged action. Returns (message, keyboard_or_None)."""
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

        title = str(slots.get("title"))
        remind = int(slots.get("remind_mins", 30) or 30)
        activity_id = await add_activity(
            user_id=user_id,
            title=title,
            activity_dt=when,
            description=str(slots.get("description") or ""),
            remind_mins=remind,
        )

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ tandai selesai", callback_data=f"done:{activity_id}"),
                InlineKeyboardButton("🗑️ hapus", callback_data=f"del_activity:{activity_id}"),
            ]
        ])
        return (
            f"✅ oke, udah aku simpan!\n\n"
            f"📌 *{_md(title)}*\n"
            f"🕒 {format_activity_datetime(when)} WIB\n"
            f"⏰ aku ingatkan {remind} menit sebelumnya 🔔",
            keyboard,
        )

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
