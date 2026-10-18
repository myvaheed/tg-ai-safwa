"""The shared Reminder schedule: resolve parameters, advance and describe.

Pure functions over a :class:`Schedule` value.  Nothing here opens a session or talks to
Telegram, so every branch is unit-testable without a database.

Two rules decide what ``first_day`` and ``time`` mean, and everything else follows from
them: ``first_day`` is always a *start* day, never a fire clock; ``time`` is the fire clock when
weekdays or days of the month are given and a start clock otherwise.
"""

from __future__ import annotations

import calendar
from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, time, timedelta
from datetime import date as date_type
from typing import Any
from zoneinfo import ZoneInfo

from ...constants import WEEKDAY_NAMES
from .model import ScheduleKind

MINUTES_PER_DAY = 24 * 60
REMINDER_MIN_INTERVAL_MINUTES = 5
# How late a missed *repeat* may still fire.  Past this it rolls forward silently, so a
# weekend offline cannot produce 32 messages at once.  A one-shot ignores this and
# always fires, however late.
REMINDER_CATCHUP_GRACE_MINUTES = 120
_WORKWEEK = ("Mon", "Tue", "Wed", "Thu", "Fri")
BOUNDS_NEED_REPEAT = "A total or a last day needs a Schedule that repeats: drop limit and last_day"
UNTIL_BEFORE_FIRST = "last_day is before the first time it runs: the start is first_day"
UNTIL_FORMS = "last_day is dd.mm.yyyy, a weekday such as Fri, or how long: 30 days, 2 weeks, 1 month, 1 year"
UNTIL_DESCRIPTION = (
    "The last day it runs on: dd.mm.yyyy or dd.mm, a weekday such as Fri, or how long from the "
    "start, such as 30 days, 2 weeks, 1 month or 1 year. Only when the text says."
)
# The prompt lines both timing sessions read first_day and last_day by.
BOUNDS_LINES = """first_day is the first day, last_day the last day: 'from 3 December' is first_day, 'until 25 December' or 'till Friday' is last_day.
How long stays words, never a date you count, and counts from today without first_day: 'for a month': last_day=1 month.
'from next week': first_day=Mon. 'from next month': first_day is the 1st of next month."""
MONTH_DAYS_DESCRIPTION = "Days of the month it runs on, 1 to 31; 31 is the last day of every month."
# A day of the month past the month's end is its last day.
LAST_MONTH_DAY = 31
# A day of the month comes round within 31 days, a weekday within 7.
_LONGEST_GAP_DAYS = 31
_RELATIVE_DAYS = {"today": 0, "tomorrow": 1}
# A repeat's start without a year lies at most this many days behind or ahead of today.
NEAREST_START_DAYS = 183
# How long ``last_day`` may say, in days or in calendar months.
_UNTIL_DAYS = {"day": 1, "week": 7}
_UNTIL_MONTHS = {"month": 1, "year": 12}


class ScheduleError(ValueError):
    """A set of resolved parameters that cannot become a schedule."""


@dataclass(frozen=True, slots=True)
class Condition:
    """Answers that make a Reminder due: which items, which answer, how many, in a row or not.

    `items` is how the screen names the items; `sql` is the SELECT of their ids that is run
    at every answer. `answer` is passed, missed or answered for a Check, done for an Action.
    """

    items: str
    sql: str
    answer: str
    count: int
    in_row: bool = False


