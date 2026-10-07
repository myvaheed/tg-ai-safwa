from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from safwa.features.cards.use_cases import create_card, finish_card
from safwa.features.workspace_mutator import state
from safwa.features.workspace_mutator.api import PRIORITY_GOAL_DEADLINE_DAYS


@pytest.fixture
def goal_clock(monkeypatch):
    now = datetime(2026, 10, 3, 0, 30, tzinfo=ZoneInfo("Europe/Istanbul"))
    monkeypatch.setattr(state, "utcnow", lambda: now.astimezone(UTC))
    return now


async def _goal(session, title, *, deadline=None, **fields):
    return await create_card(
        session,
        kind="goal",
        title=title,
        schedule=deadline.isoformat() if deadline else None,
        schedule_rule={"kind": "deadline", "date": deadline.isoformat(), "time": None}
        if deadline else None,
        **fields,
    )


def _titles(context):
    goals = context.state.split("Priority Goals:\n", 1)[1].split("Today Actions:", 1)[0]
    return [line.split("](")[0].removeprefix("- [") for line in goals.splitlines()]


async def test_priority_goals_are_open_root_goals_of_every_priority(sessions, goal_clock):
    """WS-CONTEXT-008 — tests/brd/workspace_mutator.feature"""
    async with sessions() as session:
        low = await _goal(session, "Low", priority="low")
        await _goal(session, "Medium", priority="medium")
        critical = await _goal(session, "Critical", priority="critical")
        for index in range(state.CONTEXT_PRIORITY_GOAL_LIMIT):
            await _goal(session, f"Filler {index}", priority="low")
        closed = await _goal(session, "Closed", priority="critical")
        await finish_card(session, closed.id)
        await create_card(session, kind="subgoal", title="Subgoal", parent_id=low.id)
        await create_card(session, kind="action", title="Action", priority="critical")
        await session.commit()
        context = await state.workspace_context(session)

    assert _titles(context) == ["Critical", "Medium", "Low"] + [
        f"Filler {index}" for index in range(state.CONTEXT_PRIORITY_GOAL_LIMIT - 3)
    ]
    assert f"[Critical](card:{critical.id}) priority=critical stage=backlog" in context.state
    assert "Critical Cards:" not in context.state


@pytest.mark.parametrize(
    ("case", "urgent"),
    [("overdue", True), ("today", True), ("boundary", True), ("later", False)],
)
async def test_urgent_goal_deadlines_outrank_priority_in_the_local_day(
    sessions, goal_clock, case, urgent
):
    """WS-CONTEXT-008 — tests/brd/workspace_mutator.feature"""
    offset = {
        "overdue": -1,
        "today": 0,
        "boundary": PRIORITY_GOAL_DEADLINE_DAYS,
        "later": PRIORITY_GOAL_DEADLINE_DAYS + 1,
    }[case]
    day = goal_clock.date() + timedelta(days=offset)
    async with sessions() as session:
        await _goal(session, "Critical", priority="critical")
        await _goal(session, "Deadline", priority="low", deadline=day)
        await session.commit()
        context = await state.workspace_context(session)

    assert _titles(context) == (["Deadline", "Critical"] if urgent else ["Critical", "Deadline"])
    assert f"deadline={day:%d.%m.%Y} 23:59" in context.state


@pytest.mark.parametrize("stage", ["sprint", "today"])
async def test_goals_with_planned_work_through_subgoals_come_before_other_equal_goals(
    sessions, goal_clock, stage
):
    """WS-CONTEXT-008 — tests/brd/workspace_mutator.feature"""
    near = goal_clock.date() + timedelta(days=PRIORITY_GOAL_DEADLINE_DAYS + 1)
    async with sessions() as session:
        await _goal(session, "Earlier deadline", deadline=near)
        planned = await _goal(session, "Planned", deadline=near + timedelta(days=1))
        subgoal = await create_card(session, kind="subgoal", title="Milestone", parent_id=planned.id)
        await create_card(
            session, kind="action", title="Next step", parent_id=subgoal.id, stage=stage
        )
        await session.commit()
        context = await state.workspace_context(session)

    assert _titles(context) == ["Planned", "Earlier deadline"]


async def test_equal_goals_use_deadlines_then_age_and_id(sessions, goal_clock):
    """WS-CONTEXT-008 — tests/brd/workspace_mutator.feature"""
    day = goal_clock.date() + timedelta(days=PRIORITY_GOAL_DEADLINE_DAYS + 1)
    async with sessions() as session:
        first = await _goal(session, "First")
        second = await _goal(session, "Second")
        older = await _goal(session, "Older")
        later = await _goal(session, "Later deadline", deadline=day + timedelta(days=1))
        sooner = await _goal(session, "Sooner deadline", deadline=day)
        first.created_at = second.created_at = goal_clock
        older.created_at = goal_clock - timedelta(days=1)
        await session.commit()
        context = await state.workspace_context(session)

    assert _titles(context) == [sooner.title, later.title, older.title, first.title, second.title]


async def test_no_open_goals_leave_no_empty_heading(sessions, goal_clock):
    """WS-CONTEXT-008 — tests/brd/workspace_mutator.feature"""
    async with sessions() as session:
        closed = await _goal(session, "Closed")
        await finish_card(session, closed.id)
        await session.commit()
        context = await state.workspace_context(session)

    assert "Priority Goals:" not in context.state
