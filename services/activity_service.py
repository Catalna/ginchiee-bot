"""
services/activity_service.py
CRUD for user activities stored in Postgres.

Replaces the old Google Calendar integration. All datetimes are stored as
TIMESTAMPTZ and resolved against each user's timezone, so "today" and "in 30
minutes" are correct for users outside Asia/Jakarta (WIB/WITA/WIT).
"""

import logging
from datetime import datetime, timedelta
from typing import Optional, Sequence, Union

import pytz

from config.settings import APP_TIMEZONE
from database.connection import get_db
from services.user_service import get_user

logger = logging.getLogger(__name__)

_DEFAULT_TZ = APP_TIMEZONE


# ── Timezone helpers ───────────────────────────────────────────────────────────

def _parse_datetime(value) -> Optional[datetime]:
    """Best-effort parse of an ISO-ish datetime, returning a naive datetime."""
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:19], fmt)
        except ValueError:
            continue
    return None


def _user_timezone_or_default(tz_name: Optional[str]) -> str:
    """Return a usable timezone name."""
    return tz_name if tz_name else _DEFAULT_TZ


async def _tz_for(user_id: int) -> str:
    user = await get_user(user_id)
    tz = (user or {}).get("timezone")
    return tz if tz else _DEFAULT_TZ


def _to_aware(value: Union[str, datetime], tz_name: str) -> datetime:
    """Normalise a value to a timezone-aware datetime in tz_name."""
    dt = _parse_datetime(value)
    if dt is None:
        raise ValueError(f"Invalid datetime value: {value!r}")
    if dt.tzinfo is not None:
        return dt.astimezone(pytz.utc)
    tz = pytz.timezone(tz_name)
    return tz.localize(dt)


#: A reminder lead time is clamped to a sane window. Without this, a bogus
#: remind_mins (e.g. a parse error producing a huge number) shifts the reminder
#: date so far back that a far-future activity becomes "due" immediately.
MIN_REMIND_MINS = 0
MAX_REMIND_MINS = 7 * 24 * 60  # one week


def _clamp_remind_mins(value) -> int:
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        return 30
    return max(MIN_REMIND_MINS, min(MAX_REMIND_MINS, minutes))


# ── CRUD ──────────────────────────────────────────────────────────────────────

async def add_activity(
    user_id: int,
    title: str,
    activity_dt: Union[str, datetime],
    description: str = "",
    remind_mins: int = 30,
) -> int:
    """Insert an activity and return its id.

    `activity_dt` may be an ISO string ("2026-09-30T10:00:00") or a datetime.
    A naive value is interpreted in the user's timezone.
    """
    tz_name = await _tz_for(user_id)
    when = _to_aware(activity_dt, tz_name)

    db = await get_db()
    activity_id = await db.execute(
        """
        INSERT INTO user_activities (user_id, title, description, activity_dt, remind_mins)
        VALUES ($1, $2, $3, $4, $5)
        RETURNING id
        """,
        (user_id, title, description, when, _clamp_remind_mins(remind_mins)),
    ).fetchval()

    logger.info("Activity %s created for user %s at %s", activity_id, user_id, when.isoformat())
    return activity_id


async def get_activity_by_id(activity_id: int, user_id: int) -> Optional[dict]:
    """Fetch a single activity, scoped to the owning user."""
    db = await get_db()
    row = await db.execute(
        "SELECT * FROM user_activities WHERE id = $1 AND user_id = $2",
        (activity_id, user_id),
    ).fetchone()
    return dict(row) if row else None


async def get_activity_by_title(activity_id: int, user_id: int) -> Optional[dict]:
    return await get_activity_by_id(activity_id, user_id)


async def get_upcoming_activities(user_id: int, days: int = 7) -> list:
    """Activities from now until `days` days ahead. Timezone-correct."""
    db = await get_db()
    rows = await db.execute(
        """
        SELECT * FROM user_activities
        WHERE user_id = $1
          AND activity_dt >= now()
          AND activity_dt <= now() + make_interval(days => $2)
        ORDER BY activity_dt ASC
        """,
        (user_id, int(days)),
    ).fetchall()
    return [dict(r) for r in rows]