@dataclass(frozen=True, slots=True)
class Schedule:
    """When a Reminder fires. Immutable; build a new one to change the timing."""

    kind: ScheduleKind
    weekdays: tuple[str, ...] = ()
    month_days: tuple[int, ...] = ()
    at_time: time | None = None
    anchor_at: datetime | None = None
    interval_minutes: int | None = None
    quiet_windows: tuple[tuple[time, time], ...] = ()
    condition: Condition | None = None
    # The last local day it fires on, and how many times it fires in all; None runs on.
    until: date_type | None = None
    total: int | None = None

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
    month_days: Sequence[int] | None = None,
    clock: str | None = None,
    day: str | None = None,
    interval_minutes: int | None = None,
    in_minutes: int | None = None,
    quiet_windows: Sequence[str] | None = None,
    until: str | None = None,
    limit: int | None = None,
    now: datetime,
    tz: ZoneInfo,
) -> Schedule:
    """Turn the setup session's parameters into a schedule, or say why they do not work.

    ``clock``, ``day`` and ``until`` take the ``time``, ``first_day`` and ``last_day`` tool
    parameters. ``in_minutes`` is a moment counted from ``now`` here,
    so the model never adds minutes to a clock. ``until`` and ``limit`` end a repeat: on its
    last day, and after that many fires.
    """
    weekdays = _normalize_weekdays(days)
    at_time = parse_clock(clock) if clock else None
    repeats = bool(weekdays or month_days or interval_minutes)
    on_date = start_day(day, at_time, now, tz, repeats=repeats) if day else None
    windows = tuple(parse_window(item) for item in (quiet_windows or ()))
    schedule = _timing(
        weekdays,
        tuple(sorted(set(month_days or ()))),
        at_time,
        on_date,
        interval_minutes,
        in_minutes,
        windows,
        now,
        tz,
    )
    if until is None and limit is None:
        return schedule
    if not schedule.repeating:
        raise ScheduleError(BOUNDS_NEED_REPEAT)
    last = parse_until(until, on_date or now.astimezone(tz).date()) if until else None
    schedule = replace(schedule, until=last, total=limit)
    if next_fire(schedule, previous=None, now=now, tz=tz) is None:
        raise ScheduleError(UNTIL_BEFORE_FIRST)
    return schedule


def _timing(
    weekdays: tuple[str, ...],
    month_days: tuple[int, ...],
    at_time: time | None,
    on_date: date_type | None,
    interval_minutes: int | None,
    in_minutes: int | None,
    windows: tuple[tuple[time, time], ...],
    now: datetime,
    tz: ZoneInfo,
) -> Schedule:
    if in_minutes is not None:
        if weekdays or month_days or at_time or on_date or interval_minutes is not None or windows:
            raise ScheduleError(
                "in_minutes is one moment from now: give no days, month_days, time, first_day or interval"
            )
        moment = (now + timedelta(minutes=in_minutes)).replace(second=0, microsecond=0)
        return Schedule(kind=ScheduleKind.ONCE, at_time=moment.astimezone(tz).time(), anchor_at=moment)

    if len(weekdays) == len(WEEKDAY_NAMES) and interval_minutes is not None:
        # Every day says nothing an interval does not.
        weekdays = ()
    if weekdays and interval_minutes is not None:
        raise ScheduleError(
            "Give either an interval or weekdays, not both — a Reminder repeats one way"
        )
    if weekdays and month_days:
        raise ScheduleError("Give days or month_days, not both")
    if month_days and interval_minutes is not None:
        raise ScheduleError("month_days fire at a time: give time instead of interval_minutes")
    if on_date is not None and at_time is None:
        raise ScheduleError("first_day needs an hour: give time as HH:MM as well")

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

    if month_days:
        if at_time is None:
            raise ScheduleError("month_days need a time: give time as HH:MM")
        return Schedule(
            kind=ScheduleKind.MONTHLY, month_days=month_days, at_time=at_time, anchor_at=anchor
        )

    if at_time is None:
        raise ScheduleError("Nothing to schedule: give a time, a date and time, or an interval")

    moment = anchor if anchor is not None else _next_clock(at_time, now, tz)
    if moment <= now:
        raise ScheduleError("That moment has already passed")
    return Schedule(kind=ScheduleKind.ONCE, at_time=at_time, anchor_at=moment)


def parse_phrase(raw: str, *, now: datetime, tz: ZoneInfo) -> Schedule:
    """A schedule typed by hand: `HH:MM`, `dd.mm.yyyy HH:MM`, weekdays and `HH:MM`, or
    `daily HH:MM`. Anything a Reminder's setup session would need the model for is not
    read here."""
    days: list[str] = []
    clock = day = None
    weekdays = {name.lower() for name in WEEKDAY_NAMES}
    for token in raw.replace(",", " ").split():
        lowered = token.lower()
        if lowered == "daily":
            days = list(WEEKDAY_NAMES)
        elif lowered[:3] in weekdays:
            days.append(token)
        elif ":" in token:
            clock = token
        elif "." in token:
            day = token
        else:
            raise ScheduleError(f"Cannot read {token!r}")
    return resolve(days=days or None, clock=clock, day=day, now=now, tz=tz)


