'''
services/activity_service.py
CRUD for user activities stored in local SQLite DB.
Replaces Google Calendar integration.
'''

import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Optional, List

import pytz

from config.settings import APP_TIMEZONE
from database.connection import get_db

logger = logging.getLogger(__name__)

_TZ = pytz.timezone(APP_TIMEZONE)


def _now_local() -> datetime:
    '''Return current datetime in app timezone (naive).'''
    return datetime.now(_TZ).replace(tzinfo=None)


# ── CRUD ──────────────────────────────────────────────────────────────────────

async def add_activity(
    user_id: int,
    title: str,
    activity_dt: str,
    description: str = '',
    remind_mins: int = 30,
) -> int:
    '''Insert a new activity and return its id.'''
    db = await get_db()
    cursor = await db.execute(
        '''
        INSERT INTO user_activities (user_id, title, description, activity_dt, remind_mins)
        VALUES (?, ?, ?, ?, ?)
        ''',
        (user_id, title, description, activity_dt, remind_mins),
    )
    await db.commit()
    return cursor.lastrowid


async def get_upcoming_activities(user_id: int, days: int = 7) -> list:
    '''Return upcoming activities for user within days days from now.'''
    now = _now_local()
    until = now + timedelta(days=days)
    db = await get_db()
    async with db.execute(
        '''
        SELECT * FROM user_activities
        WHERE user_id = ?
          AND activity_dt >= ?
          AND activity_dt <= ?
        ORDER BY activity_dt ASC
        ''',
        (user_id, now.isoformat(timespec='seconds'), until.isoformat(timespec='seconds')),
    ) as cursor:
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def get_today_activities(user_id: int) -> list:
    '''Return activities for today.'''
    now = _now_local()
    today_start = now.replace(hour=0, minute=0, second=0).isoformat(timespec='seconds')
    today_end = now.replace(hour=23, minute=59, second=59).isoformat(timespec='seconds')
    db = await get_db()
    async with db.execute(
        '''
        SELECT * FROM user_activities
        WHERE user_id = ?
          AND activity_dt >= ?
          AND activity_dt <= ?
        ORDER BY activity_dt ASC
        ''',
        (user_id, today_start, today_end),
    ) as cursor:
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def delete_activity(activity_id: int, user_id: int) -> bool:
    '''Delete an activity. Returns True if deleted, False if not found.'''
    db = await get_db()
    cursor = await db.execute(
        'DELETE FROM user_activities WHERE id = ? AND user_id = ?',
        (activity_id, user_id),
    )
    await db.commit()
    return cursor.rowcount > 0


async def get_all_user_activities(user_id: int, limit: int = 20) -> list:
    '''Return all future activities for a user (for deletion listing).'''
    now = _now_local()
    db = await get_db()
    async with db.execute(
        '''
        SELECT * FROM user_activities
        WHERE user_id = ? AND activity_dt >= ?
        ORDER BY activity_dt ASC
        LIMIT ?
        ''',
        (user_id, now.isoformat(timespec='seconds'), limit),
    ) as cursor:
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


# ── Reminder Check ─────────────────────────────────────────────────────────────

async def get_due_activity_reminders() -> list:
    '''
    Return activities that need to be reminded right now.
    Due when: activity_dt - remind_mins <= now < activity_dt and reminded = 0.

    BUG FIX: SQLite datetime() function always outputs "YYYY-MM-DD HH:MM:SS" (space separator).
    If activity_dt is stored as "YYYY-MM-DDTHH:MM:SS" (T separator), the string comparison
    between a space-separator result and a T-separator stored value is incorrect because
    ASCII(' ') = 32 < ASCII('T') = 84, causing all events in the day to fire at midnight.
    Fix: normalise both sides to use the space separator via replace(activity_dt, 'T', ' ').
    '''
    now = _now_local()
    # Use space-separated ISO string so it matches SQLite's datetime() output format
    now_str = now.strftime('%Y-%m-%d %H:%M:%S')
    db = await get_db()
    async with db.execute(
        '''
        SELECT a.*, u.name AS user_name, u.timezone
        FROM user_activities a
        JOIN users u ON a.user_id = u.user_id
        WHERE a.reminded = 0
          AND datetime(replace(a.activity_dt, 'T', ' '), '-' || a.remind_mins || ' minutes') <= ?
          AND replace(a.activity_dt, 'T', ' ') > ?
        ''',
        (now_str, now_str),
    ) as cursor:
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def mark_reminded(activity_id: int) -> None:
    '''Mark activity as already reminded.'''
    db = await get_db()
    await db.execute(
        'UPDATE user_activities SET reminded = 1 WHERE id = ?',
        (activity_id,),
    )
    await db.commit()


