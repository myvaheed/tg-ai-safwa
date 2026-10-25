"""Remind's lead: how long before each moment its Reminder fires, and the `remind` tool."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError
from schedule_helpers import rule_for
from sqlalchemy import func, select

from safwa.bootstrap.modules import PROPOSALS
from safwa.constants import WEEKDAY_NAMES
from safwa.features.cards.telegram.review import CardProposalPresenter
from safwa.features.cards.use_cases import create_card
from safwa.features.checks.telegram.review import CheckProposalPresenter
from safwa.features.checks.use_cases import create_check
from safwa.features.reminders.api import (
    REMINDER_CATCHUP_GRACE_MINUTES,
    Schedule,
    describe,
    next_fire,
    on_wall_clock,
    resolve,
    roll_forward,
)
from safwa.features.reminders.firing import Firing, format_cue, tick
from safwa.features.reminders.model import Reminder, ScheduleKind
from safwa.features.reminders.use_cases import create_reminder
from safwa.features.schedules.api import REMIND_NEEDS_TIME, entity_reminder
from tg_agent_shell.cues.model import Cue
from tg_agent_shell.proposals.api import ApplyContext, Effect, ProposalChange, ToolPreparationError
from tg_agent_shell.proposals.marks import field_line
from tg_agent_shell.proposals.prepare import ChangePreparer

TZ = ZoneInfo("Europe/Istanbul")
BERLIN = ZoneInfo("Europe/Berlin")


def _local(moment: datetime, tz: ZoneInfo = TZ) -> datetime:
    return moment.astimezone(tz).replace(tzinfo=None)


def test_a_lead_fires_before_each_moment_across_midnight() -> None:
    """SCH-REMIND-028 — tests/brd/schedules.feature"""
    now = datetime(2026, 10, 18, 12, tzinfo=TZ)
    lead = replace(resolve(days=list(WEEKDAY_NAMES), clock="00:15", now=now, tz=TZ), lead_minutes=30)
    first = next_fire(lead, previous=None, now=now, tz=TZ)
    assert _local(first) == datetime(2026, 10, 18, 23, 45)
    assert _local(roll_forward(lead, previous=first, now=first, tz=TZ)) == datetime(2026, 10, 19, 23, 45)
    assert on_wall_clock(lead, first, TZ)
    assert describe(lead, tz=TZ) == "every day at 00:15, 30 minutes before"


def test_a_lead_before_the_last_day_of_each_month() -> None:
    """SCH-REMIND-028 — tests/brd/schedules.feature"""
    now = datetime(2026, 12, 2, 12, tzinfo=TZ)
    lead = replace(resolve(month_days=[31], clock="18:00", now=now, tz=TZ), lead_minutes=60)
    fires = [next_fire(lead, previous=None, now=now, tz=TZ)]
    for _ in range(2):
        fires.append(roll_forward(lead, previous=fires[-1], now=fires[-1], tz=TZ))
    assert [_local(fire) for fire in fires] == [
        datetime(2026, 12, 31, 17),
        datetime(2027, 1, 31, 17),
        datetime(2027, 2, 28, 17),
    ]


def test_a_lead_counts_the_minutes_as_the_clock_runs_across_daylight_saving() -> None:
    """SCH-REMIND-028 — tests/brd/schedules.feature"""
    now = datetime(2026, 3, 28, 12, tzinfo=BERLIN)
    lead = replace(resolve(days=list(WEEKDAY_NAMES), clock="03:30", now=now, tz=BERLIN), lead_minutes=60)
    first = next_fire(lead, previous=None, now=now, tz=BERLIN)
    # 03:30 summer time on 29 March is 01:30 UTC; an hour before it the clock still read 01:30.
    assert first == datetime(2026, 3, 29, 0, 30, tzinfo=UTC)
    assert _local(first, BERLIN) == datetime(2026, 3, 29, 1, 30)
    assert on_wall_clock(lead, first, BERLIN)
    second = roll_forward(lead, previous=first, now=first, tz=BERLIN)
    assert _local(second, BERLIN) == datetime(2026, 3, 30, 2, 30)


def test_a_lead_on_an_interval_fires_before_each_of_its_moments() -> None:
    """SCH-REMIND-028 — tests/brd/schedules.feature"""
    start = datetime(2026, 10, 18, 9, tzinfo=UTC)
    interval = Schedule(
        kind=ScheduleKind.INTERVAL, interval_minutes=120, anchor_at=start, lead_minutes=30
    )
    first = next_fire(interval, previous=None, now=start - timedelta(hours=1), tz=TZ)
    assert first == start - timedelta(minutes=30)
    assert roll_forward(interval, previous=first, now=first, tz=TZ) == start + timedelta(minutes=90)


async def test_a_lead_already_passed_fires_at_once_while_the_moment_is_ahead(sessions) -> None:
    """SCH-REMIND-028 — tests/brd/schedules.feature"""
    now = datetime.now(UTC)
    moment = (now + timedelta(minutes=10)).replace(second=0, microsecond=0)
    clock = moment.astimezone(TZ).time()
    daily = Schedule(kind=ScheduleKind.DAILY, weekdays=WEEKDAY_NAMES, at_time=clock)
    once = Schedule(kind=ScheduleKind.ONCE, at_time=clock, anchor_at=moment, lead_minutes=60)
    past = REMINDER_CATCHUP_GRACE_MINUTES + 60
    async with sessions() as session:
        for words, schedule in (
            ("Call", once),
            ("Stretch", replace(daily, lead_minutes=60)),
            ("Walk", replace(daily, lead_minutes=past)),
        ):
            await create_reminder(session, instruction=words, schedule=schedule, tz=TZ)
        await session.commit()

    assert await tick(sessions, tz=TZ, now=now) is True
    async with sessions() as session:
        [cue] = list(await session.scalars(select(Cue)))
        assert "Text: Call" in cue.text and "Text: Stretch" in cue.text
        assert "Walk" not in cue.text and "Was due" not in cue.text
        walk = await session.scalar(select(Reminder).where(Reminder.instruction == "Walk"))
        assert walk.next_fire_at == moment + timedelta(days=1) - timedelta(minutes=past)


def test_its_words_say_how_late_only_once_the_moment_has_passed() -> None:
    """SCH-REMIND-028 — tests/brd/schedules.feature"""
    now = datetime(2026, 10, 18, 12, tzinfo=UTC)

    def cue(late: int, lead: int) -> str:
        due = now - timedelta(minutes=late)
        return format_cue(
            [Firing(reminder_id=1, instruction="x", schedule="s", due_at=due, lead_minutes=lead)],
            now=now,
        )

    assert "Was due" not in cue(50, 60)
    assert "Was due 30 minutes ago." in cue(45, 15)


async def test_safwa_reads_the_lead_in_ai_reminders(read_views) -> None:
    """RM-READ-024 — tests/brd/reminders.feature"""
    sessions, runner = read_views
    moment = datetime.now(UTC) + timedelta(days=1)
    once = Schedule(
        kind=ScheduleKind.ONCE, at_time=moment.astimezone(TZ).time(), anchor_at=moment, lead_minutes=45
    )
    async with sessions() as session:
        await create_reminder(session, instruction="Call", schedule=once, tz=TZ)
        await session.commit()
    (row,) = (await runner.run("SELECT lead_minutes FROM ai_reminders")).rows
    assert row["lead_minutes"] == 45


@pytest.mark.parametrize(
    ("item", "entity", "schedule"),
    [
        ("action", "card", "daily 20:00"),
        ("goal", "card", "20.10.2099 18:00"),
        ("check", "check", "daily 20:00"),
    ],
)
async def test_safwa_turns_remind_on_with_the_remind_tool(sessions, item, entity, schedule) -> None:
    """SCH-REMIND-029 — tests/brd/schedules.feature"""
    async with sessions() as session:
        make = create_card if entity == "card" else create_check
        kind = {"kind": item} if entity == "card" else {}
        target = await make(
            session,
            **kind,
            title="Stretch",
            schedule=schedule,
            schedule_rule=await rule_for(session, schedule),
        )
        change = PROPOSALS.change_from_tool(
            "remind", {"item": item, "id": target.id, "minutes_before": 60}
        )
        assert (change.entity, change.values) == (entity, {"remind": 60})
        prepared = await ChangePreparer(None, None, PROPOSALS).prepare(session, change)
        proposal = ProposalChange(
            entity=entity,
            action=change.action,
            entity_id=target.id,
            expected_version=prepared.expected_version,
            values=prepared.values,
        )
        presenter = CardProposalPresenter() if entity == "card" else CheckProposalPresenter()
        screen = await presenter.screen(session, [proposal])
        assert any("1 hour before" in (field_line(shown, Effect.UPDATE) or "") for shown in screen.fields)
        await PROPOSALS.handler(entity).apply(ApplyContext(session, frozenset()), proposal)
        reminder = await entity_reminder(session, target)
        assert reminder.lead_minutes == 60
        assert reminder.next_fire_at == target.period_start - timedelta(hours=1)


@pytest.mark.parametrize("tool", ["goal", "action", "check"])
def test_an_items_own_tool_takes_remind_only_when_it_creates_the_item(tool) -> None:
    """SCH-REMIND-029 — tests/brd/schedules.feature"""
    change = PROPOSALS.change_from_tool(tool, {"mode": "create", "title": "Stretch", "remind": 15})
    assert change.values["remind"] == 15
    with pytest.raises(ValidationError, match="update does not accept: remind"):
        PROPOSALS.tools[tool].input_model.model_validate({"mode": "update", "id": 3, "remind": 15})


async def test_remind_of_0_is_at_the_time_and_null_turns_it_off(sessions) -> None:
    """SCH-REMIND-029 — tests/brd/schedules.feature"""
    async with sessions() as session:
        card = await create_card(
            session,
            kind="action",
            title="Stretch",
            schedule="daily 20:00",
            schedule_rule=await rule_for(session, "daily 20:00"),
        )
        for lead, kept in ((0, 0), (None, None)):
            change = PROPOSALS.change_from_tool(
                "remind", {"item": "action", "id": card.id, "minutes_before": lead}
            )
            prepared = await ChangePreparer(None, None, PROPOSALS).prepare(session, change)
            assert prepared.values["remind"] == lead
            await PROPOSALS.handler("card").apply(
                ApplyContext(session, frozenset()),
                ProposalChange(
                    entity="card",
                    action=change.action,
                    entity_id=card.id,
                    expected_version=prepared.expected_version,
                    values=prepared.values,
                ),
            )
            reminder = await entity_reminder(session, card)
            assert (reminder.lead_minutes if reminder else None) == kept
        assert reminder is None


async def test_remind_without_a_clock_still_ahead_goes_back_to_the_model(sessions) -> None:
    """SCH-REMIND-029 — tests/brd/schedules.feature"""
    async with sessions() as session:
        quota = await create_card(
            session,
            kind="action",
            title="Run",
            schedule="three times a week",
            schedule_rule={"kind": "quota", "period": "week", "count": 3},
        )
        change = PROPOSALS.change_from_tool(
            "remind", {"item": "action", "id": quota.id, "minutes_before": 15}
        )
        with pytest.raises(ToolPreparationError) as refusal:
            await ChangePreparer(None, None, PROPOSALS).prepare(session, change)
        result = refusal.value.as_tool_result()
        assert (result["code"], result["error"]) == ("remind_needs_time", REMIND_NEEDS_TIME)
        assert await session.scalar(select(func.count(Reminder.id))) == 0