def start_day(
    raw: str, at_time: time | None, now: datetime, tz: ZoneInfo, *, repeats: bool = False
) -> date_type:
    """A dd.mm.yyyy date; today or tomorrow; dd.mm, or a weekday name, for the next such day
    whose clock is still ahead, so the model never counts days or picks the year.

    A repeat's dd.mm is the one nearest today instead: one a few days past has begun.
    """
    today = now.astimezone(tz).date()
    if raw.strip().lower() in _RELATIVE_DAYS:
        return today + timedelta(days=_RELATIVE_DAYS[raw.strip().lower()])
    name = _weekday(raw)
    if name is None and raw.strip().count(".") != 1:
        return parse_day(raw)
    if name is None and repeats:
        return _nearest_day_month(raw, today)
    candidate = _on_or_after(today, name) if name else _day_month(raw, today)
    if at_time is not None and _combine(candidate, at_time, tz) <= now:
        candidate = (
            candidate + timedelta(days=len(WEEKDAY_NAMES))
            if name
            else candidate.replace(year=candidate.year + 1)
        )
    return candidate


def parse_until(raw: str, start: date_type) -> date_type:
    """The last day, inclusive: dd.mm.yyyy, or dd.mm on or after the start; a weekday for the
    next such day on or after it; or how long from it, so the model never counts days.

    A month from 19.10 runs through 18.11, and one from 31.01 through 28.02: a day the month
    lacks is the first of the next, less one.
    """
    text = raw.strip().lower()
    name = _weekday(text)
    if name is not None:
        return _on_or_after(start, name)
    amount, _, unit = text.partition(" ")
    unit = unit.strip().removesuffix("s")
    if amount.isdigit() and unit in _UNTIL_DAYS:
        return start + timedelta(days=int(amount) * _UNTIL_DAYS[unit] - 1)
    if amount.isdigit() and unit in _UNTIL_MONTHS:
        return _months_after(start, int(amount) * _UNTIL_MONTHS[unit]) - timedelta(days=1)
    try:
        return _day_month(text, start) if text.count(".") == 1 else parse_day(text)
    except ScheduleError as error:
        raise ScheduleError(UNTIL_FORMS) from error


def _day_month(raw: str, start: date_type) -> date_type:
    """dd.mm on or after the start."""
    day = parse_day(f"{raw.strip()}.{start.year}")
    return day if day >= start else day.replace(year=day.year + 1)


def _nearest_day_month(raw: str, today: date_type) -> date_type:
    """dd.mm in the year that puts it at most NEAREST_START_DAYS from today."""
    day = parse_day(f"{raw.strip()}.{today.year}")
    if (day - today).days > NEAREST_START_DAYS:
        return _months_after(day, -12)
    if (today - day).days > NEAREST_START_DAYS:
        return _months_after(day, 12)
    return day


def _weekday(raw: str) -> str | None:
    return {day.lower(): day for day in WEEKDAY_NAMES}.get(raw.strip().lower()[:3])


def _on_or_after(day: date_type, name: str) -> date_type:
    return day + timedelta(days=(WEEKDAY_NAMES.index(name) - day.weekday()) % 7)


def _months_after(day: date_type, months: int) -> date_type:
    year, month = divmod(day.month - 1 + months, 12)
    year, month = day.year + year, month + 1
    length = calendar.monthrange(year, month)[1]
    if day.day > length:
        return date_type(year, month, length) + timedelta(days=1)
    return day.replace(year=year, month=month)


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

    Returns None when a one-shot has already fired — the caller deletes the row — once the
    next fire falls after the last day, and for a condition, which an answer fires rather
    than the clock.
    """
    if schedule.kind is ScheduleKind.CONDITION:
        return None
    if schedule.kind is ScheduleKind.ONCE:
        return None if previous is not None else schedule.anchor_at

    if schedule.kind is ScheduleKind.INTERVAL:
        if previous is None:
            candidate = schedule.anchor_at or now
        else:
            candidate = previous + timedelta(minutes=schedule.interval_minutes or 0)
        return _by_last_day(schedule, _clear_quiet_windows(candidate, schedule.quiet_windows, tz), tz)

    if previous is not None:
        candidate = _next_wall_clock(schedule, previous, inclusive=False, tz=tz)
    else:
        floor = max(schedule.anchor_at, now) if schedule.anchor_at else now
        candidate = _next_wall_clock(schedule, floor, inclusive=True, tz=tz)
    return _by_last_day(schedule, candidate, tz)


def roll_forward(
    schedule: Schedule, *, previous: datetime, now: datetime, tz: ZoneInfo
) -> datetime | None:
    """Skip every occurrence missed while the bot was down and land on the next one, or None
    once that falls after the last day.

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
        return _by_last_day(schedule, _clear_quiet_windows(candidate, schedule.quiet_windows, tz), tz)

    return _by_last_day(
        schedule, _next_wall_clock(schedule, max(previous, now), inclusive=False, tz=tz), tz
    )


