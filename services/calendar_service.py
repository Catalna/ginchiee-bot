"""
services/calendar_service.py
Google Calendar API integration via OAuth2 per-user.
Each Telegram user can connect their own Google Calendar by authorizing via /connectcalendar.
"""

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timedelta
from typing import Optional

import pytz
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

from config.settings import (
    APP_TIMEZONE,
    GOOGLE_CALENDAR_ID,
    GOOGLE_OAUTH_CLIENT_SECRETS_FILE,
)
from database.connection import get_db

logger = logging.getLogger(__name__)

CALENDAR_SCOPES = ["https://www.googleapis.com/auth/calendar"]


# ── OAuth2 Flow Helpers ────────────────────────────────────────────────────────


def get_client_secrets_file() -> str:
    """Return configured OAuth secrets file, or fallback to auto-detecting client_secret_*.json in config/."""
    if os.path.isfile(GOOGLE_OAUTH_CLIENT_SECRETS_FILE):
        return GOOGLE_OAUTH_CLIENT_SECRETS_FILE
    
    config_dir = os.path.dirname(GOOGLE_OAUTH_CLIENT_SECRETS_FILE) or "config"
    if os.path.exists(config_dir):
        for fname in os.listdir(config_dir):
            if fname.startswith("client_secret_") and fname.endswith(".json"):
                return os.path.join(config_dir, fname)
    return GOOGLE_OAUTH_CLIENT_SECRETS_FILE


def is_oauth_configured() -> bool:
    """Check if the OAuth2 client secrets file exists."""
    return os.path.isfile(get_client_secrets_file())


def get_oauth_flow() -> Flow:
    """
    Build an OAuth2 Flow using the client secrets file.
    Uses OOB (out-of-band) redirect for CLI/bot environments.
    """
    secrets_file = get_client_secrets_file()
    if not is_oauth_configured():
        raise FileNotFoundError(
            f"OAuth2 client secrets file not found: {secrets_file}\n"
            "Download OAuth2 credentials (Desktop app) from Google Cloud Console."
        )

    flow = Flow.from_client_secrets_file(
        secrets_file,
        scopes=CALENDAR_SCOPES,
        redirect_uri="urn:ietf:wg:oauth:2.0:oob",  # OOB / copy-paste flow
    )
    return flow


def get_auth_url() -> str:
    """Generate the Google OAuth2 authorization URL for the user to visit."""
    flow = get_oauth_flow()
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent",  # Always show consent to get refresh_token
        include_granted_scopes="true",
    )
    return auth_url


def exchange_code_for_tokens(auth_code: str) -> dict:
    """
    Exchange the authorization code (from user) for access + refresh tokens.
    Returns a dict ready to be JSON-serialized and stored in DB.
    """
    flow = get_oauth_flow()
    flow.fetch_token(code=auth_code.strip())
    creds = flow.credentials

    token_data = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes) if creds.scopes else CALENDAR_SCOPES,
        "expiry": creds.expiry.isoformat() if creds.expiry else None,
    }
    return token_data


# ── Token Storage (Database) ───────────────────────────────────────────────────


async def save_user_token(user_id: int, token_data: dict, calendar_id: str = "primary") -> None:
    """Persist the user's OAuth2 token data to the database."""
    db = await get_db()
    await db.execute(
        """
        INSERT INTO google_calendar_tokens (user_id, token, calendar_id, connected_at)
        VALUES (?, ?, ?, datetime('now'))
        ON CONFLICT(user_id) DO UPDATE SET
            token = excluded.token,
            calendar_id = excluded.calendar_id,
            connected_at = excluded.connected_at
        """,
        (user_id, json.dumps(token_data), calendar_id),
    )
    await db.commit()
    logger.info(f"Saved Google Calendar token for user {user_id}")


async def get_user_token_data(user_id: int) -> Optional[dict]:
    """Retrieve stored token data for a user, or None if not connected."""
    db = await get_db()
    async with db.execute(
        "SELECT token, calendar_id FROM google_calendar_tokens WHERE user_id = ?",
        (user_id,),
    ) as cursor:
        row = await cursor.fetchone()

    if row is None:
        return None
    return {"token_data": json.loads(row[0]), "calendar_id": row[1]}


