from __future__ import annotations

import pytest
from hook_helpers import changes_of
from pydantic import ValidationError
from schedule_helpers import create_card, create_check
from sqlalchemy import select

from safwa.bootstrap.modules import PROPOSALS
from safwa.features.cards.hierarchy import card_parent_ids, card_progress
from safwa.features.cards.hooks import parent_completion_request
from safwa.features.cards.model import CARD_TREE_DEPTH_MAX, Card, CardKind
from safwa.features.cards.use_cases import (
    CARD_ACTIONS_FINISHED,
    CARD_ORPHANED,
    archive_subtree,
    delete_card,
    finish_action,
    finish_card,
    reopen_card,
    toggle_card_check,
    toggle_card_parent,
    update_card_fields,
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
                parent_ids={goals[-1].id} if goals else set(),
            )
        )
    return goals


async def _prepare(session, kind, arguments):
    return await ChangePreparer(None, None, PROPOSALS).prepare(  # type: ignore[arg-type]
        session, PROPOSALS.change_from_tool(kind, arguments)
    )


@pytest.mark.parametrize("kind", ["goal", "action"])
async def test_cd_tree_049_creation_counts_every_card_level(sessions, kind):
    """CD-TREE-049 — tests/brd/cards.feature"""
    assert set(CardKind) == {CardKind.GOAL, CardKind.ACTION}
    async with sessions() as session:
        goals = await _goals(session, CARD_TREE_DEPTH_MAX)
        leaf = await create_card(session, kind=kind, title="Last level", parent_ids={goals[-2].id})
        assert leaf.kind == kind
        with pytest.raises(DomainError, match="at most.*levels"):
            await create_card(session, kind=kind, title="Too deep", parent_ids={goals[-1].id})
        with pytest.raises(ToolPreparationError, match="at most.*levels"):
            await _prepare(session, kind, {"mode": "create", "title": "Too deep", "parent": goals[-1].id})


async def test_cd_tree_049_a_new_parent_counts_every_path_and_repairs_every_goal_above(sessions):
    """CD-TREE-049 — tests/brd/cards.feature"""
    async with sessions() as session:
        goals = await _goals(session, CARD_TREE_DEPTH_MAX - 1)
        source = await create_card(session, kind="goal", title="Source")
        branch = await create_card(session, kind="goal", title="Branch", parent_ids={source.id})
        action = await create_card(
            session,
            kind="action",
            title="Work",
            parent_ids={branch.id},
            stage="today",
            effort_points=3,
        )
        version = branch.version
        with pytest.raises(DomainError, match="at most.*levels"):
            await toggle_card_parent(session, branch.id, goals[-1].id)
        with pytest.raises(ToolPreparationError, match="at most.*levels"):
            await _prepare(session, "goal", {"mode": "link", "id": branch.id, "parent": goals[-1].id})
        assert (await card_parent_ids(session, branch.id), branch.version) == ([source.id], version)

        await toggle_card_parent(session, branch.id, goals[-2].id)
        assert (goals[0].effective_stage, goals[0].effort_points) == ("today", 3)
        assert (source.effective_stage, source.effort_points) == ("today", 3)
        await toggle_card_parent(session, branch.id, source.id)
        assert (source.effective_stage, source.effort_points) == ("backlog", None)
        assert await card_parent_ids(session, action.id) == [branch.id]
        await toggle_card_parent(session, branch.id, goals[-2].id)
        assert goals[0].effort_points is None and branch.effort_points == 3
        assert await card_parent_ids(session, branch.id) == []


@pytest.mark.parametrize("parent_index", [0, 2])
async def test_cd_tree_050_cycles_are_refused_before_review_and_on_save(sessions, parent_index):
    """CD-TREE-050 — tests/brd/cards.feature"""
    async with sessions() as session:
        goals = await _goals(session, 3)
        original = [
            (await card_parent_ids(session, goal.id), goal.version) for goal in goals
        ]
        with pytest.raises(ToolPreparationError, match="itself or its descendant"):
            await _prepare(
                session, "goal", {"mode": "link", "id": goals[0].id, "parent": goals[parent_index].id}
            )
        with pytest.raises(DomainError, match="itself or its descendant"):
            await toggle_card_parent(session, goals[0].id, goals[parent_index].id)
        assert [
            (await card_parent_ids(session, goal.id), goal.version) for goal in goals
        ] == original


async def test_cd_tree_056_a_card_stands_under_several_goals(sessions):
    """CD-TREE-056 — tests/brd/cards.feature"""
    async with sessions() as session:
        blog = await create_card(session, kind="goal", title="Launch the blog")
        audience = await create_card(session, kind="goal", title="Grow audience")
        post = await create_card(session, kind="goal", title="First post", parent_ids={blog.id})
        write = await create_card(session, kind="action", title="Write it", parent_ids={post.id})
        await session.commit()

        linked = await _prepare(
            session, "goal", {"mode": "link", "id": post.id, "parent": "Grow audience"}
        )
        assert linked.values["parents"] == "Grow audience"
        assert await toggle_card_parent(session, post.id, audience.id) is True
        assert await card_parent_ids(session, post.id) == sorted([blog.id, audience.id])
        assert await session.scalar(
            select(LogEvent.operation)
            .where(LogEvent.item_type == "card", LogEvent.item_id == post.id)
            .order_by(LogEvent.id.desc())
        ) == "link_parent"
        await toggle_card_parent(session, write.id, audience.id)
        assert await card_parent_ids(session, write.id) == sorted([post.id, audience.id])

        created = await _prepare(
            session, "action", {"mode": "create", "title": "Edit it", "parent": post.id}
        )
        assert created.values["parents"] == post.id
        with pytest.raises(ValidationError, match="use link or unlink"):
            PROPOSALS.change_from_tool("goal", {"mode": "update", "id": post.id, "parent": blog.id})
        with pytest.raises(ValidationError):
            PROPOSALS.change_from_tool(
                "goal", {"mode": "link", "id": post.id, "parent": [blog.id, audience.id]}
            )

        assert await toggle_card_parent(session, post.id, blog.id) is False
        assert await card_parent_ids(session, post.id) == [audience.id]