def _by_last_day(schedule: Schedule, moment: datetime, tz: ZoneInfo) -> datetime | None:
    if schedule.until is not None and moment.astimezone(tz).date() > schedule.until:
        return None
    return moment


def on_wall_clock(schedule: Schedule, moment: datetime, tz: ZoneInfo) -> bool:
    """Whether a stored instant still lands on this schedule's local day and clock.

    A daily, weekly or monthly schedule stores a *local* time, so moving `workspace.timezone`
    makes every stored `next_fire_at` name the wrong instant.  This is the exact test for
    that, and a no-op check when the zone has not moved.  Interval schedules are pure duration
    and always pass.
    """
    if schedule.kind not in {ScheduleKind.DAILY, ScheduleKind.WEEKLY, ScheduleKind.MONTHLY}:
        return True
    if schedule.at_time is None:
        return True
    local = moment.astimezone(tz)
    return local.time() == schedule.at_time and _runs_on(schedule, local.date())


def _runs_on(schedule: Schedule, day: date_type) -> bool:
    if schedule.month_days:
        length = calendar.monthrange(day.year, day.month)[1]
        return day.day in {min(item, length) for item in schedule.month_days}
    return WEEKDAY_NAMES[day.weekday()] in schedule.weekdays


def _next_wall_clock(
    schedule: Schedule, boundary: datetime, *, inclusive: bool, tz: ZoneInfo
) -> datetime:
    if schedule.at_time is None or not (schedule.weekdays or schedule.month_days):
        raise ScheduleError("A daily, weekly or monthly schedule needs days and a time")
    start_date = boundary.astimezone(tz).date()
    for offset in range(_LONGEST_GAP_DAYS + 1):
        day = start_date + timedelta(days=offset)
        if not _runs_on(schedule, day):
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
        "month_days": list(schedule.month_days),
        "at_time": schedule.at_time,
        "anchor_at": schedule.anchor_at,
        "interval_minutes": schedule.interval_minutes,
        "quiet_windows": [f"{start:%H:%M}-{end:%H:%M}" for start, end in schedule.quiet_windows],
        "condition": _condition_payload(schedule.condition),
        "until": schedule.until,
        "total": schedule.total,
    }


def _condition_payload(condition: Condition | None) -> dict[str, Any] | None:
    return asdict(condition) if condition is not None else None


def _condition_of(payload: dict[str, Any] | None) -> Condition | None:
    return Condition(**payload) if payload else None


def schedule_of(reminder: Any) -> Schedule:
    """Read a schedule back off a Reminder row."""
    return Schedule(
        kind=ScheduleKind(reminder.schedule_kind),
        weekdays=tuple(reminder.weekdays or ()),
        month_days=tuple(reminder.month_days or ()),
        at_time=reminder.at_time,
        anchor_at=reminder.anchor_at,
        interval_minutes=reminder.interval_minutes,
        quiet_windows=tuple(parse_window(item) for item in (reminder.quiet_windows or ())),
        condition=_condition_of(reminder.condition),
        until=reminder.until,
        total=reminder.total,
    )