async def delete_user_token(user_id: int) -> None:
    """Remove the user's calendar connection from the database."""
    db = await get_db()
    await db.execute(
        "DELETE FROM google_calendar_tokens WHERE user_id = ?", (user_id,)
    )
    await db.commit()
    logger.info(f"Deleted Google Calendar token for user {user_id}")


async def is_user_calendar_connected(user_id: int) -> bool:
    """Return True if the user has connected their Google Calendar."""
    return (await get_user_token_data(user_id)) is not None


# ── Build Authorized Calendar Client ──────────────────────────────────────────


def _build_credentials_from_token_data(token_data: dict) -> Credentials:
    """Reconstruct a google.oauth2.credentials.Credentials from stored dict."""
    expiry = None
    if token_data.get("expiry"):
        try:
            expiry = datetime.fromisoformat(token_data["expiry"])
        except Exception:
            pass

    creds = Credentials(
        token=token_data.get("token"),
        refresh_token=token_data.get("refresh_token"),
        token_uri=token_data.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=token_data.get("client_id"),
        client_secret=token_data.get("client_secret"),
        scopes=token_data.get("scopes", CALENDAR_SCOPES),
        expiry=expiry,
    )
    return creds


async def _get_user_calendar_client(user_id: int):
    """
    Build an authorized Google Calendar service client for a specific user.
    Auto-refreshes the token if expired and saves the new token to DB.
    """
    stored = await get_user_token_data(user_id)
    if stored is None:
        raise PermissionError(
            f"User {user_id} has not connected their Google Calendar. "
            "Ask them to use /connectcalendar first."
        )

    token_data = stored["token_data"]
    calendar_id = stored["calendar_id"]
    creds = _build_credentials_from_token_data(token_data)

    # Refresh token if expired
    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
        # Save the refreshed token back to DB
        refreshed_data = {
            **token_data,
            "token": creds.token,
            "expiry": creds.expiry.isoformat() if creds.expiry else None,
        }
        await save_user_token(user_id, refreshed_data, calendar_id)

    service = build("calendar", "v3", credentials=creds, cache_discovery=False)
    return service, calendar_id


# ── Google Calendar Operations ────────────────────────────────────────────────


async def add_calendar_event(
    user_id: int,
    summary: str,
    start_time_iso: str,
    end_time_iso: Optional[str] = None,
    description: Optional[str] = None,
    location: Optional[str] = None,
    reminder_minutes: int = 30,
) -> dict:
    """
    Create a new event in the user's Google Calendar.
    Times must be ISO format with timezone (e.g. 2026-09-07T14:00:00+07:00).
    """
    def _sync_create(service, calendar_id):
        tz = APP_TIMEZONE
        nonlocal end_time_iso

        if not end_time_iso:
            try:
                dt_start = datetime.fromisoformat(start_time_iso)
                dt_end = dt_start + timedelta(hours=1)
                end_time_iso = dt_end.isoformat()
            except Exception:
                end_time_iso = start_time_iso

        event_body = {
            "summary": summary,
            "description": description or "Dibuat otomatis oleh Ginchiee Bot 🌸",
            "start": {
                "dateTime": start_time_iso,
                "timeZone": tz,
            },
            "end": {
                "dateTime": end_time_iso,
                "timeZone": tz,
            },
            "reminders": {
                "useDefault": False,
                "overrides": [
                    {"method": "popup", "minutes": reminder_minutes},
                    {"method": "popup", "minutes": 10},
                ],
            },
        }

        if location:
            event_body["location"] = location

        created = service.events().insert(
            calendarId=calendar_id,
            body=event_body,
        ).execute()
        return created

    service, calendar_id = await _get_user_calendar_client(user_id)
    return await asyncio.to_thread(_sync_create, service, calendar_id)


