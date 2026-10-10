"""
services/scheduler_service.py
APScheduler setup — checks due meal reminders and activity reminders every minute.
"""

import logging
from datetime import datetime

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from services.activity_service import get_due_activity_reminders, mark_reminded
from services.reminder_service import generate_reminder_message
from services.user_service import get_all_active_schedules

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None

#: In-memory guard so a meal reminder is sent at most once per user/meal/day,
#: even though the matching window spans several scheduler ticks.
_sent_today: set[tuple[int, str, str]] = set()


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler()
    return _scheduler


async def _check_reminders(bot, ai_service) -> None:
    """
    Runs every minute at :00. Sends meal reminders that are due.

    Matching used to require `current_hhmm == meal_time` exactly, which silently
    dropped a whole day's reminder whenever the event loop was blocked for more
    than 60s by a slow model call (max_instances=1). A 5-minute window with a
    `last_sent` guard keeps it at-most-once per day while tolerating a slow tick.
    """
    schedules = await get_all_active_schedules()
    now_utc = datetime.now(pytz.utc)

    for sched in schedules:
        user_id = sched["user_id"]
        meal_type = sched["meal_type"]
        meal_time = sched["time"]          # HH:MM
        user_tz_str = sched.get("timezone") or "Asia/Jakarta"

        try:
            user_tz = pytz.timezone(user_tz_str)
            now_local = now_utc.astimezone(user_tz)
            current_hhmm = now_local.strftime("%H:%M")

            if not _within_window(current_hhmm, meal_time):
                continue

            # At-most-once per user/meal/day even if the window overlaps ticks.
            sent_key = (user_id, meal_type, now_local.strftime("%Y-%m-%d"))
            if sent_key in _sent_today:
                continue

            logger.info("Sending %s reminder to user %s at %s", meal_type, user_id, meal_time)
            message = await generate_reminder_message(
                user_id=user_id,
                meal_type=meal_type,
                ai_service=ai_service,
            )
            await bot.send_message(chat_id=user_id, text=message)

            # Only mark as sent after a successful delivery, so a failed send is
            # retried on the next tick inside the same window.
            _sent_today.add(sent_key)
            _prune_sent_cache(now_local)

        except Exception as e:
            logger.error(f"Error processing meal reminder for user {user_id}: {e}")


def _within_window(current_hhmm: str, target_hhmm: str, grace_minutes: int = 5) -> bool:
    """True when `current_hhmm` is at or after `target_hhmm` within grace."""
    def to_minutes(value: str) -> int | None:
        try:
            hours, minutes = value.split(":")
            total = int(hours) * 60 + int(minutes)
            return total if 0 <= total < 24 * 60 else None
        except (ValueError, AttributeError):
            return None

    now = to_minutes(current_hhmm)
    target = to_minutes(target_hhmm)
    if now is None or target is None:
        return False

    # Same-minute window, extended by the grace period so a slow tick still fires.
    return target <= now <= target + grace_minutes


def _prune_sent_cache(now_local: datetime) -> None:
    """Keep the in-memory dedupe set small and relevant."""
    if len(_sent_today) < 500:
        return
    today = now_local.strftime("%Y-%m-%d")
    for key in [k for k in _sent_today if k[2] != today]:
        _sent_today.discard(key)


async def _check_activity_reminders(bot, ai_service) -> None:
    """
    Runs every minute. Finds all due activity reminders and sends them.
    """
    try:
        due = await get_due_activity_reminders()
    except Exception as e:
        logger.error(f"Error fetching due activity reminders: {e}")
        return

    for activity in due:
        user_id = activity["user_id"]
        activity_id = activity["id"]
        try:
            context = {
                "title": activity["title"],
                "description": activity.get("description", ""),
                "activity_dt": activity["activity_dt"],
                "remind_mins": activity["remind_mins"],
                "user_name": activity.get("user_name", "kamu"),
            }
            message = await ai_service.generate_activity_reminder(context)
            await bot.send_message(
                chat_id=user_id,
                text=message,
                parse_mode="Markdown",
            )
            await mark_reminded(activity_id)
            logger.info(
                f"Sent activity reminder for '{activity['title']}' to user {user_id}"
            )
        except Exception as e:
            logger.error(
                f"Error sending activity reminder id={activity_id} to user {user_id}: {e}"
            )


def start_scheduler(bot, ai_service) -> AsyncIOScheduler:
    """Initialize and start the APScheduler."""
    scheduler = get_scheduler()

    # Meal reminder check — every minute at :00 seconds
    scheduler.add_job(
        _check_reminders,
        trigger=CronTrigger(second=0),
        args=[bot, ai_service],
        id="reminder_check",
        replace_existing=True,
        max_instances=1,
    )

    # Activity reminder check — every minute at :30 seconds (offset to avoid overlap)
    scheduler.add_job(
        _check_activity_reminders,
        trigger=CronTrigger(second=30),
        args=[bot, ai_service],
        id="activity_reminder_check",
        replace_existing=True,
        max_instances=1,
    )

    scheduler.start()
    logger.info("Scheduler started — checking meal & activity reminders every minute.")
    return scheduler


def stop_scheduler() -> None:
    scheduler = get_scheduler()
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped.")

