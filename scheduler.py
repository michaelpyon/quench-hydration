from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler

from calculator import calculate_daily_oz, generate_schedule, get_timezone
from models import DailyLog, Reminder, User, calculate_streak, db, get_or_create_daily_log
from sms import build_reminder_message, build_weekly_summary, send_sms_message


logger = logging.getLogger(__name__)
scheduler: BackgroundScheduler | None = None


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def local_date_to_utc_window(user: User, target_date: date) -> tuple[datetime, datetime]:
    tz = get_timezone(user)
    start_local = datetime.combine(target_date, time.min, tzinfo=tz)
    end_local = datetime.combine(target_date + timedelta(days=1), time.min, tzinfo=tz)
    return (
        start_local.astimezone(timezone.utc).replace(tzinfo=None),
        end_local.astimezone(timezone.utc).replace(tzinfo=None),
    )


def get_user_local_date(user: User, base_utc: datetime | None = None) -> date:
    base = (base_utc or utcnow()).replace(tzinfo=timezone.utc)
    return base.astimezone(get_timezone(user)).date()


def get_user_local_now(user: User, base_utc: datetime | None = None) -> datetime:
    base = (base_utc or utcnow()).replace(tzinfo=timezone.utc)
    return base.astimezone(get_timezone(user))


def ensure_schedule_for_user_date(user: User, target_date: date, force: bool = False) -> list[Reminder]:
    start_utc, end_utc = local_date_to_utc_window(user, target_date)
    existing = (
        Reminder.query.filter(
            Reminder.user_id == user.id,
            Reminder.scheduled_time >= start_utc,
            Reminder.scheduled_time < end_utc,
        )
        .order_by(Reminder.scheduled_time.asc())
        .all()
    )

    day_goal = calculate_daily_oz(user, target_date)

    if existing and not force:
        get_or_create_daily_log(user.id, target_date, day_goal)
        if target_date == get_user_local_date(user):
            user.daily_oz_goal = day_goal
        db.session.flush()
        return existing

    if force and existing:
        for reminder in existing:
            if reminder.completed:
                continue
            db.session.delete(reminder)
        db.session.flush()

    if target_date == get_user_local_date(user):
        user.daily_oz_goal = day_goal
    schedule = generate_schedule(user, day_goal, target_date)
    reminders: list[Reminder] = []
    for item in schedule:
        reminder = Reminder(
            user_id=user.id,
            scheduled_time=item["scheduled_time_utc"],
            amount_oz=item["amount_oz"],
        )
        db.session.add(reminder)
        reminders.append(reminder)

    get_or_create_daily_log(user.id, target_date, day_goal)
    db.session.flush()
    return reminders


def generate_daily_schedules(app) -> None:
    with app.app_context():
        users = User.query.filter_by(verified=True, active=True).all()
        created = 0
        for user in users:
            today = get_user_local_date(user)
            tomorrow = today + timedelta(days=1)
            before_today = Reminder.query.filter_by(user_id=user.id).count()
            ensure_schedule_for_user_date(user, today)
            ensure_schedule_for_user_date(user, tomorrow)
            after_today = Reminder.query.filter_by(user_id=user.id).count()
            created += max(0, after_today - before_today)
        db.session.commit()
        if created:
            logger.info("Generated %s reminders across active users.", created)


def send_due_reminders(app) -> None:
    with app.app_context():
        now = utcnow()
        due_reminders = (
            Reminder.query.join(User)
            .filter(
                Reminder.sent.is_(False),
                Reminder.scheduled_time <= now,
                User.verified.is_(True),
                User.active.is_(True),
            )
            .order_by(Reminder.scheduled_time.asc())
            .all()
        )

        for reminder in due_reminders:
            user = reminder.user
            local_date = reminder.scheduled_time.replace(tzinfo=timezone.utc).astimezone(
                get_timezone(user)
            ).date()
            daily_log = get_or_create_daily_log(user.id, local_date, user.daily_oz_goal or 0)
            recent_cutoff = now - timedelta(days=7)
            recent_keys = [
                row.message_key
                for row in Reminder.query.filter(
                    Reminder.user_id == user.id,
                    Reminder.sent_at >= recent_cutoff,
                    Reminder.message_key.isnot(None),
                ).all()
            ]
            projected_total = min(daily_log.target_oz, daily_log.oz_completed + reminder.amount_oz)
            body, message_key = build_reminder_message(
                first_name=user.first_name,
                oz=reminder.amount_oz,
                running_total=projected_total,
                daily_goal=daily_log.target_oz,
                recent_keys=recent_keys,
                seed=reminder.id or int(reminder.scheduled_time.timestamp()),
            )
            reminder.message_sid = send_sms_message(user.phone, body)
            reminder.message_key = message_key
            reminder.sent = True
            reminder.sent_at = now
            daily_log.reminders_sent += 1

        db.session.commit()


def send_weekly_summary(app) -> None:
    with app.app_context():
        now = utcnow()
        users = User.query.filter_by(verified=True, active=True).all()
        for user in users:
            local_now = get_user_local_now(user, now)
            if local_now.weekday() != 6:
                continue
            if local_now.hour != 20 or local_now.minute >= 15:
                continue
            if user.last_weekly_summary_for == local_now.date():
                continue

            window_start = local_now.date() - timedelta(days=6)
            logs = DailyLog.query.filter(
                DailyLog.user_id == user.id,
                DailyLog.log_date >= window_start,
                DailyLog.log_date <= local_now.date(),
            ).all()
            hit_days = sum(1 for log in logs if log.oz_completed >= log.target_oz)
            streak = calculate_streak(user.id, local_now.date())
            body = build_weekly_summary(user.first_name or "Hydration check", hit_days, streak)
            send_sms_message(user.phone, body)
            user.last_weekly_summary_for = local_now.date()
        db.session.commit()


def init_scheduler(app) -> BackgroundScheduler:
    global scheduler
    if scheduler and scheduler.running:
        return scheduler

    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(send_due_reminders, "interval", minutes=1, id="send_due_reminders", args=[app], replace_existing=True)
    scheduler.add_job(
        generate_daily_schedules,
        "interval",
        minutes=15,
        id="generate_daily_schedules",
        args=[app],
        replace_existing=True,
    )
    scheduler.add_job(
        send_weekly_summary,
        "interval",
        minutes=15,
        id="send_weekly_summary",
        args=[app],
        replace_existing=True,
    )
    scheduler.start()
    logger.info("Scheduler started.")
    return scheduler
