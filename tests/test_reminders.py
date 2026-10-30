from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from safwa.features.reminders.api import (
    Schedule,
    ScheduleError,
    describe,
    next_fire,
    parse_windows,
    resolve,
    roll_forward,
    schedule_columns,
    schedule_from_payload,
    schedule_of,
    schedule_payload,
)
from safwa.features.reminders.model import ScheduleKind

TZ = ZoneInfo("Europe/Istanbul")  # UTC+3 all year, so plain cases stay readable
BERLIN = ZoneInfo("Europe/Berlin")  # observes DST, for the wall-clock cases
NOW = datetime(2026, 8, 13, 9, 0, tzinfo=UTC)  # a Thursday


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


def quiet(schedule: Schedule, *windows: str) -> Schedule:
    """The schedule as it fires with these Don't disturb windows."""
    return replace(schedule, quiet_windows=parse_windows(windows))


class FakeRow:
    """The Reminder columns `schedule_of` reads, without a database."""

    def __init__(self, **columns: object) -> None:
        self.__dict__.update(columns)


# --- resolution -----------------------------------------------------------


def test_interval_alone_starts_now():
    """RM-SCHEDULE-003 — tests/brd/reminders.feature"""
    schedule = resolve(interval_minutes=120, now=NOW, tz=TZ)
    assert schedule.kind is ScheduleKind.INTERVAL
    assert schedule.anchor_at == NOW
    assert schedule.repeating


def test_interval_with_time_starts_at_that_clocks_next_occurrence():
    """RM-SCHEDULE-003 — tests/brd/reminders.feature"""
    # 09:00 UTC is 12:00 local, so 08:00 local is tomorrow.
    schedule = resolve(interval_minutes=120, clock="08:00", now=NOW, tz=TZ)
    assert schedule.anchor_at == utc(2026, 8, 14, 5, 0)
    upcoming = resolve(interval_minutes=120, clock="13:00", now=NOW, tz=TZ)
    assert upcoming.anchor_at == utc(2026, 8, 13, 10, 0)


def test_interval_with_date_and_time_starts_at_that_exact_moment():
    """RM-SCHEDULE-003 — tests/brd/reminders.feature"""
    schedule = resolve(
        interval_minutes=120, clock="09:00", day="01.09.2026", now=NOW, tz=TZ
    )
    assert schedule.anchor_at == utc(2026, 9, 1, 6, 0)


def test_days_and_time_is_weekly():
    """RM-SCHEDULE-003 — tests/brd/reminders.feature"""
    schedule = resolve(days=["Mon", "Wed"], clock="08:30", now=NOW, tz=TZ)
    assert schedule.kind is ScheduleKind.WEEKLY
    assert schedule.weekdays == ("Mon", "Wed")
    assert schedule.at_time == time(8, 30)
    assert schedule.anchor_at is None


def test_all_seven_days_is_daily():
    """RM-SCHEDULE-003 — tests/brd/reminders.feature"""
    schedule = resolve(
        days=["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"], clock="08:30", now=NOW, tz=TZ
    )
    assert schedule.kind is ScheduleKind.DAILY
    assert schedule.weekdays[0] == "Mon"  # normalized into weekday order


def test_weekday_names_are_normalized_and_deduplicated():
    schedule = resolve(days=["monday", "MON", "friday"], clock="08:30", now=NOW, tz=TZ)
    assert schedule.weekdays == ("Mon", "Fri")


def test_date_and_time_is_once():
    """RM-SCHEDULE-003 — tests/brd/reminders.feature"""
    schedule = resolve(clock="09:00", day="20.08.2026", now=NOW, tz=TZ)
    assert schedule.kind is ScheduleKind.ONCE
    assert not schedule.repeating
    assert schedule.anchor_at == utc(2026, 8, 20, 6, 0)


def test_time_alone_is_once_at_its_next_occurrence():
    """RM-SCHEDULE-003 — tests/brd/reminders.feature"""
    schedule = resolve(clock="08:00", now=NOW, tz=TZ)
    assert schedule.kind is ScheduleKind.ONCE
    assert schedule.anchor_at == utc(2026, 8, 14, 5, 0)


@pytest.mark.parametrize(
    ("day", "clock", "moment"),
    [
        ("Thu", "15:00", utc(2026, 8, 13, 12, 0)),
        ("Thursday", "10:00", utc(2026, 8, 20, 7, 0)),
        ("Fri", "10:00", utc(2026, 8, 14, 7, 0)),
    ],
)
def test_a_weekday_as_the_date_is_the_next_one_still_ahead(day, clock, moment):
    """RM-SCHEDULE-003 — tests/brd/reminders.feature"""
    schedule = resolve(clock=clock, day=day, now=NOW, tz=TZ)
    assert schedule.kind is ScheduleKind.ONCE and schedule.anchor_at == moment