# ── AI Parsing ────────────────────────────────────────────────────────────────

INDONESIAN_DAYS = {
    "Monday": "Senin",
    "Tuesday": "Selasa",
    "Wednesday": "Rabu",
    "Thursday": "Kamis",
    "Friday": "Jumat",
    "Saturday": "Sabtu",
    "Sunday": "Minggu",
}

async def parse_activity_from_text(text: str, ai_service) -> Optional[dict]:
    '''
    Use AI to parse a natural language message into an activity dict.
    Returns dict with keys: title, description, activity_dt, remind_mins
    Returns None if no activity-add intent detected.
    '''
    now = _now_local()
    now_str = now.strftime('%Y-%m-%d %H:%M')
    day_en = now.strftime('%A')
    day_id = INDONESIAN_DAYS.get(day_en, day_en)

    # Build list of upcoming days with their Indonesian names & dates
    upcoming_days_info = []
    for i in range(1, 8):
        f_dt = now + timedelta(days=i)
        f_day_id = INDONESIAN_DAYS.get(f_dt.strftime('%A'), f_dt.strftime('%A'))
        upcoming_days_info.append(
            f'- {f_day_id} terdekat (tgl {f_dt.strftime("%d %B %Y")}): {f_dt.strftime("%Y-%m-%d")}'
        )

    upcoming_str = '\n'.join(upcoming_days_info)
    today_str = now.strftime('%Y-%m-%d')
    tomorrow_str = (now + timedelta(days=1)).strftime('%Y-%m-%d')
    day_after_str = (now + timedelta(days=2)).strftime('%Y-%m-%d')

    prompt = f'''Waktu sekarang: {now_str} WIB (Hari {day_id}, {now.strftime("%d %B %Y")}).

Referensi tanggal terdekat:
- Hari ini ({day_id}): {today_str}
- Besok: {tomorrow_str}
- Lusa: {day_after_str}
{upcoming_str}

Tugas:
Analisa pesan user berikut. Tentukan apakah user bermaksud MENAMBAHKAN / MENCATAT / MENYIMPAN suatu kegiatan atau acara baru ke jadwal.

Pesan user: "{text}"

Aturan PENTING:
- Jawab {{"has_activity": true}} HANYA jika user jelas ingin MENYIMPAN/MENCATAT kegiatan baru.
  Contoh positif: "besok ada meeting jam 10", "ingetin aku sabtu mau ke dokter", "tambahin agenda Culfest tgl 28",
  "nanti jam 10 pagi aku ngedate", "aku hari ini ada agenda Binus Crypto Week", "tolong catat rapat besok jam 2".
- Jawab {{"has_activity": false}} jika user hanya BERTANYA tentang jadwal, obrolan biasa, atau minta LIHAT jadwal.
  Contoh negatif: "jadwal hari ini apa?", "ada acara apa?", "halo", "gimana kabar?".
- Kalimat seperti "aku hari ini ada X" atau "besok ada X" atau "nanti ada X" = user memberitahu kamu ada kegiatan → SIMPAN.

Jika has_activity true, balas HANYA JSON valid:
{{"has_activity": true, "title": "judul kegiatan singkat", "description": "deskripsi tambahan atau kosong", "activity_dt": "YYYY-MM-DDTHH:MM:SS", "remind_mins": 30}}

Aturan waktu:
- Format ISO wajib: YYYY-MM-DDTHH:MM:SS
- "jam 10 pagi" → 10:00:00, "jam 2 siang" → 14:00:00, "jam 3 sore" → 15:00:00
- Jika tidak ada jam spesifik, gunakan 09:00:00
- remind_mins default 30

Jika has_activity false, balas HANYA:
{{"has_activity": false}}'''

    raw = ''
    try:
        raw = await ai_service.generate_raw(prompt)
        raw = raw.strip()
        # Strip markdown code blocks if present
        if '```' in raw:
            parts = raw.split('```')
            raw = parts[1] if len(parts) > 1 else raw
            if raw.strip().startswith('json'):
                raw = raw.strip()[4:]
        raw = raw.strip()
        data = json.loads(raw)
        if data.get('has_activity') and data.get('title'):
            return {
                'title': data.get('title', 'Kegiatan'),
                'description': data.get('description', ''),
                'activity_dt': data.get('activity_dt', now.isoformat(timespec='seconds')),
                'remind_mins': int(data.get('remind_mins', 30)),
            }
    except Exception as e:
        logger.error(f'Failed to parse activity from text: {e} | raw: {raw}')

    return None


