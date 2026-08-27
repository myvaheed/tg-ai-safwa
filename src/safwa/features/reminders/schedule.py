"""Schedule arithmetic for Reminders: resolve free-text parameters, advance, describe.

Pure functions over a :class:`Schedule` value.  Nothing here opens a session or talks to
Telegram, so every branch is unit-testable without a database.

Two rules decide what ``date`` and ``time`` mean, and everything else follows from them:
``date`` is always a *start* date, never a fire clock; ``time`` is the fire clock when
weekdays are given and a start clock otherwise.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from datetime import date as date_type
from typing import Any
from zoneinfo import ZoneInfo

from ...constants import REMINDER_MIN_INTERVAL_MINUTES, WEEKDAY_NAMES
from ...enums import ScheduleKind

MINUTES_PER_DAY = 24 * 60
_WORKWEEK = ("Mon", "Tue", "Wed", "Thu", "Fri")


class ScheduleError(ValueError):
    """A set of resolved parameters that cannot become a schedule."""


@dataclass(frozen=True, slots=True)
class Schedule:
    """When a Reminder fires. Immutable; build a new one to change the timing."""

    kind: ScheduleKind
    weekdays: tuple[str, ...] = ()
    at_time: time | None = None
    anchor_at: datetime | None = None
    interval_minutes: int | None = None
    quiet_windows: tuple[tuple[time, time], ...] = ()

    @property
    def repeating(self) -> bool:
        return self.kind is not ScheduleKind.ONCE


# --- parsing --------------------------------------------------------------


def parse_clock(raw: str) -> time:
    try:
        return datetime.strptime(raw.strip(), "%H:%M").time()
    except ValueError as error:
        raise ScheduleError(f"A time must look like HH:MM, got {raw!r}") from error


def parse_day(raw: str) -> date_type:
    try:
        return datetime.strptime(raw.strip(), "%d.%m.%Y").date()
    except ValueError as error:
        raise ScheduleError(f"A date must look like dd.mm.yyyy, got {raw!r}") from error


def parse_window(raw: str) -> tuple[time, time]:
    start, _, end = raw.partition("-")
    if not end:
        raise ScheduleError(f"A quiet window must look like HH:MM-HH:MM, got {raw!r}")
    parsed = (parse_clock(start), parse_clock(end))
    if parsed[0] == parsed[1]:
        raise ScheduleError("A quiet window cannot start and end at the same time")
    return parsed


def _normalize_weekdays(days: Sequence[str] | None) -> tuple[str, ...]:
    if not days:
        return ()
    lookup = {name.lower(): name for name in WEEKDAY_NAMES}
    chosen: list[str] = []
    for raw in days:
        name = lookup.get(raw.strip().lower()[:3])
        if name is None:
            raise ScheduleError(f"Unknown weekday {raw!r}; use {', '.join(WEEKDAY_NAMES)}")
        if name not in chosen:
            chosen.append(name)
    return tuple(sorted(chosen, key=WEEKDAY_NAMES.index))


# --- resolution -----------------------------------------------------------


def resolve(
    *,
    days: Sequence[str] | None = None,
    clock: str | None = None,
    day: str | None = None,
    interval_minutes: int | None = None,
    quiet_windows: Sequence[str] | None = None,
    now: datetime,
    tz: ZoneInfo,
) -> Schedule:
    """Turn the setup session's parameters into a schedule, or say why they do not work.

    ``clock`` and ``day`` are the ``time`` and ``date`` tool parameters, renamed here only
    because both shadow a stdlib name.
    """
    weekdays = _normalize_weekdays(days)
    at_time = parse_clock(clock) if clock else None
    on_date = parse_day(day) if day else None
    windows = tuple(parse_window(item) for item in (quiet_windows or ()))

    if weekdays and interval_minutes is not None:
        raise ScheduleError(
            "Give either an interval or weekdays, not both — a Reminder repeats one way"
        )
    if on_date is not None and at_time is None:
        raise ScheduleError("A date needs an hour: give time as HH:MM as well")

    anchor = _combine(on_date, at_time, tz) if on_date is not None and at_time else None

    if interval_minutes is not None:
        if interval_minutes < REMINDER_MIN_INTERVAL_MINUTES:
            raise ScheduleError(
                f"An interval must be at least {REMINDER_MIN_INTERVAL_MINUTES} minutes"
            )
        _reject_all_day_windows(windows)
        if anchor is None and at_time is not None:
            anchor = _next_clock(at_time, now, tz)
        return Schedule(
            kind=ScheduleKind.INTERVAL,
            anchor_at=anchor or now,
            interval_minutes=interval_minutes,
            quiet_windows=windows,
        )

    if windows:
        raise ScheduleError("Quiet windows only apply to an interval schedule")

    if weekdays:
        if at_time is None:
            raise ScheduleError("Weekdays need a time: give time as HH:MM")
        kind = ScheduleKind.DAILY if len(weekdays) == len(WEEKDAY_NAMES) else ScheduleKind.WEEKLY
        return Schedule(kind=kind, weekdays=weekdays, at_time=at_time, anchor_at=anchor)

    if at_time is None:
        raise ScheduleError("Nothing to schedule: give a time, a date and time, or an interval")

    moment = anchor if anchor is not None else _next_clock(at_time, now, tz)
    if moment <= now:
        raise ScheduleError("That moment has already passed")
    return Schedule(kind=ScheduleKind.ONCE, at_time=at_time, anchor_at=moment)


def _reject_all_day_windows(windows: tuple[tuple[time, time], ...]) -> None:
    if not windows:
        return
    covered = bytearray(MINUTES_PER_DAY)
    for start, end in windows:
        first = start.hour * 60 + start.minute
        last = end.hour * 60 + end.minute
        if first < last:
            covered[first:last] = b"\x01" * (last - first)
        else:  # wraps midnight
            covered[first:] = b"\x01" * (MINUTES_PER_DAY - first)
            covered[:last] = b"\x01" * last
    if all(covered):
        raise ScheduleError("Quiet windows leave no time of day for the Reminder to fire")


# --- advancing ------------------------------------------------------------


def next_fire(
    schedule: Schedule, *, previous: datetime | None, now: datetime, tz: ZoneInfo
) -> datetime | None:
    """The first fire strictly after ``previous``, or the first fire ever when it is None.

    Returns None only when a one-shot has already fired — the caller deletes the row.
    """
    if schedule.kind is ScheduleKind.ONCE:
        return None if previous is not None else schedule.anchor_at

    if schedule.kind is ScheduleKind.INTERVAL:
        if previous is None:
            candidate = schedule.anchor_at or now
        else:
            candidate = previous + timedelta(minutes=schedule.interval_minutes or 0)
        return _clear_quiet_windows(candidate, schedule.quiet_windows, tz)

    if previous is not None:
        return _next_wall_clock(schedule, previous, inclusive=False, tz=tz)
    floor = max(schedule.anchor_at, now) if schedule.anchor_at else now
    return _next_wall_clock(schedule, floor, inclusive=True, tz=tz)


def roll_forward(
    schedule: Schedule, *, previous: datetime, now: datetime, tz: ZoneInfo
) -> datetime:
    """Skip every occurrence missed while the bot was down and land on the next one.

    Computed rather than iterated: a five-minute interval offline for a week would be two
    thousand steps.  A one-shot is returned untouched — it always fires, however late.
    """
    if schedule.kind is ScheduleKind.ONCE:
        return previous

    if schedule.kind is ScheduleKind.INTERVAL:
        step = timedelta(minutes=schedule.interval_minutes or 0)
        candidate = previous
        if candidate <= now and step:
            candidate += ((now - candidate) // step + 1) * step
        return _clear_quiet_windows(candidate, schedule.quiet_windows, tz)

    return _next_wall_clock(schedule, max(previous, now), inclusive=False, tz=tz)


def on_wall_clock(schedule: Schedule, moment: datetime, tz: ZoneInfo) -> bool:
    """Whether a stored instant still lands on this schedule's local weekday and clock.

    A daily or weekly schedule stores a *local* time, so moving `workspace.timezone` makes
    every stored `next_fire_at` name the wrong instant.  This is the exact test for that,
    and a no-op check when the zone has not moved.  Interval schedules are pure duration
    and always pass.
    """
    if schedule.kind not in {ScheduleKind.DAILY, ScheduleKind.WEEKLY}:
        return True
    if schedule.at_time is None:
        return True
    local = moment.astimezone(tz)
    return (
        local.time() == schedule.at_time
        and WEEKDAY_NAMES[local.weekday()] in schedule.weekdays
    )


def _next_wall_clock(
    schedule: Schedule, boundary: datetime, *, inclusive: bool, tz: ZoneInfo
) -> datetime:
    if schedule.at_time is None or not schedule.weekdays:
        raise ScheduleError("A daily or weekly schedule needs weekdays and a time")
    start_date = boundary.astimezone(tz).date()
    for offset in range(len(WEEKDAY_NAMES) + 1):
        day = start_date + timedelta(days=offset)
        if WEEKDAY_NAMES[day.weekday()] not in schedule.weekdays:
            continue
        candidate = _combine(day, schedule.at_time, tz)
        if candidate > boundary or (inclusive and candidate == boundary):
            return candidate
    raise ScheduleError("A weekly schedule needs at least one weekday")


def _clear_quiet_windows(
    candidate: datetime, windows: tuple[tuple[time, time], ...], tz: ZoneInfo
) -> datetime:
    """Push a candidate that landed inside a quiet window out to the window's end.

    The conversion to local time is redone here on every call and never cached: after a
    daylight-saving shift "09:00 local" is a different UTC instant than yesterday, so
    arithmetic kept purely in UTC would land on the wrong side of the window.
    """
    for _ in range(len(windows) + 1):
        local = candidate.astimezone(tz)
        window = _containing_window(local.time(), windows)
        if window is None:
            return candidate
        end = _combine(local.date(), window[1], tz)
        if end <= candidate:
            end = _combine(local.date() + timedelta(days=1), window[1], tz)
        candidate = end
    return candidate


def _containing_window(
    moment: time, windows: tuple[tuple[time, time], ...]
) -> tuple[time, time] | None:
    for start, end in windows:
        inside = start <= moment < end if start < end else (moment >= start or moment < end)
        if inside:
            return start, end
    return None


def _next_clock(at_time: time, now: datetime, tz: ZoneInfo) -> datetime:
    """The next local occurrence of a wall clock, strictly after ``now``."""
    today = now.astimezone(tz).date()
    candidate = _combine(today, at_time, tz)
    if candidate <= now:
        candidate = _combine(today + timedelta(days=1), at_time, tz)
    return candidate


def _combine(day: date_type, at_time: time, tz: ZoneInfo) -> datetime:
    return datetime.combine(day, at_time, tzinfo=tz).astimezone(UTC)


# --- persistence ----------------------------------------------------------


def schedule_columns(schedule: Schedule) -> dict[str, Any]:
    """The Reminder columns this schedule occupies, ready to splat onto a row."""
    return {
        "schedule_kind": schedule.kind.value,
        "weekdays": list(schedule.weekdays),
        "at_time": schedule.at_time,
        "anchor_at": schedule.anchor_at,
        "interval_minutes": schedule.interval_minutes,
        "quiet_windows": [f"{start:%H:%M}-{end:%H:%M}" for start, end in schedule.quiet_windows],
    }


def schedule_of(reminder: Any) -> Schedule:
    """Read a schedule back off a Reminder row."""
    return Schedule(
        kind=ScheduleKind(reminder.schedule_kind),
        weekdays=tuple(reminder.weekdays or ()),
        at_time=reminder.at_time,
        anchor_at=reminder.anchor_at,
        interval_minutes=reminder.interval_minutes,
        quiet_windows=tuple(parse_window(item) for item in (reminder.quiet_windows or ())),
    )


def schedule_payload(schedule: Schedule) -> dict[str, Any]:
    """The same schedule as JSON primitives, for a ``ProposalChange.values`` blob.

    Separate from :func:`schedule_columns` because that one hands real ``time`` and
    ``datetime`` objects to typed ORM columns, which a JSON column cannot store.
    """
    return {
        "schedule_kind": schedule.kind.value,
        "weekdays": list(schedule.weekdays),
        "at_time": f"{schedule.at_time:%H:%M}" if schedule.at_time else None,
        "anchor_at": schedule.anchor_at.isoformat() if schedule.anchor_at else None,
        "interval_minutes": schedule.interval_minutes,
        "quiet_windows": [f"{start:%H:%M}-{end:%H:%M}" for start, end in schedule.quiet_windows],
    }


def schedule_from_payload(payload: dict[str, Any]) -> Schedule:
    anchor = payload.get("anchor_at")
    at_time = payload.get("at_time")
    return Schedule(
        kind=ScheduleKind(payload["schedule_kind"]),
        weekdays=tuple(payload.get("weekdays") or ()),
        at_time=parse_clock(at_time) if at_time else None,
        anchor_at=datetime.fromisoformat(anchor) if anchor else None,
        interval_minutes=payload.get("interval_minutes"),
        quiet_windows=tuple(parse_window(item) for item in (payload.get("quiet_windows") or ())),
    )


# --- describing -----------------------------------------------------------


def describe(schedule: Schedule, *, tz: ZoneInfo, now: datetime | None = None) -> str:
    """One human phrase for a schedule.

    The single source for the proposal screen, the `/reminders` list and the Cue text, so those three can never disagree about what a Reminder does.
    """
    if schedule.kind is ScheduleKind.ONCE:
        if schedule.anchor_at is None:
            return "once"
        return f"once, at {schedule.anchor_at.astimezone(tz):%Y-%m-%d %H:%M}"

    if schedule.kind is ScheduleKind.INTERVAL:
        text = f"every {_humanize_interval(schedule.interval_minutes or 0)}"
        if schedule.quiet_windows:
            windows = ", ".join(
                f"{start:%H:%M}–{end:%H:%M}" for start, end in schedule.quiet_windows
            )
            text += f" except {windows}"
        return text + _start_suffix(schedule, tz, now)

    clock = f"{schedule.at_time:%H:%M}" if schedule.at_time else "?"
    if schedule.kind is ScheduleKind.DAILY:
        return f"every day at {clock}" + _start_suffix(schedule, tz, now)
    return f"every {_humanize_weekdays(schedule.weekdays)} at {clock}" + _start_suffix(
        schedule, tz, now
    )


def _start_suffix(schedule: Schedule, tz: ZoneInfo, now: datetime | None) -> str:
    """Name the start only while it is still ahead; afterwards it is history, not schedule."""
    if schedule.anchor_at is None or now is None or schedule.anchor_at <= now:
        return ""
    return f", from {schedule.anchor_at.astimezone(tz):%d.%m.%Y %H:%M}"


def _humanize_interval(minutes: int) -> str:
    if minutes and minutes % 60 == 0:
        hours = minutes // 60
        return "hour" if hours == 1 else f"{hours} hours"
    if minutes > 60:
        hours, rest = divmod(minutes, 60)
        return f"{hours} h {rest} min"
    return f"{minutes} minutes"


def _humanize_weekdays(weekdays: tuple[str, ...]) -> str:
    return "weekday" if weekdays == _WORKWEEK else ", ".join(weekdays)
