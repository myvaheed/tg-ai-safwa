"""How the configured model reads timing words, each phrase the whole way through the code.

Opt-in with ``--live-provider``; it reads SAFWA_QA_AI_* over SAFWA_AI_* from `.env`, so run
it from the folder that holds `.env`. Reasoning is always off, as Safwa is meant to run: a step
the owner waits 5-10 seconds for is no use. A phrase is one Scheduler session for a Schedule, or the setup session of
a Reminder, and a second one for the SELECT of a condition. What is compared is what the code
made of the model's answer, never the answer itself.

Each case names the batch that makes its form readable: `now` for forms the code holds today,
`1a` for a last day, a total on a Reminder and the start of a quota, `1b` for days of the
month. The passes are the measure: `-k now` before and after a change says whether the new
fields cost the old forms anything, and `-k 1a` says whether the new ones are read.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from llm_gateway import OpenAICompatibleProvider
from safwa.constants import WEEKDAY_NAMES
from safwa.features.reminders.agent import resolve_schedule
from safwa.features.reminders.api import Schedule, ScheduleError, schedule_from_payload
from safwa.features.schedules.agent import ScheduleCompiler
from safwa.qa import qa_provider_settings

pytestmark = pytest.mark.live_provider

TZ = ZoneInfo("Europe/Istanbul")
# A Saturday at noon: next Monday is 19.10, next Friday 23.10, next month starts 01.11.
NOW = datetime(2026, 10, 17, 12, 0, tzinfo=TZ)
NAMES = "Tags: Sport, Reading\nValues: Health"
REASONING_EFFORT = "none"
EVERY_DAY = list(WEEKDAY_NAMES)
WORKWEEK = ["Mon", "Tue", "Wed", "Thu", "Fri"]


@dataclass(frozen=True)
class Case:
    batch: str
    target: str
    phrase: str
    expected: dict[str, Any]


CASES = (
    Case("now", "reminder", "каждый день в 8", {"kind": "daily", "days": EVERY_DAY, "time": "08:00"}),
    Case("now", "reminder", "every weekday at 8am", {"kind": "weekly", "days": WORKWEEK, "time": "08:00"}),
    Case("now", "reminder", "по понедельникам в 9:30", {"days": ["Mon"], "time": "09:30"}),
    Case("now", "reminder", "завтра в 15:00", {"kind": "once", "date": "18.10.2026", "time": "15:00"}),
    Case("now", "reminder", "in 90 minutes", {"kind": "once", "date": "17.10.2026", "time": "13:30"}),
    Case(
        "now", "reminder", "каждые 2 часа, кроме 22:00–09:00",
        {"interval": 120, "quiet": ["22:00-09:00"]},
    ),
    Case("now", "reminder", "в четверг в 10", {"kind": "once", "date": "22.10.2026", "time": "10:00"}),
    Case("now", "reminder", "по четвергам в 10", {"kind": "weekly", "days": ["Thu"], "time": "10:00"}),
    Case(
        "now", "reminder", "every 3 Missed in a row of Check #12",
        {"kind": "condition", "answer": "missed", "count": 3, "in_row": True, "filters": False},
    ),
    Case(
        "now", "reminder", "каждые 10 Done у Actions с тегом sport",
        {"kind": "condition", "answer": "done", "count": 10, "in_row": False, "filters": False},
    ),
    Case("now", "reminder", "каждое утро", {"unclear": "time"}),
    Case("now", "schedule", "5 раз в день", {"form": "quota", "period": "day", "count": 5}),
    Case("now", "schedule", "once a week", {"form": "quota", "period": "week", "count": 1}),
    Case("now", "schedule", "каждый вечер в 20:00", {"form": "fixed", "days": EVERY_DAY, "time": "20:00"}),
    Case("now", "schedule", "по понедельникам и средам в 9", {"days": ["Mon", "Wed"], "time": "09:00"}),
    Case("now", "schedule", "every Monday", {"form": "fixed", "days": ["Mon"], "time": None}),
    Case("now", "schedule", "20 октября в 15:00", {"kind": "once", "date": "20.10.2026", "time": "15:00"}),
    Case("now", "schedule", "после того как закончу", {"form": "after_completion"}),
    Case(
        "now", "schedule", "5 раз в день, всего 50 раз",
        {"form": "quota", "period": "day", "count": 5, "limit": 50},
    ),
    Case("now", "schedule", "every Monday at 9, 10 times", {"days": ["Mon"], "time": "09:00", "limit": 10}),
    Case("now", "schedule", "каждые 2 часа", {"form": "fixed", "interval": 120}),
    Case(
        "now", "schedule", "every evening at 20:00 from 1 November, 20 times",
        {"days": EVERY_DAY, "time": "20:00", "date": "01.11.2026", "limit": 20},
    ),
    Case("now", "reminder", "через 2 часа", {"kind": "once", "date": "17.10.2026", "time": "14:00"}),
    Case("now", "schedule", "в четверг в 10", {"kind": "once", "date": "22.10.2026", "time": "10:00"}),
    Case("now", "schedule", "через 30 минут", {"kind": "once", "date": "17.10.2026", "time": "12:30"}),
    Case(
        "1a", "reminder", "со следующего месяца по утрам в 10:00 в течение года",
        {"days": EVERY_DAY, "time": "10:00", "date": "01.11.2026", "until": "31.10.2027"},
    ),
    Case(
        "1a", "reminder", "каждый день в 9 до 30 ноября",
        {"days": EVERY_DAY, "time": "09:00", "until": "30.11.2026"},
    ),
    Case("1a", "reminder", "по будням в 8, 10 раз", {"days": WORKWEEK, "time": "08:00", "limit": 10}),
    Case(
        "1a", "reminder", "every day at 7 for 2 weeks",
        {"days": EVERY_DAY, "time": "07:00", "until": "30.10.2026"},
    ),
    # The trap: Friday is the last day, never the start.
    Case(
        "1a", "reminder", "каждые 3 часа до пятницы",
        {"interval": 180, "date": "17.10.2026", "until": "23.10.2026"},
    ),
    Case(
        "1a", "reminder", "every Monday at 10 from 2 November until the end of the year",
        {"days": ["Mon"], "time": "10:00", "date": "02.11.2026", "until": "31.12.2026"},
    ),
    Case(
        "1a", "reminder", "каждый день в 9 с 1 по 30 ноября",
        {"days": EVERY_DAY, "time": "09:00", "date": "01.11.2026", "until": "30.11.2026"},
    ),
    # The trap: named weekdays win over a count per week.
    Case(
        "1a", "schedule", "со следующей недели 2 раза в неделю в пн и чт в течение месяца",
        {"form": "fixed", "days": ["Mon", "Thu"], "time": None, "date": "19.10.2026",
         "until": "18.11.2026"},
    ),
    Case(
        "1a", "schedule", "twice a week on Tuesday and Friday at 7",
        {"form": "fixed", "days": ["Tue", "Fri"], "time": "07:00"},
    ),
    Case(
        "1a", "schedule", "3 раза в неделю с понедельника в течение месяца",
        {"form": "quota", "period": "week", "count": 3, "date": "19.10.2026", "until": "18.11.2026"},
    ),
    Case(
        "1a", "schedule", "2 раза в день в течение недели",
        {"form": "quota", "period": "day", "count": 2, "until": "23.10.2026"},
    ),
    Case(
        "1a", "schedule", "каждый понедельник до конца ноября",
        {"form": "fixed", "days": ["Mon"], "time": None, "until": "30.11.2026"},
    ),
    Case(
        "1a", "schedule", "каждый день в 21:00 в течение 30 дней",
        {"days": EVERY_DAY, "time": "21:00", "until": "15.11.2026"},
    ),
    # The trap: how long comes before the timing.
    Case(
        "1a", "reminder", "в течение двух недель каждый вечер в 21:00",
        {"days": EVERY_DAY, "time": "21:00", "until": "30.10.2026"},
    ),
    Case(
        "1a", "schedule", "по средам до 16 декабря",
        {"form": "fixed", "days": ["Wed"], "time": None, "until": "16.12.2026"},
    ),
    Case("1b", "reminder", "1 и 15 числа каждого месяца в 9", {"month_days": [1, 15], "time": "09:00"}),
    Case("1b", "reminder", "в последний день месяца в 18:00", {"month_days": [31], "time": "18:00"}),
    Case(
        "1b", "reminder", "15 числа каждого месяца в 12:00 по 30 апреля",
        {"month_days": [15], "time": "12:00", "until": "30.04.2027"},
    ),
    Case("1b", "schedule", "раз в месяц", {"form": "quota", "period": "month", "count": 1}),
    Case("1b", "schedule", "10 раз в месяц", {"form": "quota", "period": "month", "count": 10}),
    Case("1b", "schedule", "1 числа каждого месяца", {"form": "fixed", "month_days": [1], "time": None}),
)


def _timing(schedule: Schedule, *, all_day: bool = False) -> dict[str, Any]:
    reading: dict[str, Any] = {
        "kind": schedule.kind.value,
        "days": list(schedule.weekdays) or None,
        "time": None if all_day or schedule.at_time is None else f"{schedule.at_time:%H:%M}",
        "date": f"{schedule.anchor_at.astimezone(TZ):%d.%m.%Y}" if schedule.anchor_at else None,
        "interval": schedule.interval_minutes,
        "quiet": [f"{start:%H:%M}-{end:%H:%M}" for start, end in schedule.quiet_windows] or None,
        "until": f"{schedule.until:%d.%m.%Y}" if schedule.until else None,
        "limit": schedule.total,
    }
    if schedule.condition is not None:
        condition = schedule.condition
        # The SELECT finds the items; the answers are counted in code, never filtered there.
        filters = any(word in condition.sql.lower() for word in ("status", "stage"))
        reading.update(
            answer=condition.answer, count=condition.count, in_row=condition.in_row, filters=filters
        )
    return reading


async def _reminder(provider, phrase: str) -> dict[str, Any]:
    try:
        schedule = await resolve_schedule(
            provider, when=phrase, instruction="Remind me.", now=NOW, tz=TZ, names=NAMES
        )
    except ScheduleError as error:
        words = str(error)
        about = "time" if "On answers:" not in words else "answers" if "By time:" not in words else "either"
        return {"unclear": about, "reason": words.splitlines()[0]}
    return _timing(schedule)


async def _schedule(provider, phrase: str) -> dict[str, Any]:
    rule, question = await ScheduleCompiler(provider).compile(phrase, NOW, TZ, "check")
    if question:
        return {"question": question}
    reading: dict[str, Any] = {}
    if rule["kind"] == "fixed":
        reading.update(_timing(schedule_from_payload(rule["timing"]), all_day=rule.get("all_day", False)))
    if rule["kind"] == "quota":
        reading.update(period=rule["period"], count=rule["count"], date=_day(rule.get("start")))
    # The total and the last day live on the rule, never in its timing.
    reading.update(form=rule["kind"], limit=rule.get("limit"), until=_day(rule.get("until")))
    return reading


def _day(iso: str | None) -> str | None:
    return f"{date.fromisoformat(iso):%d.%m.%Y}" if iso else None


@pytest.fixture
async def provider():
    provider = OpenAICompatibleProvider(
        replace(qa_provider_settings().ai_config(), reasoning_effort=REASONING_EFFORT)
    )
    try:
        yield provider
    finally:
        await provider.aclose()


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(case, id=f"{case.batch}-{case.target}-{number:02d}")
        for number, case in enumerate(CASES, start=1)
    ],
)
async def test_the_model_reads_the_timing_phrase(provider, case):
    read = _reminder if case.target == "reminder" else _schedule
    reading = await read(provider, case.phrase)
    assert {key: reading.get(key) for key in case.expected} == case.expected, (
        f"{case.phrase!r} was read as {reading}"
    )
