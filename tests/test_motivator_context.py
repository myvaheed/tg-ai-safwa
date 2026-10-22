"""Value-specific motivation, scheduled focus and independent request groups."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from schedule_helpers import create_check as scheduled_check
from telegram_fakes import spawn_timer
from test_home import Words

from llm_gateway import CompletionTurn, ToolCall
from safwa.features.cards.use_cases import create_card, finish_action
from safwa.features.checks.use_cases import create_check, resolve_check, toggle_check_value
from safwa.features.home import motivation
from safwa.features.home.dashboard import dashboard_text
from safwa.features.home.motivation import (
    MOTIVATION_BATCH_SIZE,
    MOTIVATION_CONCURRENCY,
    MOTIVATION_FRESH_MINUTES,
    MOTIVATION_PROMPT,
    Motivator,
)
from safwa.features.planning.api import values_in_focus
from safwa.features.profile.model import ProfileField
from safwa.features.profile.use_cases import set_profile_field
from safwa.features.schedules import api as schedules
from safwa.features.values.use_cases import create_value


@pytest.mark.parametrize("clock", ["10:00", "21:30"])
async def test_scheduled_checks_put_only_todays_remaining_values_in_focus(sessions, monkeypatch, clock):
    """VL-FOCUS-001 — tests/brd/values.feature"""
    now = datetime.fromisoformat(f"2026-10-21T{clock}:00+00:00")
    monkeypatch.setattr(schedules, "utcnow", lambda: now)
    monkeypatch.setattr("schedule_helpers.utcnow", lambda: now)
    today = now.astimezone(ZoneInfo("Europe/Istanbul")).date()
    tomorrow = today + timedelta(days=1)
    async with sessions() as session:
        health = await create_value(session, "Health", "Look after my body")
        due = await scheduled_check(session, title="Slept enough?", schedule=f"{today:%d.%m.%Y} 21:00")
        daily = await create_check(session, title="Posture?", schedule="three a day", schedule_rule={
            "kind": "quota", "period": "day", "count": 3,
        })
        daily.period_start = schedules.period_start(now, "day", await schedules.workspace_zone(session))
        await toggle_check_value(session, due.id, health.id)
        await toggle_check_value(session, daily.id, health.id)
        excluded = [
            await create_check(session, title="Plain?"),
            await scheduled_check(session, title="Tomorrow?", schedule=f"{tomorrow:%d.%m.%Y} 21:00"),
            await create_check(session, title="Weekly?", schedule="three a week", schedule_rule={
                "kind": "quota", "period": "week", "count": 3,
            }),
            await create_check(session, title="Monthly?", schedule="three a month", schedule_rule={
                "kind": "quota", "period": "month", "count": 3,
            }),
            await scheduled_check(session, title="After?", schedule="after completion"),
            await scheduled_check(session, title="Archived?", schedule=f"{today:%d.%m.%Y} 22:00"),
            await scheduled_check(session, title="Answered?", schedule=f"{today:%d.%m.%Y} 22:00"),
            await create_check(session, title="Starts tomorrow?", schedule="daily", schedule_rule={
                "kind": "quota", "period": "day", "count": 1, "start": tomorrow.isoformat(),
            }),
            await create_check(session, title="Ended?", schedule="daily", schedule_rule={
                "kind": "quota", "period": "day", "count": 1,
                "until": (today - timedelta(days=1)).isoformat(),
            }),
        ]
        for index, check in enumerate(excluded):
            value = await create_value(session, f"Excluded {index}", "Not due today")
            await toggle_check_value(session, check.id, value.id)
        excluded[5].archived_at = now
        excluded[6].outcome = "passed"
        await session.commit()
        focus = await values_in_focus(session)
        assert [(v.name, {c.title for c in held}) for v, held in focus] == [
            ("Health", {"Slept enough?", "Posture?"}),
        ]
        text = await dashboard_text(session, {})
        assert "Health" in text and "Excluded" not in text.split("Values in focus")[1]
        assert {c.id: n for c, n in await schedules.checks_today(session)} == {due.id: 1, daily.id: 3}


async def test_daily_checks_leave_focus_after_their_last_answer(sessions, monkeypatch):
    """VL-FOCUS-001 — tests/brd/values.feature"""
    now = datetime(2026, 10, 21, 10, tzinfo=UTC)
    monkeypatch.setattr(schedules, "utcnow", lambda: now)
    monkeypatch.setattr("safwa.features.checks.use_cases.utcnow", lambda: now)
    async with sessions() as session:
        value = await create_value(session, "Health", "Body first")
        check = await create_check(session, title="Posture?", schedule="twice a day", schedule_rule={
            "kind": "quota", "period": "day", "count": 2,
        })
        await toggle_check_value(session, check.id, value.id)
        _, next_check = await resolve_check(session, check.id, "passed")
        await session.flush()
        assert [(c.id, n) for c, n in await schedules.checks_today(session)] == [(next_check.id, 1)]
        await resolve_check(session, next_check.id, "missed")
        await session.flush()
        assert await schedules.checks_today(session) == []
        assert await values_in_focus(session) == []


async def test_motivator_reads_values_from_every_parent_and_custom_instructions_last(sessions, monkeypatch):
    """HM-VALUES-007 — tests/brd/home.feature"""
    now = datetime(2026, 10, 21, 10, tzinfo=UTC)
    monkeypatch.setattr(schedules, "utcnow", lambda: now)
    monkeypatch.setattr(motivation, "utcnow", lambda: now)
    monkeypatch.setattr("schedule_helpers.utcnow", lambda: now)
    async with sessions() as session:
        health = await create_value(session, "Health", "Body first")
        family = await create_value(session, "Family", "Time together")
        goal = await create_card(session, kind="goal", title="Healthy year", value_ids={health.id})
        other = await create_card(session, kind="goal", title="Family time", value_ids={family.id})
        sub = await create_card(session, kind="goal", title="Weekend", parent_ids={goal.id, other.id})
        await create_card(session, kind="action", title="Walk together", stage="today", parent_ids={sub.id})
        done = await create_card(session, kind="action", title="Previous walk", parent_ids={sub.id})
        await finish_action(session, done.id)
        check = await scheduled_check(session, title="Slept enough?", schedule="21.10.2026 21:00")
        await toggle_check_value(session, check.id, health.id)
        custom = "Be enthusiastic. Remind me of my marathon."
        await set_profile_field(session, ProfileField.MOTIVATOR_INSTRUCTIONS, custom)
        await session.commit()
    model = Words({"Health": "Keep moving.", "Family": "Walk together."})
    await Motivator(model, spawn=spawn_timer).write(sessions)
    [request] = model.requests
    assert request.messages[0]["content"] == MOTIVATION_PROMPT + "\n\nMotivator instructions:\n" + custom
    context = request.messages[1]["content"]
    assert "Walk together (kind=action, priority=medium, values=Health, Family)" in context
    assert "Previous walk (values=Health, Family)" in context
    assert "Slept enough? (schedule=21.10.2026 21:00, remaining_today=1, values=Health)" in context
    listed = context.split("Values, numbered:\n")[1].splitlines()
    assert all("Action: Walk together" in line for line in listed)
    assert "Check: Slept enough?" in listed[0] and "Check:" not in listed[1]


class GroupedWords(Words):
    def __init__(self, said):
        super().__init__(said)
        self.active = 0
        self.peak = 0
        self.full = asyncio.Event()
        self.release = asyncio.Event()
        self.failed_name = None

    async def complete(self, request):
        self.active += 1
        self.peak = max(self.peak, self.active)
        if self.active == MOTIVATION_CONCURRENCY:
            self.full.set()
        try:
            await self.release.wait()
            if self.failed_name and f"1. {self.failed_name} —" in request.messages[1]["content"]:
                raise RuntimeError("One group failed")
            return await super().complete(request)
        finally:
            self.active -= 1


async def test_motivator_groups_values_and_keeps_success_when_another_group_fails(sessions, monkeypatch):
    """HM-VALUES-007 — tests/brd/home.feature"""
    assert MOTIVATION_BATCH_SIZE == 3 and MOTIVATION_CONCURRENCY == 2
    now = datetime(2026, 10, 21, 10, tzinfo=UTC)
    monkeypatch.setattr(motivation, "utcnow", lambda: now)
    async with sessions() as session:
        values = [await create_value(session, f"Value {i}", "What matters") for i in range(10)]
        await create_card(session, kind="action", title="Work", stage="today", value_ids={v.id for v in values})
        await session.commit()
    model = GroupedWords({v.name: f"First {v.name}." for v in values})
    motivator = Motivator(model, spawn=spawn_timer)
    task = asyncio.create_task(motivator.write(sessions))
    await asyncio.wait_for(model.full.wait(), timeout=5)
    assert model.active == MOTIVATION_CONCURRENCY
    model.release.set()
    result = await asyncio.wait_for(task, timeout=5)
    assert list(result) == [v.id for v in values]
    assert model.peak == MOTIVATION_CONCURRENCY
    assert [len(r.messages[1]["content"].split("Values, numbered:\n")[1].splitlines()) for r in model.requests] == [3, 3, 3, 1]
    for request in model.requests:
        assert request.reasoning_effort == "none"
    now += timedelta(minutes=MOTIVATION_FRESH_MINUTES)
    model.failed_name = "Value 3"
    model.said = {v.name: f"Second {v.name}." for v in values}
    result = await motivator.write(sessions)
    assert set(result) == {v.id for v in values if v.name not in {"Value 3", "Value 4", "Value 5"}}
    assert all(said.startswith("Second") for said in result.values())
    now += timedelta(minutes=MOTIVATION_FRESH_MINUTES)
    model.failed_name = None
    await motivator.write(sessions)
    group = next(r for r in model.requests if "- Value 1: First Value 3." in r.messages[1]["content"])
    previous = group.messages[1]["content"].split("Previous motivation:\n")[1].split("\n\n")[0]
    assert "First Value 3." in previous and "Value 0." not in previous


class WrongValue:
    async def complete(self, request):
        return CompletionTurn(content=None, tool_calls=(ToolCall("m", "motivate", json.dumps({
            "words": [{"value": 99, "text": "Not in this group."}],
        })),))


async def test_motivator_drops_an_answer_for_a_value_outside_the_group(sessions):
    """HM-VALUES-007 — tests/brd/home.feature"""
    async with sessions() as session:
        value = await create_value(session, "Health", "Body first")
        await create_card(session, kind="action", title="Walk", stage="today", value_ids={value.id})
        await session.commit()
    assert await Motivator(WrongValue(), spawn=spawn_timer).write(sessions) == {}
