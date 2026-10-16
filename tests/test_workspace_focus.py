"""What the Advisor is handed about the work: a Sprint read tactically, Planning strategically."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from safwa.features.cards.use_cases import create_card, finish_action, finish_card
from safwa.features.planning.model import SprintCommitment
from safwa.features.planning.use_cases import start_sprint
from safwa.features.values.use_cases import create_value
from safwa.features.workspace_mutator.state import CONTEXT_LIST_LIMIT, workspace_context


def _section(state: str, heading: str) -> tuple[str, list[str]]:
    """The heading that starts with `heading`, and the Card lines under it."""
    lines = state.splitlines()
    start = next(index for index, line in enumerate(lines) if line.startswith(heading))
    cards: list[str] = []
    for line in lines[start + 1:]:
        if line.endswith(":"):
            break
        if line.startswith("- "):
            cards.append(line)
    return lines[start], cards


def _titles(lines: list[str]) -> list[str]:
    return [line.removeprefix("- [").split("](")[0] for line in lines]


async def test_planning_hands_every_open_goal_the_lower_levels_first(sessions):
    """WS-CONTEXT-008 — tests/brd/workspace_mutator.feature"""
    async with sessions() as session:
        top = await create_card(session, kind="goal", title="Top")
        sub = await create_card(session, kind="goal", title="Sub", parent_ids={top.id})
        await create_card(session, kind="goal", title="Critical", priority="critical")
        closed = await create_card(session, kind="goal", title="Closed")
        await finish_card(session, closed.id)
        for index in range(CONTEXT_LIST_LIMIT):
            await create_card(session, kind="goal", title=f"Filler {index}", priority="low")
        await session.commit()
        state = (await workspace_context(session)).state

    heading, lines = _section(state, "Goals to consider")
    assert heading == f"Goals to consider, best first ({CONTEXT_LIST_LIMIT} of {CONTEXT_LIST_LIMIT + 3}):"
    assert _titles(lines)[:3] == ["Critical", "Sub", "Top"]
    assert f"- [Sub](card:{sub.id}) priority=medium stage=backlog tree_level=1 under=[Top](card:{top.id})" in lines
    assert "Closed" not in state and "Sprint Goals" not in state


async def test_a_sprint_hands_only_the_goals_above_its_work(sessions):
    """WS-CONTEXT-008 — tests/brd/workspace_mutator.feature"""
    async with sessions() as session:
        await create_value(session, "Health", "Body and mind")
        root = await create_card(session, kind="goal", title="Root")
        milestone = await create_card(session, kind="goal", title="Milestone", parent_ids={root.id})
        await create_card(session, kind="action", title="Step", stage="sprint", parent_ids={milestone.id})
        idle = await create_card(session, kind="goal", title="Idle")
        await create_card(session, kind="action", title="Later", parent_ids={idle.id})
        await start_sprint(session, success_criteria="Ship v2")
        await session.commit()
        state = (await workspace_context(session)).state

    assert _titles(_section(state, "Sprint Goals")[1]) == ["Milestone", "Root"]
    for absent in ("Goals to consider", "Backlog Actions", "Idle", "Later", "goals="):
        assert absent not in state


async def test_a_sprint_lists_its_other_actions_key_first_within_the_limit(sessions):
    """WS-CONTEXT-011 — tests/brd/workspace_mutator.feature"""
    async with sessions() as session:
        for index in range(CONTEXT_LIST_LIMIT):
            await create_card(session, kind="action", title=f"Step {index}", stage="sprint")
        key = await create_card(session, kind="action", title="Key", stage="sprint")
        await create_card(session, kind="action", title="Now", stage="today")
        await start_sprint(session, success_criteria="Ship v2")
        commitment = await session.scalar(
            select(SprintCommitment).where(SprintCommitment.card_id == key.id)
        )
        commitment.key_action = True
        await session.commit()
        state = (await workspace_context(session)).state

    heading, lines = _section(state, "Sprint Actions not in Today")
    assert heading == f"Sprint Actions not in Today, best first ({CONTEXT_LIST_LIMIT} of {CONTEXT_LIST_LIMIT + 1}):"
    assert lines[0] == f"- [Key](card:{key.id}) priority=medium key"
    assert "Now" not in "\n".join(lines) and _titles(_section(state, "Today Actions")[1]) == ["Now"]
    assert state.index("Today Actions") < state.index("Sprint Actions not in Today")
    assert "Backlog Actions" not in state


@pytest.mark.parametrize("shared_subgoal", [False, True])
async def test_today_lines_carry_their_own_priority_and_name_their_goals(
    sessions, effort_on, shared_subgoal
):
    """WS-CONTEXT-010 — tests/brd/workspace_mutator.feature"""
    async with sessions() as session:
        health = await create_value(session, "Health", "Body and mind")
        first = await create_card(
            session, kind="goal", title="First", priority="critical", value_ids={health.id}
        )
        second = await create_card(session, kind="goal", title="Second", priority="critical")
        parents = {first.id, second.id}
        if shared_subgoal:
            shared = await create_card(session, kind="goal", title="Shared", parent_ids=parents)
            parents = {shared.id}
        action = await create_card(
            session, kind="action", title="Work", stage="today", parent_ids=parents,
            priority="low", effort_points=3,
        )
        await session.commit()
        state = (await workspace_context(session)).state

    assert "Planned executions remaining today: 1" in state
    assert "Remaining planned effort today: 3 EP" in state
    under = (
        f"under=[Shared](card:{shared.id})" if shared_subgoal
        else f"under=[First](card:{first.id}), [Second](card:{second.id})"
    )
    assert _section(state, "Today Actions")[1] == [
        f"- [Work](card:{action.id}) priority=low effort=3 values=[Health](value:{health.id}) {under}"
    ]
    assert "| Card |" not in state


async def test_a_today_line_marks_blocked_and_its_executions(sessions):
    """WS-CONTEXT-010 — tests/brd/workspace_mutator.feature"""
    async with sessions() as session:
        action = await create_card(
            session, kind="action", title="Drink water", stage="today",
            blocked_description="No bottle", schedule="3 times a day",
            schedule_rule={"kind": "quota", "period": "day", "count": 3},
        )
        await session.commit()
        state = (await workspace_context(session)).state
    assert _section(state, "Today Actions")[1] == [
        f"- [Drink water](card:{action.id}) priority=medium blocked executions=3"
    ]


async def test_a_title_stays_on_its_line(sessions):
    """WS-CONTEXT-010 — tests/brd/workspace_mutator.feature"""
    async with sessions() as session:
        action = await create_card(session, kind="action", title="Work | rest\nagain", stage="today")
        state = (await workspace_context(session)).state
    assert _section(state, "Today Actions")[1] == [f"- [Work | rest again](card:{action.id}) priority=medium"]


async def test_an_empty_today_lists_nothing(sessions):
    """WS-CONTEXT-010 — tests/brd/workspace_mutator.feature"""
    async with sessions() as session:
        state = (await workspace_context(session)).state
    assert "Planned executions remaining today: 0" in state
    assert _section(state, "Today Actions")[1] == []


async def test_planning_counts_what_serves_each_value_and_reads_the_backlog(sessions):
    """WS-CONTEXT-012 — tests/brd/workspace_mutator.feature"""
    async with sessions() as session:
        health = await create_value(session, "Health", "Body and mind")
        craft = await create_value(session, "Craft", "Making things")
        live = await create_card(session, kind="goal", title="Live long", value_ids={health.id})
        fit = await create_card(session, kind="goal", title="Get fit", parent_ids={live.id})
        run = await create_card(session, kind="action", title="Run", parent_ids={fit.id})
        done = await create_card(session, kind="action", title="Ran once", parent_ids={fit.id})
        await finish_action(session, done.id)
        await create_card(session, kind="action", title="Read", stage="sprint")
        await session.commit()
        state = (await workspace_context(session)).state

    assert f"- [Health](value:{health.id}) goals=2 actions=1: Body and mind" in state
    assert f"- [Craft](value:{craft.id}) goals=0 actions=0: Making things" in state
    headings = ["Today Actions", "Goals to consider", "Backlog Actions to consider", "Still in Sprint, to review"]
    assert [state.index(heading) for heading in headings] == sorted(state.index(heading) for heading in headings)
    assert _section(state, "Backlog Actions to consider")[1] == [
        f"- [Run](card:{run.id}) priority=medium values=[Health](value:{health.id}) under=[Get fit](card:{fit.id})"
    ]
    assert _titles(_section(state, "Still in Sprint, to review")[1]) == ["Read"]
    assert "Sprint Actions not in Today" not in state


async def test_no_open_goals_leave_no_goal_heading(sessions):
    """WS-CONTEXT-008 — tests/brd/workspace_mutator.feature"""
    async with sessions() as session:
        closed = await create_card(session, kind="goal", title="Closed")
        await finish_card(session, closed.id)
        await session.commit()
        state = (await workspace_context(session)).state
    assert "Goals to consider" not in state