def schedule_payload(schedule: Schedule) -> dict[str, Any]:
    """The same schedule as JSON primitives, for a ``ProposalChange.values`` blob.

    Separate from :func:`schedule_columns` because that one hands real ``time`` and
    ``datetime`` objects to typed ORM columns, which a JSON column cannot store.
    """
    return {
        "schedule_kind": schedule.kind.value,
        "weekdays": list(schedule.weekdays),
        "month_days": list(schedule.month_days),
        "at_time": f"{schedule.at_time:%H:%M}" if schedule.at_time else None,
        "anchor_at": schedule.anchor_at.isoformat() if schedule.anchor_at else None,
        "interval_minutes": schedule.interval_minutes,
        "quiet_windows": [f"{start:%H:%M}-{end:%H:%M}" for start, end in schedule.quiet_windows],
        "condition": _condition_payload(schedule.condition),
        "until": schedule.until.isoformat() if schedule.until else None,
        "total": schedule.total,
    }


def schedule_from_payload(payload: dict[str, Any]) -> Schedule:
    anchor = payload.get("anchor_at")
    at_time = payload.get("at_time")
    until = payload.get("until")
    return Schedule(
        kind=ScheduleKind(payload["schedule_kind"]),
        weekdays=tuple(payload.get("weekdays") or ()),
        month_days=tuple(payload.get("month_days") or ()),
        at_time=parse_clock(at_time) if at_time else None,
        anchor_at=datetime.fromisoformat(anchor) if anchor else None,
        interval_minutes=payload.get("interval_minutes"),
        quiet_windows=tuple(parse_window(item) for item in (payload.get("quiet_windows") or ())),
        condition=_condition_of(payload.get("condition")),
        until=date_type.fromisoformat(until) if until else None,
        total=payload.get("total"),
    )


# --- describing -----------------------------------------------------------


def describe(schedule: Schedule, *, tz: ZoneInfo, now: datetime | None = None) -> str:
    """One human phrase for a schedule.

    The single source for the proposal screen, the `/reminders` list and the Cue text, so those three can never disagree about what a Reminder does.
    """
    if schedule.kind is ScheduleKind.CONDITION:
        return _describe_condition(schedule.condition)

    if schedule.kind is ScheduleKind.ONCE:
        if schedule.anchor_at is None:
            return "once"
        moment = schedule.anchor_at.astimezone(tz)
        return f"once, on {moment:%a %d.%m.%Y} at {moment:%H:%M}"

    if schedule.kind is ScheduleKind.INTERVAL:
        text = f"every {_humanize_interval(schedule.interval_minutes or 0)}"
        if schedule.quiet_windows:
            windows = ", ".join(
                f"{start:%H:%M}–{end:%H:%M}" for start, end in schedule.quiet_windows
            )
            text += f" except {windows}"
    else:
        clock = f"{schedule.at_time:%H:%M}" if schedule.at_time else "?"
        text = f"{repeat_days(schedule)} at {clock}"
    text += _start_suffix(schedule, tz, now)
    if schedule.until is not None:
        text += f", until {schedule.until:%d.%m.%Y}"
    return text + (f", {schedule.total} times" if schedule.total else "")


# One answer and many, as a condition is worded: "each answer of", "every 30 answers of".
_ANSWER_WORDS = {
    "passed": ("Passed", "Passed"),
    "missed": ("Missed", "Missed"),
    "answered": ("answer", "answers"),
    "done": ("Done", "Done"),
}


def _describe_condition(condition: Condition) -> str:
    one, many = _ANSWER_WORDS[condition.answer]
    if condition.count == 1:
        return f"each {one} of {condition.items}"
    row = " in a row" if condition.in_row else ""
    return f"every {condition.count} {many}{row} of {condition.items}"


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


def repeat_days(schedule: Schedule) -> str:
    """The days a wall-clock schedule fires on: every day, every weekday, every Mon, Thu, or
    on the 1st and 15th of each month."""
    if schedule.month_days:
        names = [_month_day(day) for day in schedule.month_days]
        words = names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"
        return f"on the {words} of each month"
    if schedule.kind is ScheduleKind.DAILY:
        return "every day"
    return "every weekday" if schedule.weekdays == _WORKWEEK else f"every {', '.join(schedule.weekdays)}"


def _month_day(day: int) -> str:
    if day == LAST_MONTH_DAY:
        return "last day"
    suffix = "th" if 10 < day % 100 < 14 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix}"