@pytest.mark.parametrize(
    ("day", "clock", "moment"),
    [
        ("today", "15:00", utc(2026, 8, 13, 12, 0)),
        ("tomorrow", "10:00", utc(2026, 8, 14, 7, 0)),
        ("Tomorrow", "15:00", utc(2026, 8, 14, 12, 0)),
    ],
)
def test_today_or_tomorrow_as_the_date_is_that_day(day, clock, moment):
    """RM-SCHEDULE-003 — tests/brd/reminders.feature"""
    schedule = resolve(clock=clock, day=day, now=NOW, tz=TZ)
    assert schedule.kind is ScheduleKind.ONCE and schedule.anchor_at == moment


def test_every_day_beside_an_interval_is_the_interval_alone():
    """RM-SCHEDULE-003 — tests/brd/reminders.feature"""
    every_day = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    schedule = resolve(days=every_day, interval_minutes=120, now=NOW, tz=TZ)
    assert schedule.kind is ScheduleKind.INTERVAL and schedule.weekdays == ()


def test_minutes_from_now_fire_once_at_that_minute():
    """RM-SCHEDULE-003 — tests/brd/reminders.feature"""
    schedule = resolve(in_minutes=90, now=NOW.replace(second=40), tz=TZ)
    assert schedule.kind is ScheduleKind.ONCE
    assert schedule.anchor_at == utc(2026, 8, 13, 10, 30)
    assert schedule.at_time == time(13, 30)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"day": "20.08.2026"}, "needs an hour"),
        ({}, "Nothing to schedule"),
        ({"days": ["Mon"]}, "need a time"),
        ({"days": ["Mon"], "clock": "08:30", "interval_minutes": 60}, "not both"),
        ({"interval_minutes": 1}, "at least"),
        ({"days": ["Funday"], "clock": "08:30"}, "Unknown weekday"),
        ({"clock": "25:00"}, "look like HH:MM"),
        ({"day": "2026-08-20", "clock": "09:00"}, "look like dd.mm.yyyy"),
        ({"in_minutes": 30, "clock": "08:00"}, "one moment from now"),
        ({"in_minutes": 30, "interval_minutes": 60}, "one moment from now"),
    ],
)
def test_unresolvable_configurations(kwargs, message):
    """RM-SCHEDULE-005 — tests/brd/reminders.feature"""
    with pytest.raises(ScheduleError, match=message):
        resolve(now=NOW, tz=TZ, **kwargs)


def test_a_one_shot_whose_moment_has_passed_is_refused():
    """RM-SCHEDULE-004 — tests/brd/reminders.feature"""
    with pytest.raises(ScheduleError, match="already passed"):
        resolve(clock="09:00", day="01.01.2020", now=NOW, tz=TZ)


def test_past_start_on_a_recurrence_is_allowed():
    """RM-SCHEDULE-004 — tests/brd/reminders.feature"""
    # "Already started" is not an error — only an unsatisfiable one-shot is.
    schedule = resolve(days=["Mon"], clock="08:30", day="01.01.2020", now=NOW, tz=TZ)
    assert schedule.anchor_at == utc(2020, 1, 1, 5, 30)


# --- first fire -----------------------------------------------------------


def test_first_fire_of_an_interval_is_its_anchor():
    schedule = resolve(interval_minutes=120, now=NOW, tz=TZ)
    assert next_fire(schedule, previous=None, now=NOW, tz=TZ) == NOW


def test_first_fire_of_a_weekly_is_the_next_matching_day():
    schedule = resolve(days=["Mon"], clock="08:30", now=NOW, tz=TZ)
    # Thursday 13 Aug -> Monday 17 Aug, 08:30 local = 05:30 UTC
    assert next_fire(schedule, previous=None, now=NOW, tz=TZ) == utc(2026, 8, 17, 5, 30)


def test_a_future_start_holds_a_weekly_back():
    """RM-SCHEDULE-003 — tests/brd/reminders.feature"""
    schedule = resolve(days=["Mon"], clock="08:30", day="01.09.2026", now=NOW, tz=TZ)
    # 1 Sep 2026 is a Tuesday, so the first Monday on or after it is 7 Sep.
    assert next_fire(schedule, previous=None, now=NOW, tz=TZ) == utc(2026, 9, 7, 5, 30)


