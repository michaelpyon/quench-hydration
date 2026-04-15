from __future__ import annotations

import logging
import os
from itertools import product
from typing import Iterable

from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client


logger = logging.getLogger(__name__)


HYDRATION_TIPS = [
    "A mild headache is often thirst in disguise.",
    "Hydration helps your energy feel steadier through the afternoon.",
    "Drinking earlier in the day makes evenings easier.",
    "Even light dehydration can dent focus at your desk.",
    "Your body absorbs smaller sips better than one huge catch-up chug.",
    "Water before lunch is one of the easiest habit wins.",
    "Caffeine counts less than people hope, so top it off with water.",
    "The thirst signal usually lags behind what your body actually needs.",
    "A hydrated afternoon tends to mean fewer 3pm crashes.",
    "Office air can dry you out faster than you notice.",
    "Front-loading water makes bedtime less annoying.",
    "The first half of the day does most of the hydration heavy lifting.",
    "A few steady reminders beat one guilt-trip reminder every time.",
    "If your bottle is still full at noon, the day is already getting away from you.",
    "Consistency matters more than heroic one-time chugs.",
    "A quick water break is also a fast posture break.",
    "Drinking with meals is a simple way to stay on pace.",
    "Dry climate days usually ask for a little more.",
    "Workout days quietly raise the floor on what you need.",
    "Better hydration often shows up first as better focus.",
    "You do not need perfect timing, just better timing.",
    "A finished bottle by lunch sets the tone for the rest of the day.",
    "Water is one of the cheapest energy upgrades you have.",
    "Small hydration gaps stack up across a workday.",
    "If you wait to feel parched, you are already behind.",
    "Staying ahead of thirst is easier than catching up later.",
    "Hydration helps more when it is spread across the day.",
    "Coffee plus water is a better deal than coffee alone.",
    "Your future self would love one less dehydrated evening.",
    "A two-minute water break can reset your attention faster than doomscrolling.",
]


REMINDER_TEMPLATES = [
    "{name}, time for {oz}oz. Finish this one and you'll be at {running_total}/{daily_goal}oz today. {tip}",
    "{name}, hydration check: {oz}oz now gets you to {running_total}/{daily_goal}oz today. {tip}",
    "{name}, quick water break. Knock out {oz}oz and you'll hit {running_total}/{daily_goal}oz. {tip}",
    "{name}, your next move is {oz}oz of water. That puts you at {running_total}/{daily_goal}oz today. {tip}",
    "{name}, this is your calm nudge: drink {oz}oz. You'll land at {running_total}/{daily_goal}oz. {tip}",
    "{name}, it's time to refill the tank with {oz}oz. That takes you to {running_total}/{daily_goal}oz today. {tip}",
    "{name}, desk hydration break: {oz}oz now, {running_total}/{daily_goal}oz after. {tip}",
    "{name}, your bottle is calling. {oz}oz gets you to {running_total}/{daily_goal}oz today. {tip}",
    "{name}, one small win: drink {oz}oz. You'll be sitting at {running_total}/{daily_goal}oz. {tip}",
    "{name}, keep the streak moving. {oz}oz now means {running_total}/{daily_goal}oz today. {tip}",
    "{name}, a fast water break now saves a sluggish afternoon later. {oz}oz brings you to {running_total}/{daily_goal}oz. {tip}",
    "{name}, gentle nudge: {oz}oz of water next. You'll be at {running_total}/{daily_goal}oz for the day. {tip}",
    "{name}, your plan says {oz}oz right now. That would move you to {running_total}/{daily_goal}oz. {tip}",
    "{name}, stay ahead of the crash: drink {oz}oz. You're then at {running_total}/{daily_goal}oz today. {tip}",
    "{name}, this is the easy kind of discipline: {oz}oz now, {running_total}/{daily_goal}oz after. {tip}",
    "{name}, water first. {oz}oz gets you to {running_total}/{daily_goal}oz and keeps the plan alive. {tip}",
    "{name}, tiny action, big payoff: {oz}oz now for {running_total}/{daily_goal}oz on the day. {tip}",
    "{name}, this reminder is short on purpose: drink {oz}oz. You'll be at {running_total}/{daily_goal}oz. {tip}",
    "{name}, if you do this {oz}oz now, today's total becomes {running_total}/{daily_goal}oz. {tip}",
    "{name}, a little hydration now makes the rest of the day easier. {oz}oz gets you to {running_total}/{daily_goal}oz. {tip}",
    "{name}, make the next sip count. {oz}oz takes you to {running_total}/{daily_goal}oz today. {tip}",
    "{name}, keep pace with the version of you that actually drinks water. {oz}oz now for {running_total}/{daily_goal}oz. {tip}",
    "{name}, quick reset: {oz}oz of water and you're up to {running_total}/{daily_goal}oz. {tip}",
    "{name}, one bottle move at a time. {oz}oz here puts you at {running_total}/{daily_goal}oz today. {tip}",
    "{name}, hydration math says {oz}oz next. That gets you to {running_total}/{daily_goal}oz today. {tip}",
]


