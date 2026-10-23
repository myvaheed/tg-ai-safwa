"""Calendar windows, quotas, appointments and deadlines. No model is needed to execute a rule."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from ..reminders.api import current_moment, next_fire, roll_forward, schedule_from_payload

# A Deadline or an appointment without a clock is due by the end of its day.
END_OF_DAY = time(23, 59)
# The days of a quota period, a calendar month counted as 30 on average.
_PERIOD_DAYS = {"day": 1, "week": 7, "month": 30}


def period_start(at: datetime, period: str, tz: ZoneInfo) -> datetime:
    day = at.astimezone(tz).date()
    if period == "week":
        day -= timedelta(days=day.weekday())
    elif period == "month":
        day = day.replace(day=1)
    return _midnight(day, tz)


def period_end(start: datetime, period: str, tz: ZoneInfo) -> datetime:
    day = start.astimezone(tz).date()
    if period == "month":
        return _midnight((day.replace(day=1) + timedelta(days=32)).replace(day=1), tz)
    return _midnight(day + timedelta(days=_PERIOD_DAYS[period]), tz)


def deadline_moment(rule: dict[str, Any], tz: ZoneInfo) -> datetime:
    clock = time.fromisoformat(rule["time"]) if rule["time"] else END_OF_DAY
    return datetime.combine(date.fromisoformat(rule["date"]), clock, tzinfo=tz).astimezone(UTC)


def from_start(rule: dict[str, Any], at: datetime, tz: ZoneInfo) -> datetime:
    """The moment, or the quota's first day when that is later."""
    if "start" not in rule:
        return at
    return max(at, _midnight(date.fromisoformat(rule["start"]), tz))


def past_until(rule: dict[str, Any], at: datetime, tz: ZoneInfo) -> bool:
    """Whether a slot falls after the Schedule's last day."""
    return "until" in rule and at.astimezone(tz).date() > date.fromisoformat(rule["until"])


def first_slot(rule: dict[str, Any], at: datetime, tz: ZoneInfo) -> datetime | None:
    if rule["kind"] == "quota":
        return period_start(from_start(rule, at, tz), rule["period"], tz)
    if rule["kind"] == "after_completion":
        return None
    if rule["kind"] == "deadline":
        return deadline_moment(rule, tz)
    return next_fire(schedule_from_payload(rule["timing"]), previous=None, now=at, tz=tz)


def current_slot(rule: dict[str, Any], at: datetime, tz: ZoneInfo) -> datetime | None:
    """The slot whose period holds `at`, or the first one while `at` is before the Schedule
    starts: a quota's period, or a repeating time's moment. None for a rule without periods."""
    if rule["kind"] == "quota":
        return period_start(from_start(rule, at, tz), rule["period"], tz)
    if rule["kind"] != "fixed":
        return None
    return current_moment(schedule_from_payload(rule["timing"]), at, tz)


def repeats(rule: dict[str, Any]) -> bool:
    """Whether the rule opens one successor after another."""
    if rule["kind"] in {"after_completion", "quota"}:
        return True
    return rule["kind"] == "fixed" and schedule_from_payload(rule["timing"]).repeating


def next_slot(
    rule: dict[str, Any], previous: datetime | None, now: datetime, completed: int, tz: ZoneInfo
) -> tuple[bool, datetime | None]:
    """Whether to open a successor, and the period it belongs to."""
    if rule["kind"] == "after_completion":
        return True, None
    if rule["kind"] == "quota":
        current = period_start(from_start(rule, now, tz), rule["period"], tz)
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
    """Periods intersecting [start, end), each with the executions planned inside.

    A quota period covered only partly gets its share by local days covered out of the days
    it has, rounded. Nothing is planned before a quota's first day or after the last day.
    """
    if rule["kind"] == "after_completion":
        return []
    start = from_start(rule, start, tz)
    if "until" in rule:
        end = min(end, _midnight(date.fromisoformat(rule["until"]) + timedelta(days=1), tz))
    if start >= end:
        return []
    result = []
    if rule["kind"] == "quota":
        at = period_start(start, rule["period"], tz)
        while at < end:
            until = period_end(at, rule["period"], tz)
            first = max(at, start).astimezone(tz).date()
            last = (min(until, end) - timedelta(microseconds=1)).astimezone(tz).date()
            covered = (last - first).days + 1
            days = (until.astimezone(tz).date() - at.astimezone(tz).date()).days
            result.append((at, until, round(rule["count"] * covered / days)))
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


def daily_executions(rule: dict[str, Any]) -> float:
    """The most executions the rule plans for one day, on average for a weekly or monthly
    quota."""
    if rule["kind"] == "quota":
        return rule["count"] / _PERIOD_DAYS[rule["period"]]
    if rule["kind"] == "fixed" and rule["timing"]["interval_minutes"]:
        return 24 * 60 / rule["timing"]["interval_minutes"]
    return 1


def _midnight(day: date, tz: ZoneInfo) -> datetime:
    return datetime.combine(day, time(), tzinfo=tz).astimezone(UTC)
