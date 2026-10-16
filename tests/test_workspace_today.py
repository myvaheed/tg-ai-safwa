"""The requested Today table keeps the existing hierarchy and its metadata."""

from __future__ import annotations

import pytest

from safwa.features.cards.use_cases import create_card
from safwa.features.workspace_mutator.state import workspace_context


@pytest.mark.parametrize("shared_subgoal", [False, True])
async def test_today_table_keeps_shared_cards_once_with_other_parent_links(
    sessions, effort_on, shared_subgoal
):
    """WS-CONTEXT-010 — tests/brd/workspace_mutator.feature"""
    async with sessions() as session:
        first = await create_card(session, kind="goal", title="First", priority="critical")
        second = await create_card(session, kind="goal", title="Second", priority="critical")
        parents = {first.id, second.id}
        shared = None
        if shared_subgoal:
            shared = await create_card(session, kind="goal", title="Shared", parent_ids=parents)
            parents = {shared.id}
        action = await create_card(
            session, kind="action", title="Work", stage="today", parent_ids=parents,
            priority="low", effort_points=3,
        )
        independent = await create_card(session, kind="goal", title="Independent")
        state = (await workspace_context(session)).state

    goals, today = state.split("Today Actions:", 1)
    assert f"[Independent](card:{independent.id})" in goals
    assert "Independent" not in today
    assert "Planned executions remaining today: 1" in today
    assert "Remaining planned effort today: 3 EP" in today
    rows = [line for line in today.splitlines() if line.startswith("| ")]
    assert rows[:2] == ["| Card | Priority |", "| --- | --- |"]
    expected = [
        f"| - [First](card:{first.id}) Goal |  |",
        f"| - [Second](card:{second.id}) Goal |  |",
    ]
    also = f" (also under: [First](card:{first.id}))"
    if shared:
        expected.append(f"|   - [Shared](card:{shared.id}) Subgoal{also} |  |")
        expected.append(f"|     - [Work](card:{action.id}) effort=3 | low |")
    else:
        expected.append(f"|   - [Work](card:{action.id}){also} effort=3 | low |")
    assert rows[2:] == expected


async def test_today_table_escapes_title_separators(sessions):
    """WS-CONTEXT-010 — tests/brd/workspace_mutator.feature"""
    async with sessions() as session:
        action = await create_card(session, kind="action", title="Work | rest\nagain", stage="today")
        today = (await workspace_context(session)).state.split("Today Actions:", 1)[1]
    rows = [line for line in today.splitlines() if line.startswith("| ")]
    assert rows[2:] == [f"| - [Work \\| rest again](card:{action.id}) | medium |"]


async def test_empty_today_has_no_table(sessions):
    """WS-CONTEXT-010 — tests/brd/workspace_mutator.feature"""
    async with sessions() as session:
        today = (await workspace_context(session)).state.split("Today Actions:", 1)[1]
    assert "Planned executions remaining today: 0" in today
    assert "| Card |" not in today