async def get_today_activities(user_id: int) -> list:
    """Activities whose date falls on 'today' in the user's own timezone."""
    tz_name = await _tz_for(user_id)
    tz = pytz.timezone(tz_name)
    now = datetime.now(tz)
    day_start = tz.localize(now.replace(hour=0, minute=0, second=0, microsecond=0))
    day_end = tz.localize(now.replace(hour=23, minute=59, second=59, microsecond=999999))

    db = await get_db()
    rows = await db.execute(
        """
        SELECT * FROM user_activities
        WHERE user_id = $1
          AND activity_dt >= $2
          AND activity_dt <= $3
        ORDER BY activity_dt ASC
        """,
        (user_id, day_start, day_end),
    ).fetchall()
    return [dict(r) for r in rows]


async def get_all_user_activities(user_id: int, limit: int = 20) -> list:
    """Upcoming activities for a user (used for delete/lists)."""
    db = await get_db()
    rows = await db.execute(
        """
        SELECT * FROM user_activities
        WHERE user_id = $1 AND activity_dt >= now()
        ORDER BY activity_dt ASC
        LIMIT $2
        """,
        (user_id, int(limit)),
    ).fetchall()
    return [dict(r) for r in rows]


async def update_activity(
    activity_id: int,
    user_id: int,
    title: Optional[str] = None,
    activity_dt: Optional[Union[str, datetime]] = None,
    description: Optional[str] = None,
    remind_mins: Optional[int] = None,
) -> Optional[dict]:
    """Update an activity. Returns the updated row, or None if not found."""
    existing = await get_activity_by_id(activity_id, user_id)
    if not existing:
        return None

    new_title = title if title is not None else existing["title"]
    new_desc = description if description is not None else (existing["description"] or "")
    new_remind = _clamp_remind_mins(remind_mins if remind_mins is not None else existing["remind_mins"])

    when = existing["activity_dt"]
    if activity_dt is not None:
        tz_name = await _tz_for(user_id)
        when = _to_aware(activity_dt, tz_name)

    db = await get_db()
    row = await db.execute(
        """
        UPDATE user_activities
        SET title = $1, description = $2, activity_dt = $3, remind_mins = $4
        WHERE id = $5 AND user_id = $6
        RETURNING *
        """,
        (new_title, new_desc, when, new_remind, activity_id, user_id),
    ).fetchone()

    return dict(row) if row else None


async def delete_activity(activity_id: int, user_id: int) -> bool:
    """Delete an activity. Returns True if a row was removed."""
    db = await get_db()
    deleted = await db.execute(
        "DELETE FROM user_activities WHERE id = $1 AND user_id = $2 RETURNING id",
        (activity_id, user_id),
    ).fetchval()
    return deleted is not None


async def delete_activity_by_title(activity_id: int, user_id: int) -> bool:
    return await delete_activity(activity_id, user_id)


async def mark_activity_done(activity_id: int, user_id: int) -> Optional[dict]:
    """Flag an activity as completed. Returns the updated row."""
    db = await get_db()
    row = await db.execute(
        """
        UPDATE user_activities
        SET done = TRUE, done_at = now(), reminded = TRUE
        WHERE id = $1 AND user_id = $2
        RETURNING *
        """,
        (activity_id, user_id),
    ).fetchone()
    return dict(row) if row else None


async def unmark_activity_done(activity_id: int, user_id: int) -> Optional[dict]:
    db = await get_db()
    row = await db.execute(
        """
        UPDATE user_activities
        SET done = FALSE, done_at = NULL
        WHERE id = $1 AND user_id = $2
        RETURNING *
        """,
        (activity_id, user_id),
    ).fetchone()
    return dict(row) if row else None


# ── Reminder Check ─────────────────────────────────────────────────────────────