async def get_upcoming_events(user_id: int, days: int = 7, max_results: int = 10) -> list[dict]:
    """Retrieve upcoming events for the next N days from the user's calendar."""
    def _sync_get(service, calendar_id):
        tz = pytz.timezone(APP_TIMEZONE)
        now = datetime.now(tz)
        time_min = now.isoformat()
        time_max = (now + timedelta(days=days)).isoformat()

        events_result = service.events().list(
            calendarId=calendar_id,
            timeMin=time_min,
            timeMax=time_max,
            maxResults=max_results,
            singleEvents=True,
            orderBy="startTime",
        ).execute()

        return events_result.get("items", [])

    service, calendar_id = await _get_user_calendar_client(user_id)
    return await asyncio.to_thread(_sync_get, service, calendar_id)


async def get_today_events(user_id: int) -> list[dict]:
    """Retrieve all events for today (00:00 to 23:59 Jakarta time) from the user's calendar."""
    def _sync_get_today(service, calendar_id):
        tz = pytz.timezone(APP_TIMEZONE)
        now = datetime.now(tz)
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        end_of_day = now.replace(hour=23, minute=59, second=59, microsecond=999999).isoformat()

        events_result = service.events().list(
            calendarId=calendar_id,
            timeMin=start_of_day,
            timeMax=end_of_day,
            singleEvents=True,
            orderBy="startTime",
        ).execute()

        return events_result.get("items", [])

    service, calendar_id = await _get_user_calendar_client(user_id)
    return await asyncio.to_thread(_sync_get_today, service, calendar_id)


# ── AI Natural Language Schedule Parser ──────────────────────────────────────


async def parse_calendar_intent_from_llm(text: str, ai_service) -> Optional[dict]:
    """
    Use Gemini LLM to detect scheduling intent (create_event vs view_agenda).
    Returns a dict with parsed event details, or None if not calendar related.
    """
    tz = pytz.timezone(APP_TIMEZONE)
    now_str = datetime.now(tz).strftime("%Y-%m-%dT%H:%M:%S+07:00")
    day_name = datetime.now(tz).strftime("%A, %d %B %Y %H:%M")

    prompt = f"""Kamu adalah asisten pengatur jadwal & kalender pribadi.
Waktu lokal saat ini: {day_name} (ISO: {now_str}, Timezone: GMT+7 / Asia/Jakarta).

Teks Pengguna: "{text}"

Tugas:
Analisa apakah pengguna ingin:
A. Membuat / menambahkan jadwal baru ke Google Calendar ("action": "create_event")
B. Menanyakan / melihat agenda / jadwal ("action": "view_agenda")
C. Bukan urusan kalender / jadwal ("action": "none")

Jika "create_event":
- summary: Judul acara yang jelas dan rapi (misal: "Kontrol Dokter Gigi", "Dinner bareng Ayang", "Meeting Proyek")
- start_iso: Waktu mulai dalam format ISO 8601 lengkap dengan offset timezone +07:00 (misal: "2026-09-07T14:00:00+07:00")
- end_iso: Waktu selesai dalam ISO 8601 (perkirakan 1 jam dari mulai jika tidak disebut)
- description: Catatan tambahan jika ada
- location: Lokasi jika disebutkan

Jika "view_agenda":
- date_target: "today", "tomorrow", atau "week"

Kembalikan HANYA JSON murni (tanpa markdown codeblock, tanpa teks tambahan):
Contoh create:
{{
  "action": "create_event",
  "summary": "Makan Malam bareng Pacar",
  "start_iso": "2026-09-07T19:00:00+07:00",
  "end_iso": "2026-09-07T21:00:00+07:00",
  "description": "",
  "location": ""
}}

Contoh view:
{{
  "action": "view_agenda",
  "date_target": "today"
}}
"""

    try:
        raw = await ai_service.generate_raw(prompt)
        clean_raw = re.sub(r"^```[a-zA-Z]*\n", "", raw.strip(), flags=re.MULTILINE)
        clean_raw = re.sub(r"```$", "", clean_raw.strip(), flags=re.MULTILINE)

        json_match = re.search(r'\{.*\}', clean_raw, re.DOTALL)
        if not json_match:
            return None

        data = json.loads(json_match.group())
        action = data.get("action")
        if action in ("create_event", "view_agenda"):
            return data
        return None

    except Exception as e:
        logger.error(f"Error parsing calendar intent from LLM: {e}")
        return None