async def test_cd_effort_021_an_action_reached_along_two_paths_counts_once(sessions):
    """CD-EFFORT-021 — tests/brd/cards.feature"""
    async with sessions() as session:
        goal = await create_card(session, kind="goal", title="Launch")
        first = await create_card(session, kind="goal", title="Write", parent_ids={goal.id})
        second = await create_card(session, kind="goal", title="Publish", parent_ids={goal.id})
        shared = await create_card(
            session, kind="action", title="Draft", effort_points=5, parent_ids={first.id, second.id}
        )
        await create_card(session, kind="action", title="Upload", effort_points=3, parent_ids={second.id})
        assert (first.effort_points, second.effort_points, goal.effort_points) == (5, 8, 8)
        await update_card_fields(session, shared.id, {"effort_points": 8})
        assert (first.effort_points, second.effort_points, goal.effort_points) == (8, 11, 11)
        assert (await card_progress(session, goal.id))["effort_points"] == 11


async def test_cd_time_040_an_action_reached_along_two_paths_counts_once(sessions):
    """CD-TIME-040 — tests/brd/cards.feature"""
    async with sessions() as session:
        goal = await create_card(session, kind="goal", title="Launch")
        first = await create_card(session, kind="goal", title="Write", parent_ids={goal.id})
        second = await create_card(session, kind="goal", title="Publish", parent_ids={goal.id})
        shared = await create_card(
            session, kind="action", title="Draft", parent_ids={first.id, second.id}
        )
        other = await create_card(session, kind="action", title="Upload", parent_ids={second.id})
        await finish_action(session, shared.id, tracked_mins=30)
        await finish_action(session, other.id, tracked_mins=45)
        assert (first.tracked_mins, second.tracked_mins, goal.tracked_mins) == (30, 75, 75)


async def test_cd_stage_017_reopening_an_action_leaves_closed_goals_closed(sessions):
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
            parent_ids={goals[-1].id},
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
            goal.effective_stage == "done"
            and goal.completed_at is not None
            and goal.archived_at is None
            for goal in goals
        )
        assert all(check.outcome == CheckOutcome.PASSED.value for check in checks)
        await delete_card(session, goals[0].id)
        assert await card_parent_ids(session, goals[1].id) == []


async def test_cd_stage_015_a_goal_closes_over_open_cards_and_stays_closed(sessions):
    """CD-STAGE-015 — tests/brd/cards.feature"""
    async with sessions() as session:
        goal = await create_card(session, kind="goal", title="Launch")
        subgoal = await create_card(session, kind="goal", title="Write", parent_ids={goal.id})
        action = await create_card(
            session, kind="action", title="Draft", stage="today", parent_ids={subgoal.id}
        )
        await finish_card(session, goal.id)
        assert (goal.effective_stage, subgoal.effective_stage, action.effective_stage) == (
            "done", "today", "today",
        )
        await finish_action(session, action.id)
        await reopen_card(session, action.id)
        assert (goal.effective_stage, subgoal.effective_stage) == ("done", "backlog")
        await create_card(session, kind="action", title="Edit", parent_ids={goal.id})
        assert goal.effective_stage == "done"


async def test_cd_delete_025_children_lose_the_deleted_parent_and_keep_their_others(sessions):
    """CD-DELETE-025 — tests/brd/cards.feature"""
    async with sessions() as session:
        root, middle, child = await _goals(session, 3)
        direct = await create_card(
            session,
            kind="action",
            title="Direct",
            parent_ids={middle.id, root.id},
            effort_points=2,
        )
        deep = await create_card(
            session, kind="action", title="Deep", parent_ids={child.id}, stage="today", effort_points=3
        )
        assert (root.effective_stage, root.effort_points) == ("today", 5)
        await delete_card(session, middle.id)
        orphaned = changes_of(session, CARD_ORPHANED)
        await session.commit()
        assert await session.get(Card, middle.id) is None
        assert [await card_parent_ids(session, card.id) for card in (child, direct, deep)] == [
            [],
            [root.id],
            [child.id],
        ]
        assert (root.effective_stage, root.effort_points) == ("backlog", 2)
        assert [(change.subject_id, dict(change.data)) for change in orphaned] == [
            (middle.id, {"title": "Goal 1", "parent_ids": [root.id], "children": [child.id]})
        ]
        for moved in (child, direct):
            event = await session.scalar(
                select(LogEvent)
                .where(LogEvent.item_type == "card", LogEvent.item_id == moved.id)
                .order_by(LogEvent.id.desc())
            )
            assert event.operation == "unlink_parent"
