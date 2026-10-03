"""Calendar windows, quotas and appointments. No model is needed to execute a rule."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from ..reminders.api import next_fire, roll_forward, schedule_from_payload


def period_start(at: datetime, period: str, tz: ZoneInfo) -> datetime:
    local = at.astimezone(tz)
    day = local.date()
    if period == "week":
        day -= timedelta(days=day.weekday())
    return datetime.combine(day, time(), tzinfo=tz).astimezone(UTC)


def period_end(start: datetime, period: str, tz: ZoneInfo) -> datetime:
    day = start.astimezone(tz).date() + timedelta(days=7 if period == "week" else 1)
    return datetime.combine(day, time(), tzinfo=tz).astimezone(UTC)


def first_slot(rule: dict[str, Any], at: datetime, tz: ZoneInfo) -> datetime | None:
    if rule["kind"] == "quota":
        return period_start(at, rule["period"], tz)
    if rule["kind"] == "after_completion":
        return None
    return next_fire(schedule_from_payload(rule["timing"]), previous=None, now=at, tz=tz)


def next_slot(
    rule: dict[str, Any], previous: datetime | None, now: datetime, completed: int, tz: ZoneInfo
) -> tuple[bool, datetime | None]:
    """Whether to open a successor, and the period it belongs to."""
    if rule["kind"] == "after_completion":
        return True, None
    if rule["kind"] == "quota":
        current = period_start(now, rule["period"], tz)
        start = max(previous or current, current)
        if previous == start and completed >= rule["count"]:
            start = period_end(start, rule["period"], tz)
        return True, start
    timing = schedule_from_payload(rule["timing"])
    if timing.kind == "once":
        return False, None
    previous = previous or now
    return True, roll_forward(timing, previous=previous, now=max(now, previous), tz=tz)


def windows(
    rule: dict[str, Any], start: datetime, end: datetime, tz: ZoneInfo
) -> list[tuple[datetime, datetime, int]]:
    """Periods intersecting [start, end). A partial week still has its full quota."""
    if rule["kind"] == "after_completion":
        return []
    result = []
    if rule["kind"] == "quota":
        at = period_start(start, rule["period"], tz)
        while at < end:
            until = period_end(at, rule["period"], tz)
            result.append((at, until, rule["count"]))
            at = until
        return result
    timing = schedule_from_payload(rule["timing"])
    counts: dict[datetime, int] = {}
    at = next_fire(timing, previous=None, now=start, tz=tz)
    if at is not None and at < start and timing.kind != "once":
        at = roll_forward(timing, previous=at, now=start - timedelta(microseconds=1), tz=tz)
    while at is not None and at < end:
        if at >= start:
            day = period_start(at, "day", tz)
            counts[day] = counts.get(day, 0) + 1
        if timing.kind == "once":
            break
        at = roll_forward(timing, previous=at, now=max(start, at), tz=tz)
    return [(day, period_end(day, "day", tz), count) for day, count in counts.items()]