def test_a_start_landing_exactly_on_a_matching_slot_is_included():
    schedule = resolve(days=["Tue"], clock="09:00", day="01.09.2026", now=NOW, tz=TZ)
    assert next_fire(schedule, previous=None, now=NOW, tz=TZ) == utc(2026, 9, 1, 6, 0)


def test_a_past_start_on_a_recurrence_does_not_backfill():
    """RM-SCHEDULE-004 — tests/brd/reminders.feature"""
    schedule = resolve(days=["Mon"], clock="08:30", day="01.01.2020", now=NOW, tz=TZ)
    assert next_fire(schedule, previous=None, now=NOW, tz=TZ) == utc(2026, 8, 17, 5, 30)


def test_a_one_shot_fires_once_and_then_never_again():
    schedule = resolve(clock="09:00", day="20.08.2026", now=NOW, tz=TZ)
    first = next_fire(schedule, previous=None, now=NOW, tz=TZ)
    assert first == utc(2026, 8, 20, 6, 0)
    assert next_fire(schedule, previous=first, now=NOW, tz=TZ) is None


# --- Don't disturb --------------------------------------------------------


def test_a_candidate_inside_a_quiet_window_moves_to_its_end():
    """RM-QUIET-007 — tests/brd/reminders.feature"""
    # The worked example from the spec: 21:00 + 2 h lands at 23:00, inside 22:00-09:00.
    schedule = quiet(resolve(interval_minutes=120, now=NOW, tz=TZ), "22:00-09:00")
    previous = utc(2026, 8, 13, 18, 0)  # 21:00 local
    assert next_fire(schedule, previous=previous, now=NOW, tz=TZ) == utc(2026, 8, 14, 6, 0)


def test_a_candidate_outside_every_window_is_untouched():
    """RM-QUIET-007 — tests/brd/reminders.feature"""
    schedule = quiet(resolve(interval_minutes=120, now=NOW, tz=TZ), "22:00-09:00")
    previous = utc(2026, 8, 13, 10, 0)  # 13:00 local
    assert next_fire(schedule, previous=previous, now=NOW, tz=TZ) == utc(2026, 8, 13, 12, 0)


def test_a_wrapping_window_and_its_split_form_are_the_same_window():
    """RM-QUIET-007 — tests/brd/reminders.feature"""
    # Split at midnight the window must be 22:00-00:00, not 22:00-23:59: an end is
    # exclusive, so 23:59 leaves the last minute of the day open and the candidate stops
    # inside it instead of carrying on to 09:00.
    wrapping = quiet(resolve(interval_minutes=120, now=NOW, tz=TZ), "22:00-09:00")
    split = quiet(resolve(interval_minutes=120, now=NOW, tz=TZ), "22:00-00:00", "00:00-09:00")
    previous = utc(2026, 8, 13, 18, 0)
    assert next_fire(wrapping, previous=previous, now=NOW, tz=TZ) == next_fire(
        split, previous=previous, now=NOW, tz=TZ
    )


def test_chained_windows_push_a_candidate_through_all_of_them():
    """RM-QUIET-007 — tests/brd/reminders.feature"""
    schedule = quiet(resolve(interval_minutes=60, now=NOW, tz=TZ), "13:00-14:00", "14:00-16:00")
    previous = utc(2026, 8, 13, 9, 30)  # 12:30 local -> 13:30, inside the first window
    landed = next_fire(schedule, previous=previous, now=NOW, tz=TZ)
    assert landed.astimezone(TZ).strftime("%H:%M") == "16:00"


def test_a_candidate_before_midnight_inside_a_wrapping_window_moves_to_the_next_morning():
    """RM-QUIET-007 — tests/brd/reminders.feature"""
    schedule = quiet(resolve(interval_minutes=60, now=NOW, tz=TZ), "22:00-09:00")
    previous = utc(2026, 8, 13, 21, 30)  # 00:30 local on the 14th, inside the window
    assert next_fire(schedule, previous=previous, now=NOW, tz=TZ) == utc(2026, 8, 14, 6, 0)


# --- daylight saving ------------------------------------------------------


def test_a_wall_clock_survives_a_daylight_saving_shift():
    """RM-CLOCK-006 — tests/brd/reminders.feature"""
    # Berlin goes UTC+2 -> UTC+1 on 25 Oct 2026, and 08:30 local must stay 08:30.
    schedule = resolve(
        days=["Mon", "Tue", "Wed", "Thu", "Fri"], clock="08:30", now=NOW, tz=BERLIN
    )
    before = next_fire(schedule, previous=utc(2026, 10, 22, 6, 30), now=NOW, tz=BERLIN)
    assert before == utc(2026, 10, 23, 6, 30)  # Friday, still UTC+2
    after = next_fire(schedule, previous=before, now=NOW, tz=BERLIN)
    assert after == utc(2026, 10, 26, 7, 30)  # Monday, now UTC+1
    assert after.astimezone(BERLIN).strftime("%H:%M") == "08:30"