def build_verification_message(code: str) -> str:
    return f"Quench verification code: {code}. Reply STOP any time to opt out."


def build_help_message() -> str:
    return "Reply DONE when you drink, SKIP to snooze 4hrs, STOP to cancel."


def build_completion_message(name: str, oz: int, running_total: int, daily_goal: int, streak: int) -> str:
    streak_line = f" {streak}-day streak." if streak > 0 else ""
    if running_total >= daily_goal:
        return f"{name}, done. {running_total}/{daily_goal}oz and goal hit for today.{streak_line}"
    return f"{name}, logged {oz}oz. You're at {running_total}/{daily_goal}oz today.{streak_line}"


def build_skip_message(name: str, next_time_label: str | None) -> str:
    if next_time_label:
        return f"{name}, snoozed. I'll check back in around {next_time_label}."
    return f"{name}, snoozed for 4 hours."


def build_unsubscribe_message() -> str:
    return "Quench paused. You will not receive more reminders. Reply START in the dashboard to come back."


def build_weekly_summary(name: str, hit_days: int, streak: int) -> str:
    streak_line = f" Current streak: {streak} days." if streak else ""
    return f"{name}, you hit your hydration goal {hit_days}/7 days this week.{streak_line}"


def choose_message_key(recent_keys: Iterable[str], seed: int) -> str:
    used = set(recent_keys)
    combo_count = len(REMINDER_TEMPLATES) * len(HYDRATION_TIPS)
    for offset in range(combo_count):
        template_idx = (seed + offset) % len(REMINDER_TEMPLATES)
        tip_idx = ((seed * 3) + offset) % len(HYDRATION_TIPS)
        key = f"{template_idx}:{tip_idx}"
        if key not in used:
            return key
    return "0:0"


def build_reminder_message(
    *,
    first_name: str | None,
    oz: int,
    running_total: int,
    daily_goal: int,
    recent_keys: Iterable[str],
    seed: int,
) -> tuple[str, str]:
    message_key = choose_message_key(recent_keys, seed)
    template_idx, tip_idx = [int(part) for part in message_key.split(":")]
    name = first_name or "Hydration check"
    body = REMINDER_TEMPLATES[template_idx].format(
        name=name,
        oz=oz,
        running_total=running_total,
        daily_goal=daily_goal,
        tip=HYDRATION_TIPS[tip_idx],
    )
    return body, message_key


def send_sms_message(to_number: str, body: str) -> str:
    account_sid = os.getenv("TWILIO_ACCOUNT_SID")
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    from_number = os.getenv("TWILIO_PHONE_NUMBER")

    if not all([account_sid, auth_token, from_number]):
        logger.info("Twilio credentials missing. Skipping live send to %s: %s", to_number, body)
        return "mock-message-sid"

    client = Client(account_sid, auth_token)
    try:
        message = client.messages.create(body=body, from_=from_number, to=to_number)
        return message.sid
    except TwilioRestException as exc:
        logger.exception("Twilio send failed: %s", exc)
        raise

