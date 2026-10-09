from __future__ import annotations

import pytest
from hook_helpers import changes_of
from schedule_helpers import create_card, create_check
from sqlalchemy import select

from safwa.bootstrap.modules import PROPOSALS
from safwa.features.cards.hierarchy import card_progress
from safwa.features.cards.hooks import parent_completion_request
from safwa.features.cards.model import CARD_TREE_DEPTH_MAX, Card, CardKind
from safwa.features.cards.use_cases import (
    CARD_ACTIONS_FINISHED,
    archive_subtree,
    delete_one_card,
    delete_subtree,
    finish_action,
    finish_card,
    reopen_card,
    set_card_parent,
    toggle_card_check,
)
from safwa.features.checks.model import CheckOutcome
from safwa.foundation.log_events import LogEvent
from tg_agent_shell.foundation.errors import DomainError
from tg_agent_shell.proposals.api import ToolPreparationError
from tg_agent_shell.proposals.prepare import ChangePreparer


async def _goals(session, depth):
    goals = []
    for level in range(depth):
        goals.append(
            await create_card(
                session,
                kind="goal",
                title=f"Goal {level}",
                parent_id=goals[-1].id if goals else None,
            )
        )
    return goals


@pytest.mark.parametrize("kind", ["goal", "action"])
async def test_cd_tree_049_creation_counts_every_card_level(sessions, kind):
    """CD-TREE-049 — tests/brd/cards.feature"""
    assert set(CardKind) == {CardKind.GOAL, CardKind.ACTION}
    async with sessions() as session:
        goals = await _goals(session, CARD_TREE_DEPTH_MAX)
        leaf = await create_card(session, kind=kind, title="Last level", parent_id=goals[-2].id)
        assert leaf.kind == kind
        with pytest.raises(DomainError, match="at most.*levels"):
            await create_card(session, kind=kind, title="Too deep", parent_id=goals[-1].id)
        with pytest.raises(ToolPreparationError, match="at most.*levels"):
            await ChangePreparer(None, None, PROPOSALS).prepare(
                session,
                PROPOSALS.change_from_tool(
                    kind, {"mode": "create", "title": "Too deep", "parent": goals[-1].id}
                ),
            )


async def test_cd_tree_049_a_move_counts_the_whole_branch_and_repairs_both_roots(sessions):
    """CD-TREE-049 — tests/brd/cards.feature"""
    async with sessions() as session:
        goals = await _goals(session, CARD_TREE_DEPTH_MAX - 1)
        source = await create_card(session, kind="goal", title="Source")
        branch = await create_card(session, kind="goal", title="Branch", parent_id=source.id)
        action = await create_card(
            session,
            kind="action",
            title="Work",
            parent_id=branch.id,
            stage="today",
            effort_points=3,
        )
        original = (branch.parent_id, branch.version)
        with pytest.raises(DomainError, match="at most.*levels"):
            await set_card_parent(session, branch.id, goals[-1].id)
        with pytest.raises(ToolPreparationError, match="at most.*levels"):
            await ChangePreparer(None, None, PROPOSALS).prepare(
                session,
                PROPOSALS.change_from_tool(
                    "goal", {"mode": "update", "id": branch.id, "parent": goals[-1].id}
                ),
            )
        assert (branch.parent_id, branch.version) == original
        await set_card_parent(session, branch.id, goals[-2].id)
        assert (source.effective_stage, source.effort_points) == ("backlog", None)
        assert (goals[0].effective_stage, goals[0].effort_points) == ("today", 3)
        assert action.parent_id == branch.id and branch.kind == "goal"
        await set_card_parent(session, branch.id, None)
        assert goals[0].effort_points is None and branch.effort_points == 3


@pytest.mark.parametrize("parent_index", [0, 2])
async def test_cd_tree_050_cycles_are_refused_before_review_and_on_save(sessions, parent_index):
    """CD-TREE-050 — tests/brd/cards.feature"""
    async with sessions() as session:
        goals = await _goals(session, 3)
        original = [(goal.parent_id, goal.version) for goal in goals]
        with pytest.raises(ToolPreparationError, match="itself or its descendant"):
            await ChangePreparer(None, None, PROPOSALS).prepare(
                session,
                PROPOSALS.change_from_tool(
                    "goal", {"mode": "update", "id": goals[0].id, "parent": goals[parent_index].id}
                ),
            )
        with pytest.raises(DomainError, match="itself or its descendant"):
            await set_card_parent(session, goals[0].id, goals[parent_index].id)
        assert [(goal.parent_id, goal.version) for goal in goals] == original


async def test_cd_stage_017_every_level_derives_progress_archive_and_reopening(sessions):
    """CD-STAGE-017 — tests/brd/cards.feature"""
    async with sessions() as session:
        goals = await _goals(session, CARD_TREE_DEPTH_MAX - 1)
        checks = [
            await create_check(session, title=f"Check {index}") for index in range(len(goals))
        ]
        for goal, check in zip(goals, checks, strict=True):
            await toggle_card_check(session, goal.id, check.id)
        action = await create_card(
            session,
            kind="action",
            title="Work",
            parent_id=goals[-1].id,
            stage="today",
            effort_points=3,
        )
        assert all((goal.effective_stage, goal.effort_points) == ("today", 3) for goal in goals)
        await finish_action(session, action.id, tracked_mins=25)
        parents = [change.subject_id for change in changes_of(session, CARD_ACTIONS_FINISHED)]
        assert set(parents) == {goal.id for goal in goals}
        question = await parent_completion_request(session, parents)
        assert all(f"#{goal.id} «{goal.title}»" in question for goal in goals)
        assert all(goal.tracked_mins == 25 for goal in goals)
        assert (await card_progress(session, goals[0].id))["completed_effort"] == 3
        for goal, check in reversed(list(zip(goals, checks, strict=True))):
            await finish_card(session, goal.id, check_outcomes={check.id: CheckOutcome.PASSED})
        await archive_subtree(session, goals[0].id)
        assert all(goal.archived_at is not None for goal in goals)
        await reopen_card(session, action.id)
        assert all(
            goal.completed_at is None
            and goal.archived_at is None
            and goal.manual_stage == "backlog"
            for goal in goals
        )
        assert all(check.outcome is None for check in checks)
        assert await delete_subtree(session, goals[0].id) == CARD_TREE_DEPTH_MAX


async def test_cd_delete_025_children_take_the_deleted_goals_parent(sessions):
    """CD-DELETE-025 — tests/brd/cards.feature"""
    async with sessions() as session:
        root, middle, child = await _goals(session, 3)
        direct = await create_card(
            session, kind="action", title="Direct", parent_id=middle.id, effort_points=2
        )
        deep = await create_card(
            session, kind="action", title="Deep", parent_id=child.id, stage="today", effort_points=3
        )
        before = root.effort_points
        assert await delete_one_card(session, middle.id) == 1
        await session.commit()
        assert await session.get(Card, middle.id) is None
        assert (child.parent_id, direct.parent_id, deep.parent_id) == (root.id, root.id, child.id)
        assert (root.effective_stage, root.effort_points) == ("today", before)
        for moved in (child, direct):
            event = await session.scalar(
                select(LogEvent)
                .where(LogEvent.item_type == "card", LogEvent.item_id == moved.id)
                .order_by(LogEvent.id.desc())
            )
            assert event.operation == "set_parent"