def test_a_quiet_window_edge_survives_a_daylight_saving_shift():
    """RM-CLOCK-006 — tests/brd/reminders.feature"""
    schedule = quiet(resolve(interval_minutes=180, now=NOW, tz=BERLIN), "22:00-09:00")
    previous = utc(2026, 10, 24, 19, 0)  # 21:00 local, the evening before the shift
    landed = next_fire(schedule, previous=previous, now=NOW, tz=BERLIN)
    assert landed.astimezone(BERLIN).strftime("%H:%M") == "09:00"


# --- catch-up -------------------------------------------------------------


def test_roll_forward_skips_every_missed_interval_at_once():
    missed = utc(2026, 8, 10, 9, 0)  # three days of two-hour slots
    schedule = resolve(interval_minutes=120, now=missed, tz=TZ)
    landed = roll_forward(schedule, previous=missed, now=NOW, tz=TZ)
    assert landed == utc(2026, 8, 13, 11, 0)
    assert (landed - missed).total_seconds() % (120 * 60) == 0  # still on the anchor's grid


def test_roll_forward_skips_missed_weekly_occurrences():
    schedule = resolve(days=["Mon"], clock="08:30", now=NOW, tz=TZ)
    landed = roll_forward(schedule, previous=utc(2026, 7, 6, 5, 30), now=NOW, tz=TZ)
    assert landed == utc(2026, 8, 17, 5, 30)


def test_roll_forward_leaves_a_one_shot_where_it_is():
    overdue = utc(2026, 8, 12, 6, 0)
    schedule = resolve(clock="09:00", day="12.08.2026", now=overdue - timedelta(hours=1), tz=TZ)
    assert roll_forward(schedule, previous=overdue, now=NOW, tz=TZ) == overdue


def test_roll_forward_respects_quiet_windows():
    missed = utc(2026, 8, 1, 3, 0)
    schedule = quiet(resolve(interval_minutes=120, now=missed, tz=TZ), "22:00-09:00")
    landed = roll_forward(schedule, previous=missed, now=NOW, tz=TZ)
    local = landed.astimezone(TZ).time()
    assert local == time(14, 0)


# --- persistence round trip -----------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"interval_minutes": 120},
        {"days": ["Mon", "Wed"], "clock": "08:30"},
        {"clock": "09:00", "day": "20.08.2026"},
    ],
)
def test_a_schedule_survives_the_column_round_trip(kwargs):
    """RM-WRITE-008 — tests/brd/reminders.feature"""
    schedule = resolve(now=NOW, tz=TZ, **kwargs)
    assert schedule_of(FakeRow(**schedule_columns(schedule))) == schedule
    assert schedule_from_payload(json.loads(json.dumps(schedule_payload(schedule)))) == schedule


# --- describing -----------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"interval_minutes": 120}, "every 2 hours"),
        ({"interval_minutes": 60}, "every hour"),
        ({"interval_minutes": 90}, "every 1 h 30 min"),
        ({"interval_minutes": 30}, "every 30 minutes"),
        (
            {"days": ["Mon", "Tue", "Wed", "Thu", "Fri"], "clock": "08:30"},
            "every weekday at 08:30",
        ),
        ({"days": ["Mon", "Wed"], "clock": "08:30"}, "every Mon, Wed at 08:30"),
        (
            {"days": ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"], "clock": "08:30"},
            "every day at 08:30",
        ),
        ({"clock": "09:00", "day": "20.08.2026"}, "once, on Thu 20.08.2026 at 09:00"),
    ],
)
def test_describe(kwargs, expected):
    schedule = resolve(now=NOW, tz=TZ, **kwargs)
    assert describe(schedule, tz=TZ, now=NOW) == expected


def test_describe_names_a_start_only_while_it_is_still_ahead():
    ahead = resolve(days=["Mon"], clock="08:30", day="01.09.2026", now=NOW, tz=TZ)
    assert describe(ahead, tz=TZ, now=NOW) == "every Mon at 08:30, from 01.09.2026 08:30"
    behind = resolve(days=["Mon"], clock="08:30", day="01.01.2020", now=NOW, tz=TZ)
    assert describe(behind, tz=TZ, now=NOW) == "every Mon at 08:30"


def test_describe_tolerates_an_empty_schedule():
    assert describe(Schedule(kind=ScheduleKind.ONCE), tz=TZ, now=NOW) == "once"
