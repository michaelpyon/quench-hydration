from __future__ import annotations

import os
import random
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from urllib.parse import unquote

import phonenumbers
from phonenumbers.phonenumberutil import NumberParseException
from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_cors import CORS
from twilio.twiml.messaging_response import MessagingResponse

from calculator import calculate_daily_oz, format_time_label, generate_schedule, get_timezone
from models import Reminder, User, calculate_streak, db, get_or_create_daily_log
from scheduler import (
    ensure_schedule_for_user_date,
    get_user_local_date,
    init_scheduler,
    local_date_to_utc_window,
)
from sms import (
    build_completion_message,
    build_help_message,
    build_skip_message,
    build_unsubscribe_message,
    build_verification_message,
    send_sms_message,
)


load_dotenv()

ACTIVITY_LEVELS = {"sedentary", "light", "moderate", "heavy"}
CLIMATE_TYPES = {"humid", "dry", "temperate", "hot"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def normalize_database_url(url: str | None) -> str:
    if not url:
        return "sqlite:///quench.db"
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
    return url


def normalize_phone(raw_phone: str) -> str:
    try:
        parsed = phonenumbers.parse(raw_phone, "US")
    except NumberParseException as exc:
        raise ValueError("Please enter a valid US phone number.") from exc
    if not phonenumbers.is_valid_number(parsed):
        raise ValueError("Please enter a valid US phone number.")
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def coerce_int(value: Any, field_name: str, minimum: int = 0, maximum: int | None = None) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a number.") from exc
    if parsed < minimum:
        raise ValueError(f"{field_name} must be at least {minimum}.")
    if maximum is not None and parsed > maximum:
        raise ValueError(f"{field_name} must be at most {maximum}.")
    return parsed


def clean_name(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned[:40] if cleaned else None


def parse_clock(value: Any, field_name: str) -> time:
    try:
        parsed = datetime.strptime(str(value), "%H:%M").time()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be in HH:MM format.") from exc
    return parsed


def parse_exercise_days(value: Any) -> list[int]:
    if not value:
        return []
    if not isinstance(value, list):
        raise ValueError("Exercise days must be a list.")
    days = sorted({coerce_int(item, "Exercise day", 1, 7) for item in value})
    return list(days)


def utc_to_local(utc_dt: datetime, timezone_name: str) -> datetime:
    tz = get_timezone({"timezone": timezone_name})
    return utc_dt.replace(tzinfo=timezone.utc).astimezone(tz)


def serialize_schedule_item(reminder: Reminder, timezone_name: str) -> dict[str, Any]:
    local_dt = utc_to_local(reminder.scheduled_time, timezone_name)
    if reminder.completed:
        status = "done"
    elif reminder.sent:
        status = "active"
    else:
        status = "upcoming"
    return {
        "id": reminder.id,
        "time_label": format_time_label(local_dt),
        "scheduled_time": local_dt.isoformat(),
        "amount_oz": reminder.amount_oz,
        "sent": reminder.sent,
        "completed": reminder.completed,
        "status": status,
    }


def serialize_preview_schedule(schedule: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "time_label": item["time_label"],
            "scheduled_time": item["iso_local"],
            "amount_oz": item["amount_oz"],
        }
        for item in schedule
    ]


def serialize_streak_days(user: User, local_today: date) -> list[dict[str, Any]]:
    start_date = local_today - timedelta(days=6)
    user_record = db.session.get(User, user.id)
    logs = {
        log.log_date: log
        for log in user_record.daily_logs
        if start_date <= log.log_date <= local_today
    }
    dots = []
    for offset in range(7):
        current_date = start_date + timedelta(days=offset)
        log = logs.get(current_date)
        dots.append(
            {
                "date": current_date.isoformat(),
                "label": current_date.strftime("%a"),
                "hit_goal": bool(log and log.oz_completed >= log.target_oz),
            }
        )
    return dots


def build_dashboard_payload(user: User) -> dict[str, Any]:
    local_today = get_user_local_date(user)
    ensure_schedule_for_user_date(user, local_today)
    db.session.flush()
    target_oz = calculate_daily_oz(user, local_today)
    daily_log = get_or_create_daily_log(user.id, local_today, target_oz)
    start_utc, end_utc = local_date_to_utc_window(user, local_today)
    reminders = (
        Reminder.query.filter(
            Reminder.user_id == user.id,
            Reminder.scheduled_time >= start_utc,
            Reminder.scheduled_time < end_utc,
        )
        .order_by(Reminder.scheduled_time.asc())
        .all()
    )
    progress_percent = round((daily_log.oz_completed / daily_log.target_oz) * 100) if daily_log.target_oz else 0
    return {
        "phone": user.phone,
        "first_name": user.first_name,
        "timezone": user.timezone,
        "daily_goal": daily_log.target_oz,
        "today_completed_oz": daily_log.oz_completed,
        "remaining_oz": max(daily_log.target_oz - daily_log.oz_completed, 0),
        "progress_percent": min(progress_percent, 100),
        "streak": calculate_streak(user.id, local_today),
        "streak_days": serialize_streak_days(user, local_today),
        "schedule": [serialize_schedule_item(reminder, user.timezone) for reminder in reminders],
    }


def hydrate_user_from_payload(user: User, payload: dict[str, Any]) -> User:
    user.first_name = clean_name(payload.get("first_name"))
    user.weight_lbs = coerce_int(payload.get("weight_lbs"), "Weight", 80, 500)
    user.wake_time = parse_clock(payload.get("wake_time"), "Wake time")
    user.sleep_time = parse_clock(payload.get("sleep_time"), "Sleep time")
    user.timezone = str(payload.get("timezone") or "America/New_York")
    get_timezone({"timezone": user.timezone})

    activity_level = str(payload.get("activity_level") or "light").lower()
    if activity_level not in ACTIVITY_LEVELS:
        raise ValueError("Activity level is invalid.")
    user.activity_level = activity_level

    climate = str(payload.get("climate") or "temperate").lower()
    if climate not in CLIMATE_TYPES:
        raise ValueError("Climate is invalid.")
    user.climate = climate

    user.exercise_days = parse_exercise_days(payload.get("exercise_days", []))
    user.caffeine_cups = coerce_int(payload.get("caffeine_cups", 0), "Caffeine cups", 0, 12)
    user.alcohol_weekly = coerce_int(payload.get("alcohol_weekly", 0), "Alcohol drinks", 0, 40)
    user.active = True
    user.daily_oz_goal = calculate_daily_oz(user)
    return user


def create_app(test_config: dict[str, Any] | None = None) -> Flask:
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.getenv("SECRET_KEY", "dev-secret"),
        SQLALCHEMY_DATABASE_URI=normalize_database_url(os.getenv("DATABASE_URL")),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SQLALCHEMY_ENGINE_OPTIONS={"pool_pre_ping": True},
        JSON_SORT_KEYS=False,
        TESTING=False,
    )
    if test_config:
        app.config.update(test_config)

    CORS(app, resources={r"/api/*": {"origins": "*"}})
    db.init_app(app)

    with app.app_context():
        db.create_all()

    @app.get("/api/health")
    def health_check():
        return jsonify({"status": "ok"})

    @app.post("/api/onboard")
    def onboard():
        payload = request.get_json(silent=True) or {}
        try:
            phone = normalize_phone(str(payload.get("phone", "")))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        user = User.query.filter_by(phone=phone).one_or_none()
        is_new_user = user is None
        if is_new_user:
            user = User(phone=phone)
            db.session.add(user)

        try:
            user.phone = phone
            hydrate_user_from_payload(user, payload)
        except ValueError as exc:
            db.session.rollback()
            return jsonify({"error": str(exc)}), 400

        today = get_user_local_date(user)
        preview_schedule = generate_schedule(user, user.daily_oz_goal, today)

        response = {
            "phone": user.phone,
            "daily_goal": user.daily_oz_goal,
            "schedule_preview": serialize_preview_schedule(preview_schedule),
            "verification_required": not user.verified,
        }

        if user.verified:
            ensure_schedule_for_user_date(user, today, force=True)
            ensure_schedule_for_user_date(user, today + timedelta(days=1), force=True)
            db.session.commit()
            response["dashboard"] = build_dashboard_payload(user)
            return jsonify(response)

        verify_code = f"{random.randint(0, 999999):06d}"
        user.verify_code = verify_code
        user.verified = False
        db.session.commit()
        send_sms_message(user.phone, build_verification_message(verify_code))
        if os.getenv("EXPOSE_VERIFY_CODE", "0") == "1":
            response["debug_verify_code"] = verify_code
        return jsonify(response), 201 if is_new_user else 200

    @app.post("/api/verify")
    def verify():
        payload = request.get_json(silent=True) or {}
        try:
            phone = normalize_phone(str(payload.get("phone", "")))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        code = str(payload.get("code", "")).strip()
        user = User.query.filter_by(phone=phone).one_or_none()
        if user is None:
            return jsonify({"error": "No Quench account found for that phone."}), 404
        if user.verify_code != code:
            return jsonify({"error": "That verification code does not match."}), 400

        user.verified = True
        user.active = True
        user.verify_code = None
        today = get_user_local_date(user)
        ensure_schedule_for_user_date(user, today, force=True)
        ensure_schedule_for_user_date(user, today + timedelta(days=1), force=True)
        db.session.commit()

        welcome_schedule = generate_schedule(user, user.daily_oz_goal, today)
        send_sms_message(
            user.phone,
            f"Quench is live. Today's goal is {user.daily_oz_goal}oz across {len(welcome_schedule)} reminders. Reply DONE after each drink.",
        )
        return jsonify(build_dashboard_payload(user))

    @app.get("/api/dashboard/<path:phone_token>")
    def dashboard(phone_token: str):
        try:
            phone = normalize_phone(unquote(phone_token))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        user = User.query.filter_by(phone=phone, verified=True).one_or_none()
        if user is None:
            return jsonify({"error": "No verified Quench account found."}), 404
        payload = build_dashboard_payload(user)
        db.session.commit()
        return jsonify(payload)

    @app.post("/sms/inbound")
    def inbound_sms():
        response = MessagingResponse()
        raw_phone = request.form.get("From", "")
        command = request.form.get("Body", "").strip().upper()

        try:
            phone = normalize_phone(raw_phone)
        except ValueError:
            response.message(build_help_message())
            return str(response), 200, {"Content-Type": "application/xml"}

        user = User.query.filter_by(phone=phone).one_or_none()
        if user is None:
            response.message("You are not on a Quench plan yet. Sign up first, then reply here.")
            return str(response), 200, {"Content-Type": "application/xml"}

        if command == "STOP":
            user.active = False
            db.session.commit()
            response.message(build_unsubscribe_message())
            return str(response), 200, {"Content-Type": "application/xml"}

        if command == "HELP":
            response.message(build_help_message())
            return str(response), 200, {"Content-Type": "application/xml"}

        if command == "START":
            user.active = True
            db.session.commit()
            response.message("Quench is active again. Hydration reminders are back on.")
            return str(response), 200, {"Content-Type": "application/xml"}

        if command == "DONE":
            reminder = (
                Reminder.query.filter_by(user_id=user.id, completed=False)
                .filter(Reminder.sent.is_(True))
                .order_by(Reminder.scheduled_time.desc())
                .first()
            )
            if reminder is None:
                response.message("No open reminder found. If you already replied, you're caught up.")
                return str(response), 200, {"Content-Type": "application/xml"}

            reminder.completed = True
            reminder.completed_at = utcnow()
            local_date = utc_to_local(reminder.scheduled_time, user.timezone).date()
            target_oz = user.daily_oz_goal or calculate_daily_oz(user, local_date)
            daily_log = get_or_create_daily_log(user.id, local_date, target_oz)
            daily_log.oz_completed += reminder.amount_oz
            daily_log.reminders_completed += 1
            db.session.commit()
            streak = calculate_streak(user.id, get_user_local_date(user))
            response.message(
                build_completion_message(
                    user.first_name or "Hydration check",
                    reminder.amount_oz,
                    daily_log.oz_completed,
                    daily_log.target_oz,
                    streak,
                )
            )
            return str(response), 200, {"Content-Type": "application/xml"}

        if command == "SKIP":
            next_reminder = (
                Reminder.query.filter_by(user_id=user.id, sent=False, completed=False)
                .order_by(Reminder.scheduled_time.asc())
                .first()
            )
            if next_reminder is None:
                response.message("No future reminder to snooze right now.")
                return str(response), 200, {"Content-Type": "application/xml"}

            next_reminder.scheduled_time = max(next_reminder.scheduled_time, utcnow()) + timedelta(hours=4)
            db.session.commit()
            next_time_label = format_time_label(utc_to_local(next_reminder.scheduled_time, user.timezone))
            response.message(build_skip_message(user.first_name or "Hydration check", next_time_label))
            return str(response), 200, {"Content-Type": "application/xml"}

        response.message(build_help_message())
        return str(response), 200, {"Content-Type": "application/xml"}

    should_start_scheduler = (
        not app.config.get("TESTING")
        and os.getenv("RUN_SCHEDULER", "1") == "1"
        and (os.getenv("WERKZEUG_RUN_MAIN") == "true" or not app.debug)
    )
    if should_start_scheduler:
        init_scheduler(app)

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5001")), debug=True)