async def parse_cancel_from_text(text: str, ai_service, user_activities: list) -> Optional[dict]:
    '''
    Use AI to detect if user wants to CANCEL / DELETE a scheduled activity.
    user_activities: list of upcoming activity dicts (from get_upcoming_activities or get_all_user_activities).
    Returns dict: {"cancel": true, "activity_id": <int>, "title": <str>}
    Returns None if no cancel intent detected or no match found.
    '''
    if not user_activities:
        return None

    now = _now_local()
    now_str = now.strftime('%Y-%m-%d %H:%M')

    # Build activity list for the prompt
    activity_lines = []
    for act in user_activities:
        dt_str = act['activity_dt'][:16].replace('T', ' ')
        activity_lines.append(f'- ID {act["id"]}: "{act["title"]}" pada {dt_str}')
    activity_list_str = '\n'.join(activity_lines)

    prompt = f'''Waktu sekarang: {now_str} WIB.

Daftar kegiatan yang tersimpan milik user:
{activity_list_str}

Pesan user: "{text}"

Tugas:
Analisa apakah user bermaksud MEMBATALKAN / MENGHAPUS salah satu kegiatan di atas.

Contoh pesan pembatalan: "aku ga jadi ngedate", "cancel meeting besok", "hapus jadwal dokter",
"acara Culfest dicancel", "meeting jam 9 ga jadi", "ga jadi ke dokter", "batalin rapat",
"acaraku dicancel", "jadwal besok ga ada".

Jika user bermaksud membatalkan kegiatan:
- Cocokkan dengan kegiatan di daftar (cari yang paling relevan berdasarkan nama/judul/waktu).
- Balas HANYA JSON valid: {{"cancel": true, "activity_id": <id dari daftar>, "title": "judul kegiatan yang dicocokkan"}}

Jika pesan bukan pembatalan kegiatan:
- Balas HANYA: {{"cancel": false}}'''

    raw = ''
    try:
        raw = await ai_service.generate_raw(prompt)
        raw = raw.strip()
        if '```' in raw:
            parts = raw.split('```')
            raw = parts[1] if len(parts) > 1 else raw
            if raw.strip().startswith('json'):
                raw = raw.strip()[4:]
        raw = raw.strip()
        data = json.loads(raw)
        if data.get('cancel') and data.get('activity_id'):
            return {
                'activity_id': int(data['activity_id']),
                'title': data.get('title', 'Kegiatan'),
            }
    except Exception as e:
        logger.error(f'Failed to parse cancel intent from text: {e} | raw: {raw}')

    return None