#: Do not fire a reminder whose scheduled time is more than this far in the
#: past. Without it, a bogus remind_mins (or a bot that was down for days)
#: would resurrect an activity's reminder long after the moment has passed.
STALE_REMINDER_GRACE = timedelta(hours=24)


async def get_due_activity_reminders(now: Optional[datetime] = None) -> list:
    """Activities whose reminder window has arrived and that are not reminded yet.

    With TIMESTAMPTZ columns the arithmetic is done by Postgres, so this is
    correct for every user's timezone at once and there is no string-format
    juggling (the old SQLite code compared 'T'-separated strings against
    space-separated `datetime()` output, which silently misfired).
    """
    db = await get_db()
    rows = await db.execute(
        """
        SELECT a.*, u.name AS user_name, u.timezone
        FROM user_activities a
        JOIN users u ON a.user_id = u.user_id
        WHERE a.reminded = FALSE
          -- The reminder moment has arrived.
          AND (a.activity_dt - make_interval(mins => a.remind_mins)) <= now()
          -- But not so long ago that it is meaningless stale data.
          AND (a.activity_dt - make_interval(mins => a.remind_mins)) >= now() - make_interval(hours => 24)
          -- The activity itself is still in the future.
          AND a.activity_dt > now()
        ORDER BY a.activity_dt ASC
        """
    ).fetchall()
    return [dict(r) for r in rows]


async def mark_reminded(activity_id: int) -> None:
    db = await get_db()
    await db.execute(
        "UPDATE user_activities SET reminded = TRUE WHERE id = $1",
        (activity_id,),
    )


async def find_activity_by_reference(user_id: int, title: str, date_hint: Optional[str] = None) -> Optional[dict]:
    """Best-effort fuzzy lookup of an activity by title, for correction flows."""
    activities = await get_all_user_activities(user_id, limit=30)
    if not activities:
        return None

    needle = (title or "").strip().lower()

    # Exact title match first.
    for act in activities:
        if str(act.get("title", "")).strip().lower() == needle:
            return act

    # Substring match.
    for act in activities:
        act_title = str(act.get("title", "")).strip().lower()
        if needle and (needle in act_title or act_title in needle):
            return act

    # Token overlap fallback.
    needle_tokens = {t for t in needle.replace("-", " ").split() if len(t) > 2}
    if needle_tokens:
        best, best_score = None, 0
        for act in activities:
            tokens = {t for t in str(act.get("title", "")).lower().split() if len(t) > 2}
            score = len(needle_tokens & tokens)
            if score > best_score:
                best, best_score = act, score
        if best is not None:
            return best

    return None


# ── Formatting helper ──────────────────────────────────────────────────────────

INDONESIAN_DAYS = {
    "Monday": "Senin", "Tuesday": "Selasa", "Wednesday": "Rabu",
    "Thursday": "Kamis", "Friday": "Jumat", "Saturday": "Sabtu", "Sunday": "Minggu",
}
INDONESIAN_MONTHS = {
    1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "Mei", 6: "Jun",
    7: "Jul", 8: "Agu", 9: "Sep", 10: "Okt", 11: "Nov", 12: "Des",
}


def format_activity_datetime(activity_dt, user_id: Optional[int] = None) -> str:
    """Format an activity datetime for display, in the user's timezone."""
    dt = _parse_datetime(activity_dt)
    if dt is None:
        return str(activity_dt or "")
    if dt.tzinfo is not None:
        dt = dt.astimezone(pytz.timezone(_DEFAULT_TZ))
    day_name = INDONESIAN_DAYS.get(dt.strftime("%A"), dt.strftime("%A"))
    month_name = INDONESIAN_MONTHS.get(dt.month, dt.strftime("%b"))
    return f"{day_name}, {dt.day} {month_name} • {dt.strftime('%H:%M')}"


def slots_for_activity(needed: Sequence[str], current: dict) -> list[str]:
    """Return the list of missing slot names for an activity draft."""
    have = {
        "title": bool(current.get("title")),
        "date": bool(current.get("date")),
        "time": bool(current.get("time")),
    }
    return [slot for slot in needed if not have.get(slot, False)]


