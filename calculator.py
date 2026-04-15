from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


ACTIVITY_BONUS = {
    "sedentary": 0,
    "light": 4,
    "moderate": 8,
    "heavy": 12,
}


def _read(user: Any, field: str, default: Any = None) -> Any:
    if isinstance(user, dict):
        return user.get(field, default)
    return getattr(user, field, default)


def get_timezone(user: Any) -> ZoneInfo:
    tz_name = _read(user, "timezone", "America/New_York") or "America/New_York"
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("America/New_York")


def get_local_date(user: Any, target_date: date | None = None) -> date:
    if target_date is not None:
        return target_date
    return datetime.now(get_timezone(user)).date()


def coerce_time(value: Any, fallback: time) -> time:
    if isinstance(value, time):
        return value
    if isinstance(value, str):
        hour, minute = value.split(":")
        return time(hour=int(hour), minute=int(minute))
    return fallback


def _exercise_days(user: Any) -> list[int]:
    value = _read(user, "exercise_days", []) or []
    if isinstance(value, list):
        return [int(day) for day in value]
    return []


def is_exercise_day(user: Any, target_date: date | None = None) -> bool:
    local_date = get_local_date(user, target_date)
    return local_date.isoweekday() in _exercise_days(user)


def calculate_daily_oz(user: Any, target_date: date | None = None) -> int:
    weight_lbs = int(_read(user, "weight_lbs", 160) or 160)
    caffeine_cups = int(_read(user, "caffeine_cups", 0) or 0)
    alcohol_weekly = int(_read(user, "alcohol_weekly", 0) or 0)
    climate = (_read(user, "climate", "temperate") or "temperate").lower()
    activity_level = (_read(user, "activity_level", "light") or "light").lower()

    base = weight_lbs * 0.5
    caffeine_penalty = caffeine_cups * 8
    climate_add = 8 if climate in {"hot", "dry"} else 0
    alcohol_add = alcohol_weekly * 8 / 7
    exercise_add = 16 if is_exercise_day(user, target_date) else 0
    activity_add = ACTIVITY_BONUS.get(activity_level, 4)
    food_offset = 0.8

    total = (base + caffeine_penalty + climate_add + alcohol_add + exercise_add + activity_add) * food_offset
    return max(48, min(160, round(total)))


def format_time_label(local_dt: datetime) -> str:
    hour = local_dt.hour % 12 or 12
    suffix = "am" if local_dt.hour < 12 else "pm"
    return f"{hour}:{local_dt.minute:02d}{suffix}"


def _allocate_amounts(target_oz: int, weights: list[float]) -> list[int]:
    if not weights:
        return []

    weight_total = sum(weights)
    amounts = [max(5, round(target_oz * weight / weight_total)) for weight in weights]
    diff = target_oz - sum(amounts)
    boost_order = sorted(range(len(weights)), key=lambda idx: weights[idx], reverse=True)
    trim_order = sorted(range(len(weights)), key=lambda idx: weights[idx])

    while diff > 0:
        for idx in boost_order:
            amounts[idx] += 1
            diff -= 1
            if diff == 0:
                break

    while diff < 0:
        for idx in trim_order:
            if amounts[idx] <= 5:
                continue
            amounts[idx] -= 1
            diff += 1
            if diff == 0:
                break
        else:
            break
    return amounts


def generate_schedule(
    user: Any, target_oz: int | None = None, target_date: date | None = None
) -> list[dict[str, Any]]:
    tz = get_timezone(user)
    local_date = get_local_date(user, target_date)
    wake_time = coerce_time(_read(user, "wake_time"), time(7, 0))
    sleep_time = coerce_time(_read(user, "sleep_time"), time(23, 0))
    target_oz = target_oz or calculate_daily_oz(user, local_date)

    wake_dt = datetime.combine(local_date, wake_time, tzinfo=tz)
    sleep_dt = datetime.combine(local_date, sleep_time, tzinfo=tz)
    if sleep_dt <= wake_dt:
        sleep_dt += timedelta(days=1)

    first_reminder = wake_dt + timedelta(minutes=15)
    last_reminder = sleep_dt - timedelta(hours=2)
    if last_reminder <= first_reminder:
        last_reminder = first_reminder + timedelta(hours=8)

    window_hours = max((last_reminder - first_reminder).total_seconds() / 3600, 4)
    reminder_count = max(6, min(12, round(target_oz / 8)))
    density_cap = max(4, int(window_hours / 0.8))
    reminder_count = min(reminder_count, density_cap) if density_cap >= 4 else reminder_count
    reminder_count = max(reminder_count, 4)

    positions = [0 if reminder_count == 1 else idx / (reminder_count - 1) for idx in range(reminder_count)]
    weights = []
    for position in positions:
        weight = 1.18 - (position * 0.34)
        if is_exercise_day(user, local_date) and 0.35 <= position <= 0.65:
            weight += 0.14
        weights.append(max(0.65, weight))

    amounts = _allocate_amounts(target_oz, weights)
    span_minutes = int((last_reminder - first_reminder).total_seconds() / 60)

    schedule = []
    for idx, position in enumerate(positions):
        eased_position = position ** 1.1
        local_dt = first_reminder + timedelta(minutes=round(span_minutes * eased_position))
        utc_dt = local_dt.astimezone(timezone.utc).replace(tzinfo=None)
        schedule.append(
            {
                "scheduled_time_local": local_dt,
                "scheduled_time_utc": utc_dt,
                "time_label": format_time_label(local_dt),
                "amount_oz": amounts[idx],
                "iso_local": local_dt.isoformat(),
            }
        )
    return schedule

