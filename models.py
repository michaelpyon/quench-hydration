from __future__ import annotations

from datetime import date, datetime, time as dt_time, timezone
from typing import Any

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON


db = SQLAlchemy()


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(db.Model):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    first_name: Mapped[str | None] = mapped_column(db.String(80))
    phone: Mapped[str] = mapped_column(db.String(20), unique=True, nullable=False, index=True)
    weight_lbs: Mapped[int | None] = mapped_column(db.Integer)
    wake_time: Mapped[dt_time | None] = mapped_column(db.Time)
    sleep_time: Mapped[dt_time | None] = mapped_column(db.Time)
    timezone: Mapped[str] = mapped_column(db.String(50), default="America/New_York")
    activity_level: Mapped[str | None] = mapped_column(db.String(20))
    exercise_days: Mapped[list[int]] = mapped_column(JSON, default=list)
    climate: Mapped[str | None] = mapped_column(db.String(20))
    caffeine_cups: Mapped[int] = mapped_column(db.Integer, default=0)
    alcohol_weekly: Mapped[int] = mapped_column(db.Integer, default=0)
    verified: Mapped[bool] = mapped_column(db.Boolean, default=False, nullable=False)
    active: Mapped[bool] = mapped_column(db.Boolean, default=True, nullable=False)
    verify_code: Mapped[str | None] = mapped_column(db.String(6))
    daily_oz_goal: Mapped[int | None] = mapped_column(db.Integer)
    last_weekly_summary_for: Mapped[date | None] = mapped_column(db.Date)
    created_at: Mapped[datetime] = mapped_column(db.DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        db.DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    reminders: Mapped[list["Reminder"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    daily_logs: Mapped[list["DailyLog"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Reminder(db.Model):
    __tablename__ = "reminders"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(db.ForeignKey("users.id"), index=True, nullable=False)
    scheduled_time: Mapped[datetime] = mapped_column(db.DateTime, index=True, nullable=False)
    amount_oz: Mapped[int] = mapped_column(db.Integer, nullable=False)
    sent: Mapped[bool] = mapped_column(db.Boolean, default=False, nullable=False)
    completed: Mapped[bool] = mapped_column(db.Boolean, default=False, nullable=False)
    message_sid: Mapped[str | None] = mapped_column(db.String(50))
    message_key: Mapped[str | None] = mapped_column(db.String(20))
    sent_at: Mapped[datetime | None] = mapped_column(db.DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(db.DateTime)
    created_at: Mapped[datetime] = mapped_column(db.DateTime, default=utcnow, nullable=False)

    user: Mapped[User] = relationship(back_populates="reminders")


class DailyLog(db.Model):
    __tablename__ = "daily_logs"
    __table_args__ = (UniqueConstraint("user_id", "log_date", name="uq_daily_logs_user_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(db.ForeignKey("users.id"), index=True, nullable=False)
    log_date: Mapped[date] = mapped_column(db.Date, nullable=False)
    oz_completed: Mapped[int] = mapped_column(db.Integer, default=0, nullable=False)
    target_oz: Mapped[int] = mapped_column(db.Integer, nullable=False)
    reminders_sent: Mapped[int] = mapped_column(db.Integer, default=0, nullable=False)
    reminders_completed: Mapped[int] = mapped_column(db.Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(db.DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        db.DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    user: Mapped[User] = relationship(back_populates="daily_logs")


def get_or_create_daily_log(user_id: int, log_date: date, target_oz: int) -> DailyLog:
    daily_log = DailyLog.query.filter_by(user_id=user_id, log_date=log_date).one_or_none()
    if daily_log is None:
        daily_log = DailyLog(user_id=user_id, log_date=log_date, target_oz=target_oz)
        db.session.add(daily_log)
        db.session.flush()
    else:
        daily_log.target_oz = target_oz
    return daily_log


def calculate_streak(user_id: int, end_date: date | None = None) -> int:
    if end_date is None:
        end_date = date.today()

    logs = (
        DailyLog.query.filter(
            DailyLog.user_id == user_id,
            DailyLog.log_date <= end_date,
            DailyLog.oz_completed >= DailyLog.target_oz,
        )
        .order_by(DailyLog.log_date.desc())
        .all()
    )

    streak = 0
    cursor = end_date
    hits = {log.log_date for log in logs}
    while cursor in hits:
        streak += 1
        cursor = cursor.fromordinal(cursor.toordinal() - 1)
    return streak


def serialize_daily_log(daily_log: DailyLog | None) -> dict[str, Any]:
    if daily_log is None:
        return {
            "oz_completed": 0,
            "target_oz": 0,
            "reminders_sent": 0,
            "reminders_completed": 0,
        }

    return {
        "oz_completed": daily_log.oz_completed,
        "target_oz": daily_log.target_oz,
        "reminders_sent": daily_log.reminders_sent,
        "reminders_completed": daily_log.reminders_completed,
    }
