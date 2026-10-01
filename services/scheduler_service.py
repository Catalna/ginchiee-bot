"""
services/scheduler_service.py
APScheduler setup — checks due meal reminders and activity reminders every minute.
"""

import logging
from datetime import datetime

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from services.user_service import get_all_active_schedules
from services.reminder_service import generate_reminder_message
from services.activity_service import get_due_activity_reminders, mark_reminded

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler()
    return _scheduler


async def _check_reminders(bot, ai_service) -> None:
    """
    Runs every minute. Finds all due meal reminders and sends them.
    """
    schedules = await get_all_active_schedules()
    now_utc = datetime.now(pytz.utc)

    for sched in schedules:
        user_id = sched["user_id"]
        meal_type = sched["meal_type"]
        meal_time = sched["time"]          # HH:MM
        user_tz_str = sched.get("timezone", "Asia/Jakarta")

        try:
            user_tz = pytz.timezone(user_tz_str)
            now_local = now_utc.astimezone(user_tz)
            current_hhmm = now_local.strftime("%H:%M")

            if current_hhmm == meal_time:
                logger.info(
                    f"Sending {meal_type} reminder to user {user_id} at {meal_time}"
                )
                message = await generate_reminder_message(
                    user_id=user_id,
                    meal_type=meal_type,
                    ai_service=ai_service,
                )
                await bot.send_message(chat_id=user_id, text=message)

        except Exception as e:
            logger.error(f"Error processing meal reminder for user {user_id}: {e}")


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

