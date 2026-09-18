"""What a Card is: its kind, its place in the tree, and the fields that kind may carry.

Every test here is evidence for one scenario in `tests/brd/cards.feature`. The stage ladder,
the Checks that gate completion and the repeat successor are the packets after this one.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from safwa.bootstrap.modules import PROPOSALS, SYSTEM_PROMPT
from safwa.features.cards.agent import CardToolInput
from safwa.features.cards.hard_time import typed_hard_time
from safwa.features.cards.hierarchy import blocking_actions, card_children, card_progress
from safwa.features.cards.hooks import (
    BLOCKER_HOOK,
    EMPTY_PARENT_GRACE_DAYS,
    EMPTY_PARENTS_HOOK,
    REST_TODAY_HOOK,
    TODAY_CAPACITY_EP,
    TODAY_OVERLOAD_HOOK,
    blocker_request,
    empty_parents_request,
    rest_today_request,
    today_overload_request,
)
from safwa.features.cards.model import (
    EFFORT_RUNGS,
    Card,
    CardCategory,
    CardCheck,
    CardEnergyType,
    CardEvent,
    CardKind,
    CardStage,
    effort_label,
)
from safwa.features.cards.telegram.presentation import paginate_cards
from safwa.features.cards.use_cases import (
    CARD_BLOCKED,
    CARD_TODAY,
    EFFORT_POINTS,
    archive_subtree,
    create_card,
    delete_one_card,
    delete_subtree,
    edit_card_text,
    finish_action,
    move_card,
    set_card_parent,
    toggle_card_check,
    toggle_card_tag,
    toggle_card_value,
    update_card_fields,
)
from safwa.features.checks.model import Check, CheckOutcome
from safwa.features.checks.use_cases import check_card_id, create_check, toggle_check_value
from safwa.features.planning.model import SprintCommitment
from safwa.features.planning.use_cases import finish_sprint, start_sprint
from safwa.features.profile.api import morning_time
from safwa.features.profile.model import MORNING_TIME_DEFAULT, ProfileField, UserProfile
from safwa.features.profile.use_cases import set_profile_field
from safwa.features.tags.model import CardTag, Tag
from safwa.features.tags.use_cases import create_tag
from safwa.features.values.model import CardValue, Value
from safwa.features.values.use_cases import create_value, delete_value, set_value_focus
from safwa.features.workspace_mutator.state import workspace_context
from safwa.foundation.marks import live_repeat_instance_id, title_marks
from tg_agent_shell.foundation.changes import Committed, take_changes
from tg_agent_shell.foundation.clock import utcnow
from tg_agent_shell.foundation.errors import DomainError
from tg_agent_shell.hooks.contracts import OnCommitted, OnTick
from tg_agent_shell.proposals.api import ToolPreparationError
from tg_agent_shell.proposals.prepare import ChangePreparer


async def test_cd_kind_001_a_card_stays_the_kind_it_was_created_as(sessions):
    """CD-KIND-001 — tests/brd/cards.feature"""
    async with sessions() as session:
        goal = await create_card(session, kind="goal", title="Health")
        subgoal = await create_card(
            session, kind="subgoal", title="Sleep better", parent_id=goal.id
        )
        await session.commit()

        with pytest.raises(DomainError, match="Unsupported Card fields"):
            await update_card_fields(session, subgoal.id, {"kind": "action"})

        assert (await session.get(Card, subgoal.id)).kind == CardKind.SUBGOAL.value

    with pytest.raises(ValidationError, match="does not accept"):
        CardToolInput(mode="update", id=1, kind="action")


async def test_cd_tree_002_a_goal_placed_under_a_goal_becomes_a_subgoal(sessions):
    """CD-TREE-002 — tests/brd/cards.feature"""
    async with sessions() as session:
        health = await create_card(session, kind="goal", title="Health")
        life = await create_card(session, kind="goal", title="Life")
        work = await create_card(session, kind="goal", title="Work")
        sleep = await create_card(
            session, kind="subgoal", title="Sleep better", parent_id=work.id
        )
        walk = await create_card(
            session, kind="action", title="Walk", effort_points=1, parent_id=life.id
        )
        await session.commit()

        with pytest.raises(DomainError, match="root-level"):
            await create_card(session, kind="goal", title="Nested", parent_id=life.id)

        await set_card_parent(session, health.id, life.id)
        await session.commit()
        await session.refresh(health)
        assert (health.kind, health.parent_id) == (CardKind.SUBGOAL.value, life.id)
        assert await session.scalar(
            select(CardEvent.operation).where(CardEvent.card_id == health.id).order_by(
                CardEvent.id.desc()
            )
        ) == "edit_kind"

        with pytest.raises(DomainError, match="Subgoals under it"):
            await set_card_parent(session, work.id, life.id)
        with pytest.raises(DomainError, match="only be placed under a Goal"):
            await set_card_parent(session, work.id, sleep.id)
        with pytest.raises(DomainError, match="cannot have children"):
            await set_card_parent(session, work.id, walk.id)
        assert (work.kind, work.parent_id) == (CardKind.GOAL.value, None)


async def test_cd_tree_002_a_proposal_says_the_goal_becomes_a_subgoal(sessions):
    """CD-TREE-002 — tests/brd/cards.feature"""
    async with sessions() as session:
        health = await create_card(session, kind="goal", title="Health")
        life = await create_card(session, kind="goal", title="Life")
        work = await create_card(session, kind="goal", title="Work")
        await create_card(session, kind="subgoal", title="Sleep better", parent_id=work.id)
        await session.commit()

        refused = await _refused_proposal(
            session, {"mode": "create", "kind": "goal", "title": "Nested", "parent_id": life.id}
        )
        assert refused.code == "invalid_parent_kind"
        assert "root-level" in str(refused)

        prepared = await ChangePreparer(None, None, PROPOSALS).prepare(  # type: ignore[arg-type]
            session,
            PROPOSALS.change_from_tool(
                "card", {"mode": "update", "id": health.id, "parent_query": "Life"}
            ),
        )
        # The change of kind is in the proposal, so the screen and the receipt say it.
        assert prepared.values == {"parent_id": life.id, "kind": CardKind.SUBGOAL.value}

        refused = await _refused_proposal(
            session, {"mode": "update", "id": work.id, "parent_id": life.id}
        )
        assert refused.code == "invalid_parent_kind"
        assert "Subgoals under it" in str(refused)


async def test_cd_tree_003_a_subgoal_belongs_to_a_goal(sessions):
    """CD-TREE-003 — tests/brd/cards.feature"""
    async with sessions() as session:
        health = await create_card(session, kind="goal", title="Health")
        sleep = await create_card(
            session, kind="subgoal", title="Sleep better", parent_id=health.id
        )
        assert sleep.parent_id == health.id

        with pytest.raises(DomainError, match="only be placed under a Goal"):
            await create_card(session, kind="subgoal", title="Nap daily", parent_id=sleep.id)
        with pytest.raises(DomainError, match="only be placed under a Goal"):
            await create_card(session, kind="subgoal", title="Read more")
        with pytest.raises(DomainError, match="only be placed under a Goal"):
            await set_card_parent(session, sleep.id, None)


async def test_cd_tree_004_an_action_sits_under_a_goal_a_subgoal_or_nothing(sessions):
    """CD-TREE-004 — tests/brd/cards.feature"""
    async with sessions() as session:
        health = await create_card(session, kind="goal", title="Health")
        sleep = await create_card(session, kind="subgoal", title="Sleep better", parent_id=health.id)
        pillow = await create_card(
            session, kind="action", title="Buy a pillow", effort_points=2, parent_id=health.id
        )
        await session.commit()

        await set_card_parent(session, pillow.id, sleep.id)
        assert pillow.parent_id == sleep.id
        await set_card_parent(session, pillow.id, None)
        assert pillow.parent_id is None

        for kind, extra in (("subgoal", {}), ("action", {"effort_points": 1})):
            with pytest.raises(DomainError, match="cannot have children"):
                await create_card(
                    session, kind=kind, title="Underneath", parent_id=pillow.id, **extra
                )


async def test_cd_tree_006_an_archived_or_missing_parent_is_refused(sessions):
    """CD-TREE-006 — tests/brd/cards.feature"""
    async with sessions() as session:
        gone = await create_card(session, kind="goal", title="Old plan")
        abandoned = await create_card(
            session, kind="action", title="Sort the shed", effort_points=1, parent_id=gone.id
        )
        live = await create_card(session, kind="goal", title="Health")
        action = await create_card(
            session, kind="action", title="Walk", effort_points=2, parent_id=live.id
        )
        # Only a closed Card may be archived, and a Goal closes through its Actions.
        await finish_action(session, abandoned.id)
        await archive_subtree(session, gone.id)
        await session.commit()

        with pytest.raises(DomainError, match="does not exist or is archived"):
            await create_card(session, kind="action", title="Sort", effort_points=1, parent_id=gone.id)
        with pytest.raises(DomainError, match="does not exist or is archived"):
            await set_card_parent(session, action.id, gone.id)
        with pytest.raises(DomainError, match="does not exist or is archived"):
            await set_card_parent(session, action.id, 9999)

        assert action.parent_id == live.id


async def test_cd_field_007_a_goal_is_saved_without_the_fields_that_are_an_actions(sessions):
    """CD-FIELD-007 — tests/brd/cards.feature"""
    async with sessions() as session:
        goal = await create_card(
            session,
            kind="goal",
            title="Release VrWalk",
            effort_points=5,
            repeatable=True,
            categories={"work"},
            energy_types={"cognitive"},
            blocked=True,
            blocked_description="Waiting for the store",
        )
        await session.flush()

        assert goal.effort_points is None
        assert goal.repeatable is False
        assert goal.blocked is False
        assert goal.blocked_description == ""
        assert not list(
            await session.scalars(select(CardCategory).where(CardCategory.card_id == goal.id))
        )
        assert not list(
            await session.scalars(select(CardEnergyType).where(CardEnergyType.card_id == goal.id))
        )

        # The domain door is the backstop: what the other doors drop, this one refuses.
        with pytest.raises(DomainError, match="Action-only fields"):
            await update_card_fields(session, goal.id, {"blocked": True, "blocked_description": "x"})


async def test_cd_field_007_a_proposal_drops_them_and_refuses_a_change_that_was_only_them(sessions):
    """CD-FIELD-007 — tests/brd/cards.feature"""
    async with sessions() as session:
        created = await ChangePreparer(None, None, PROPOSALS).prepare(  # type: ignore[arg-type]
            session,
            PROPOSALS.change_from_tool(
                "card",
                {
                    "mode": "create",
                    "kind": "goal",
                    "title": "Release VrWalk",
                    "effort_points": 5,
                    "blocked": True,
                    "blocked_description": "Waiting for the store",
                },
            ),
        )
        assert created.values.get("blocked") is None
        assert created.values.get("effort_points") is None
        assert created.values["title"] == "Release VrWalk"

        goal = await create_card(session, kind="goal", title="Ship it")
        await session.commit()
        with pytest.raises(DomainError, match="no applicable fields"):
            await ChangePreparer(None, None, PROPOSALS).prepare(  # type: ignore[arg-type]
                session,
                PROPOSALS.change_from_tool(
                    "card",
                    {
                        "mode": "update",
                        "id": goal.id,
                        "blocked": True,
                        "blocked_description": "Waiting",
                    },
                ),
            )


async def test_cd_effort_008_an_action_needs_a_size_from_the_one_scale(sessions):
    """CD-EFFORT-008 — tests/brd/cards.feature"""
    async with sessions() as session:
        with pytest.raises(DomainError, match="effort points"):
            await create_card(session, kind="action", title="Run")
        off_the_scale = max(EFFORT_POINTS) - 1
        assert off_the_scale not in EFFORT_POINTS
        with pytest.raises(DomainError, match="effort points"):
            await create_card(session, kind="action", title="Run", effort_points=off_the_scale)

        for points in sorted(EFFORT_POINTS):
            card = await create_card(
                session, kind="action", title=f"Run {points}", effort_points=points
            )
            assert card.effort_points == points


async def test_cd_title_009_a_card_has_to_be_called_something(sessions):
    """CD-TITLE-009 — tests/brd/cards.feature"""
    async with sessions() as session:
        with pytest.raises(DomainError, match="title cannot be empty"):
            await create_card(session, kind="action", title="   ", effort_points=1)

        card = await create_card(session, kind="action", title="  Run  ", effort_points=1)
        assert card.title == "Run"
        await session.commit()

        with pytest.raises(DomainError, match="title cannot be empty"):
            await edit_card_text(session, card.id, "title", "  ")
        with pytest.raises(DomainError, match="title cannot be empty"):
            await update_card_fields(session, card.id, {"title": " "})

        assert (await session.get(Card, card.id)).title == "Run"


async def test_cd_blocked_010_a_blocked_action_says_why_and_unblocking_clears_it(sessions):
    """CD-BLOCKED-010 — tests/brd/cards.feature"""
    async with sessions() as session:
        with pytest.raises(DomainError, match="blocked description"):
            await create_card(session, kind="action", title="Waiting", effort_points=1, blocked=True)

        card = await create_card(
            session,
            kind="action",
            title="Waiting",
            effort_points=1,
            blocked=True,
            blocked_description="Need account access",
        )
        await update_card_fields(session, card.id, {"blocked": False})

        assert card.blocked is False
        assert card.blocked_description == ""


async def test_cd_stage_011_a_new_card_starts_in_the_backlog(sessions):
    """CD-STAGE-011 — tests/brd/cards.feature"""
    async with sessions() as session:
        card = await create_card(session, kind="action", title="Run", effort_points=2)
        assert card.manual_stage == CardStage.BACKLOG.value
        assert card.effective_stage == CardStage.BACKLOG.value

        with pytest.raises(DomainError, match="must start in Backlog"):
            await create_card(
                session, kind="action", title="Already over", effort_points=2,
                stage=CardStage.DONE,
            )


async def test_cd_link_012_a_card_is_created_with_all_of_its_links_or_not_at_all(sessions):
    """CD-LINK-012 — tests/brd/cards.feature"""
    async with sessions() as session:
        health = await create_value(session, "Health")
        home = await create_tag(session, "home")
        slept = await create_check(session, title="Slept 7 hours")
        await session.commit()

        card = await create_card(
            session,
            kind=CardKind.ACTION,
            title="Sleep 8 hours",
            effort_points=2,
            categories={"self"},
            energy_types={"physical"},
            value_ids={health.id},
            tag_ids={home.id},
            check_ids={slept.id},
        )
        await session.commit()

    async with sessions() as session:
        stored = await session.get(Card, card.id)
        assert stored is not None
        assert stored.title == "Sleep 8 hours"
        assert await session.get(CardCategory, {"card_id": card.id, "category": "self"}) is not None
        assert (
            await session.get(CardEnergyType, {"card_id": card.id, "energy_type": "physical"})
            is not None
        )
        assert await session.get(CardValue, {"card_id": card.id, "value_id": health.id}) is not None

        await delete_value(session, health.id)
        await session.commit()

        with pytest.raises(DomainError) as refused:
            await create_card(
                session,
                kind=CardKind.ACTION,
                title="Sleep 9 hours",
                effort_points=2,
                value_ids={health.id},
                tag_ids={home.id},
                check_ids={slept.id},
            )
        assert f"#{health.id}" in str(refused.value)

        with pytest.raises(DomainError, match="#9999"):
            await create_card(
                session,
                kind=CardKind.ACTION,
                title="Sleep 9 hours",
                effort_points=2,
                tag_ids={9999},
            )

    async with sessions() as session:
        assert await session.scalar(select(func.count(Card.id))) == 1
        assert await session.scalar(select(func.count(CardTag.card_id))) == 1
        assert await session.scalar(select(func.count(CardCheck.card_id))) == 1


# --------------------------------------------------------- stages, blocked, effort


async def _goal_with_action(session, **overrides):
    goal = await create_card(session, kind="goal", title=overrides.pop("goal_title", "Health"))
    action = await create_card(
        session,
        kind="action",
        title=overrides.pop("title", "Walk"),
        effort_points=overrides.pop("effort_points", 3),
        parent_id=goal.id,
        **overrides,
    )
    return goal, action


async def _refused_proposal(session, arguments):
    change = PROPOSALS.change_from_tool("card", arguments)
    with pytest.raises(ToolPreparationError) as refused:
        await ChangePreparer(None, None, PROPOSALS).prepare(session, change)  # type: ignore[arg-type]
    return refused.value


async def test_cd_stage_013_only_an_action_has_a_stage(sessions):
    """CD-STAGE-013 — tests/brd/cards.feature"""
    async with sessions() as session:
        goal = await create_card(session, kind="goal", title="Health")
        subgoal = await create_card(session, kind="subgoal", title="Sleep better", parent_id=goal.id)
        action = await create_card(
            session, kind="action", title="Buy a pillow", effort_points=2, parent_id=subgoal.id
        )
        await session.commit()

        await move_card(session, action.id, CardStage.TODAY)
        assert (await session.get(Card, action.id)).effective_stage == CardStage.TODAY.value

        for parent in (goal, subgoal):
            with pytest.raises(DomainError, match="Only an Action has a stage"):
                await move_card(session, parent.id, CardStage.SPRINT)
            refused = await _refused_proposal(
                session, {"mode": "move", "id": parent.id, "stage": "sprint"}
            )
            assert refused.code == "stage_is_action_only"
            refused = await _refused_proposal(session, {"mode": "complete", "id": parent.id})
            assert refused.code == "stage_is_action_only"

        # A stage asked for at creation is dropped rather than refused, like effort.
        born = await create_card(session, kind="goal", title="Fitness", stage="today")
        await session.commit()
        assert born.effective_stage == CardStage.BACKLOG.value


async def test_cd_stage_014_a_goal_shows_the_stage_of_the_actions_under_it(sessions):
    """CD-STAGE-014 — tests/brd/cards.feature"""
    async with sessions() as session:
        goal = await create_card(session, kind="goal", title="Health")
        subgoal = await create_card(session, kind="subgoal", title="Sleep better", parent_id=goal.id)
        backlog = await create_card(
            session, kind="action", title="Buy a pillow", effort_points=2, parent_id=subgoal.id
        )
        sprinting = await create_card(
            session, kind="action", title="Book the doctor", effort_points=2, parent_id=subgoal.id
        )
        await session.commit()

        await move_card(session, sprinting.id, CardStage.SPRINT)
        await session.commit()
        assert (await session.get(Card, goal.id)).effective_stage == CardStage.SPRINT.value

        await move_card(session, backlog.id, CardStage.TODAY)
        await session.commit()
        # Every Card between the Action and the root shows it, not the direct parent alone.
        assert (await session.get(Card, subgoal.id)).effective_stage == CardStage.TODAY.value
        assert (await session.get(Card, goal.id)).effective_stage == CardStage.TODAY.value

        await move_card(session, backlog.id, CardStage.BACKLOG)
        await session.commit()
        assert (await session.get(Card, goal.id)).effective_stage == CardStage.SPRINT.value

        empty = await create_card(session, kind="goal", title="Someday")
        await create_card(session, kind="subgoal", title="Nothing yet", parent_id=empty.id)
        await session.commit()
        assert (await session.get(Card, empty.id)).effective_stage == CardStage.BACKLOG.value


async def test_cd_stage_015_a_goal_is_done_only_when_all_its_actions_are_finished(sessions):
    """CD-STAGE-015 — tests/brd/cards.feature"""
    async with sessions() as session:
        goal = await create_card(session, kind="goal", title="Health")
        first = await create_card(
            session, kind="action", title="Walk", effort_points=2, parent_id=goal.id
        )
        second = await create_card(
            session, kind="action", title="Swim", effort_points=2, parent_id=goal.id
        )
        await session.commit()

        await finish_action(session, first.id)
        await session.commit()
        # One live Action left, so the Goal shows what that one is in.
        assert (await session.get(Card, goal.id)).effective_stage == CardStage.BACKLOG.value

        await finish_action(session, second.id)
        await session.commit()
        assert (await session.get(Card, goal.id)).effective_stage == CardStage.DONE.value

        empty = await create_card(session, kind="goal", title="Someday")
        await session.commit()
        assert (await session.get(Card, empty.id)).effective_stage == CardStage.BACKLOG.value


async def test_cd_stage_015_an_empty_subgoal_holds_its_goal_out_of_done(sessions):
    """CD-STAGE-015 — tests/brd/cards.feature"""
    async with sessions() as session:
        goal = await create_card(session, kind="goal", title="Health")
        walk = await create_card(
            session, kind="action", title="Walk", effort_points=2, parent_id=goal.id
        )
        subgoal = await create_card(session, kind="subgoal", title="Sleep better", parent_id=goal.id)
        await session.commit()

        await finish_action(session, walk.id)
        await session.commit()
        # The Subgoal has nothing in it, so it is in Backlog, and its Goal is not finished.
        assert (await session.get(Card, subgoal.id)).effective_stage == CardStage.BACKLOG.value
        assert (await session.get(Card, goal.id)).effective_stage == CardStage.BACKLOG.value

        pillow = await create_card(
            session, kind="action", title="Buy a pillow", effort_points=1, parent_id=subgoal.id
        )
        await finish_action(session, pillow.id)
        await session.commit()
        assert (await session.get(Card, goal.id)).effective_stage == CardStage.DONE.value

        # A child written under a finished Goal takes it back out of Done.
        await create_card(session, kind="subgoal", title="Nothing yet", parent_id=goal.id)
        await session.commit()
        assert (await session.get(Card, goal.id)).effective_stage == CardStage.BACKLOG.value


async def test_cd_stage_016_done_comes_from_finishing_not_from_moving(sessions):
    """CD-STAGE-016 — tests/brd/cards.feature"""
    async with sessions() as session:
        action = await create_card(
            session, kind="action", title="Walk", effort_points=2, stage="today"
        )
        await session.commit()

        with pytest.raises(DomainError, match="through finish_action"):
            await move_card(session, action.id, CardStage.DONE)
        assert (await session.get(Card, action.id)).effective_stage == CardStage.TODAY.value

        # Finishing settles the Card, its Check and its Sprint result in the same act.
        check = await create_check(session, title="Did it help?")
        await toggle_card_check(session, action.id, check.id)
        await session.commit()
        await finish_action(
            session, action.id, check_outcomes={check.id: CheckOutcome.PASSED}
        )
        await session.commit()
        assert (await session.get(Card, action.id)).completed_at is not None
        assert (await session.get(Check, check.id)).outcome == CheckOutcome.PASSED.value


async def test_cd_stage_017_reopening_an_action_undoes_what_closing_it_did(sessions):
    """CD-STAGE-017 — tests/brd/cards.feature"""
    async with sessions() as session:
        goal, action = await _goal_with_action(session)
        await session.commit()

        await finish_action(session, action.id)
        await session.commit()
        assert (await session.get(Card, goal.id)).effective_stage == CardStage.DONE.value

        await move_card(session, action.id, CardStage.TODAY)
        await session.commit()

        reopened = await session.get(Card, action.id)
        assert reopened.completed_at is None
        assert (await session.get(Card, goal.id)).effective_stage == CardStage.TODAY.value

        repeating = await create_card(
            session, kind="action", title="Posture", effort_points=1, stage="today", repeatable=True
        )
        await finish_action(session, repeating.id)
        await session.commit()
        with pytest.raises(DomainError, match="closed repeating Action cannot be reopened"):
            await move_card(session, repeating.id, CardStage.TODAY)


async def test_cd_blocked_018_only_an_action_can_be_marked_blocked(sessions):
    """CD-BLOCKED-018 — tests/brd/cards.feature"""
    async with sessions() as session:
        goal = await create_card(session, kind="goal", title="Health")
        subgoal = await create_card(session, kind="subgoal", title="Sleep better", parent_id=goal.id)
        action = await create_card(
            session, kind="action", title="Buy a pillow", effort_points=2, parent_id=subgoal.id
        )
        await session.commit()

        await update_card_fields(
            session, action.id, {"blocked": True, "blocked_description": "Shop is shut"}
        )
        await session.commit()
        assert (await session.get(Card, action.id)).blocked_description == "Shop is shut"

        for parent in (goal, subgoal):
            with pytest.raises(DomainError, match="Action-only fields"):
                await update_card_fields(
                    session, parent.id, {"blocked": True, "blocked_description": "Waiting"}
                )
            change = PROPOSALS.change_from_tool(
                "card",
                {"mode": "update", "id": parent.id, "blocked": True, "blocked_description": "x"},
            )
            with pytest.raises(DomainError, match="no applicable fields"):
                await ChangePreparer(None, None, PROPOSALS).prepare(session, change)  # type: ignore[arg-type]


async def test_cd_blocked_019_a_goal_shows_the_blocked_actions_under_it(sessions):
    """CD-BLOCKED-019 — tests/brd/cards.feature"""
    async with sessions() as session:
        goal = await create_card(session, kind="goal", title="Health")
        subgoal = await create_card(session, kind="subgoal", title="Sleep better", parent_id=goal.id)
        action = await create_card(
            session,
            kind="action",
            title="Buy a pillow",
            effort_points=2,
            parent_id=subgoal.id,
            blocked=True,
            blocked_description="Shop is shut",
        )
        await session.commit()

        for parent in (goal, subgoal):
            stored = await session.get(Card, parent.id)
            assert stored.blocked is True
            # A Goal has no reason of its own; the screen quotes the Actions instead.
            assert stored.blocked_description == ""
        named = await blocking_actions(session, goal.id)
        assert [(item.title, item.blocked_description) for item in named] == [
            ("Buy a pillow", "Shop is shut")
        ]

        await update_card_fields(session, action.id, {"blocked": False})
        await session.commit()
        assert (await session.get(Card, goal.id)).blocked is False

        await update_card_fields(
            session, action.id, {"blocked": True, "blocked_description": "Shop is shut"}
        )
        await finish_action(session, action.id)
        await session.commit()
        # A finished Action is not something the branch is waiting on.
        assert (await session.get(Card, goal.id)).blocked is False


async def test_cd_hardtime_033_a_hard_time_is_a_schedule_and_what_fixes_it(sessions):
    """CD-HARDTIME-033 — tests/brd/cards.feature"""
    zone = ZoneInfo("Europe/Istanbul")
    async with sessions() as session:
        clinic = await create_card(
            session,
            kind="action",
            title="Call the clinic",
            effort_points=1,
            hard_time=await typed_hard_time(session, "Mon Wed 09:00"),
            hard_time_description="They only answer in the morning",
        )
        plain = await create_card(session, kind="action", title="Read", effort_points=1)
        await session.commit()

        # The schedule is a Reminder's, and the next occurrence is a Monday or a Wednesday.
        assert clinic.hard_time["schedule_kind"] == "weekly"
        assert clinic.hard_time["weekdays"] == ["Mon", "Wed"]
        local = clinic.hard_time_at.astimezone(zone)
        assert (local.strftime("%a"), local.strftime("%H:%M")) in {("Mon", "09:00"), ("Wed", "09:00")}
        assert clinic.hard_time_description == "They only answer in the morning"
        assert clinic.hard_time_at > datetime.now(UTC)

        with pytest.raises(DomainError, match="Cannot read"):
            await typed_hard_time(session, "sometime soon")
        with pytest.raises(DomainError, match="need a time"):
            await typed_hard_time(session, "Mon")

        # Sorted: the Hard Time first, the sooner of two first.
        later = await create_card(
            session,
            kind="action",
            title="Dentist",
            effort_points=1,
            hard_time=await typed_hard_time(session, "31.12.2099 10:00"),
        )
        assert [card.title for card in paginate_cards([plain, later, clinic], 0).items] == [
            "Call the clinic",
            "Dentist",
            "Read",
        ]

        # Removing the Hard Time takes the description with it.
        await update_card_fields(session, clinic.id, {"hard_time": None})
        assert (clinic.hard_time, clinic.hard_time_at, clinic.hard_time_description) == (
            None,
            None,
            "",
        )

        # A repeating schedule carries to the next instance at its next occurrence; one
        # moment does not.
        daily = await create_card(
            session,
            kind="action",
            title="Pills",
            effort_points=0.5,
            repeatable=True,
            hard_time=await typed_hard_time(session, "daily 09:00"),
            hard_time_description="With breakfast",
        )
        once = await create_card(
            session,
            kind="action",
            title="Renew the passport",
            effort_points=2,
            repeatable=True,
            hard_time=await typed_hard_time(session, "31.12.2099 10:00"),
        )
        first_at = daily.hard_time_at
        next_daily = await session.get(
            Card, (await finish_action(session, daily.id)).successor_ids[0]
        )
        next_once = await session.get(
            Card, (await finish_action(session, once.id)).successor_ids[0]
        )
        assert next_daily.hard_time_at > first_at
        assert next_daily.hard_time_at.astimezone(zone).strftime("%H:%M") == "09:00"
        assert next_daily.hard_time_description == "With breakfast"
        assert (next_once.hard_time, next_once.hard_time_at) == (None, None)


async def test_cd_blocked_020_being_blocked_does_not_stop_anything(sessions):
    """CD-BLOCKED-020 — tests/brd/cards.feature"""
    async with sessions() as session:
        action = await create_card(
            session,
            kind="action",
            title="Buy a pillow",
            effort_points=2,
            stage="today",
            blocked=True,
            blocked_description="Shop is shut",
        )
        await session.commit()

        moved = await move_card(session, action.id, CardStage.SPRINT)
        assert moved.warnings == ["Blocked: Shop is shut"]
        finished = await finish_action(session, action.id)
        await session.commit()

        assert finished.warnings == ["Blocked: Shop is shut"]
        assert (await session.get(Card, action.id)).effective_stage == CardStage.DONE.value


async def test_cd_effort_021_a_goal_shows_the_effort_of_the_actions_under_it(sessions):
    """CD-EFFORT-021 — tests/brd/cards.feature"""
    async with sessions() as session:
        goal = await create_card(session, kind="goal", title="Health")
        subgoal = await create_card(session, kind="subgoal", title="Sleep better", parent_id=goal.id)
        first = await create_card(
            session, kind="action", title="One", effort_points=5, parent_id=goal.id
        )
        await create_card(session, kind="action", title="Two", effort_points=5, parent_id=subgoal.id)
        await create_card(session, kind="action", title="Three", effort_points=5, parent_id=subgoal.id)
        await session.commit()

        assert (await session.get(Card, goal.id)).effort_points == 15
        assert (await session.get(Card, subgoal.id)).effort_points == 10

        await update_card_fields(session, first.id, {"effort_points": 8})
        await session.commit()
        assert (await session.get(Card, goal.id)).effort_points == 18
        assert (await session.get(Card, subgoal.id)).effort_points == 10

        for parent in (goal, subgoal):
            with pytest.raises(DomainError, match="Action-only fields"):
                await update_card_fields(session, parent.id, {"effort_points": 3})

        empty = await create_card(session, kind="goal", title="Someday")
        await session.commit()
        assert (await session.get(Card, empty.id)).effort_points is None


# ------------------------------------------------------------ archiving and deleting


async def test_cd_archive_022_a_closed_card_is_archived_two_sprints_later(sessions):
    """CD-ARCHIVE-022 — tests/brd/cards.feature"""
    async with sessions() as session:
        goal, action = await _goal_with_action(session, title="Walk")
        # A Sprint needs an Action planned into it, and this one is also what stays live.
        open_goal, live = await _goal_with_action(
            session, goal_title="Fitness", title="Swim", stage="sprint"
        )
        await finish_action(session, action.id)
        await session.commit()

        for index in (1, 2):
            await start_sprint(session, success_criteria=f"Sprint {index}")
            await finish_sprint(session)
            await session.commit()
        assert (await session.get(Card, action.id)).archived_at is None

        await start_sprint(session, success_criteria="Sprint 3")
        await finish_sprint(session)
        await session.commit()

        assert (await session.get(Card, action.id)).archived_at is not None
        # The Goal above it went with the branch it had nothing left in.
        assert (await session.get(Card, goal.id)).archived_at is not None
        assert (await session.get(Card, live.id)).archived_at is None
        assert (await session.get(Card, open_goal.id)).archived_at is None


async def test_cd_archive_023_an_archived_card_is_marked_not_left_out(sessions):
    """CD-ARCHIVE-023 — tests/brd/cards.feature"""
    async with sessions() as session:
        goal = await create_card(session, kind="goal", title="Health")
        hidden = await create_card(
            session, kind="action", title="Walk", effort_points=5, parent_id=goal.id
        )
        shown = await create_card(
            session, kind="action", title="Swim", effort_points=3, parent_id=goal.id
        )
        await finish_action(session, hidden.id)
        await finish_action(session, shown.id)
        await archive_subtree(session, hidden.id)
        await session.commit()

        assert (await session.get(Card, hidden.id)).archived_at is not None
        assert (await session.get(Card, goal.id)).archived_at is None
        # Archiving is a matter of sight: the effort and the completion still count.
        assert (await session.get(Card, goal.id)).effort_points == 8
        assert (await card_progress(session, goal.id))["completed_effort"] == 8
        assert (await session.get(Card, goal.id)).effective_stage == CardStage.DONE.value
        # The Goal lists both children and marks the archived one, rather than dropping it.
        assert [item.id for item in await card_children(session, goal.id)] == [
            hidden.id,
            shown.id,
        ]
        assert await title_marks(session, hidden) == " [📦]"
        assert await title_marks(session, shown) == ""


async def test_cd_archive_024_only_a_closed_card_can_be_archived_by_hand(sessions):
    """CD-ARCHIVE-024 — tests/brd/cards.feature"""
    async with sessions() as session:
        action = await create_card(
            session, kind="action", title="Walk", effort_points=2, stage="today"
        )
        await session.commit()

        with pytest.raises(DomainError, match="Done may be archived"):
            await archive_subtree(session, action.id)

        await finish_action(session, action.id)
        await archive_subtree(session, action.id)
        await session.commit()
        assert (await session.get(Card, action.id)).archived_at is not None

        # Reopening it takes it out of the archive and back onto the screens.
        await move_card(session, action.id, CardStage.TODAY)
        await session.commit()
        assert (await session.get(Card, action.id)).archived_at is None

        repeating = await create_card(
            session, kind="action", title="Posture", effort_points=1, stage="today", repeatable=True
        )
        await finish_action(session, repeating.id)
        await archive_subtree(session, repeating.id)
        await session.commit()
        with pytest.raises(DomainError, match="closed repeating Action cannot be reopened"):
            await move_card(session, repeating.id, CardStage.TODAY)
        assert (await session.get(Card, repeating.id)).archived_at is not None


async def test_cd_archive_024_an_unfinished_child_keeps_its_goal_out_of_the_archive(sessions):
    """CD-ARCHIVE-024 — tests/brd/cards.feature"""
    async with sessions() as session:
        goal = await create_card(session, kind="goal", title="Health")
        walk = await create_card(
            session, kind="action", title="Walk", effort_points=2, parent_id=goal.id
        )
        subgoal = await create_card(session, kind="subgoal", title="Sleep better", parent_id=goal.id)
        await session.commit()
        await finish_action(session, walk.id)
        await session.commit()

        # The Goal is not Done while the Subgoal is in Backlog, so nothing under it is archived
        # either: a Card in Backlog can never reach the archive by being dragged there.
        with pytest.raises(DomainError, match="Done may be archived"):
            await archive_subtree(session, goal.id)
        assert (await session.get(Card, subgoal.id)).archived_at is None


async def test_cd_archive_024_a_live_card_takes_its_branch_out_of_the_archive(sessions):
    """CD-ARCHIVE-024 — tests/brd/cards.feature"""
    async with sessions() as session:
        goal = await create_card(session, kind="goal", title="Health")
        subgoal = await create_card(session, kind="subgoal", title="Move more", parent_id=goal.id)
        walk = await create_card(
            session, kind="action", title="Walk", effort_points=2, parent_id=subgoal.id
        )
        swim = await create_card(
            session, kind="action", title="Swim", effort_points=3, parent_id=subgoal.id
        )
        await finish_action(session, walk.id)
        await finish_action(session, swim.id)
        await archive_subtree(session, walk.id)
        await session.commit()
        # One Action still in sight keeps the whole branch above it in sight.
        assert (await session.get(Card, subgoal.id)).archived_at is None
        assert (await session.get(Card, goal.id)).archived_at is None
    ids = (goal.id, subgoal.id, walk.id, swim.id)

    # A later session reads the first stamp back out of the database, so the branch is
    # worked out from one stamp that came from a row and one straight off the clock.
    async with sessions() as session:
        await archive_subtree(session, ids[3])
        await session.commit()
        assert (await session.get(Card, ids[1])).archived_at is not None
        assert (await session.get(Card, ids[0])).archived_at is not None

    async with sessions() as session:
        await move_card(session, ids[3], CardStage.TODAY)
        await session.commit()
        assert (await session.get(Card, ids[1])).archived_at is None
        assert (await session.get(Card, ids[0])).archived_at is None
        assert (await session.get(Card, ids[2])).archived_at is not None


async def test_cd_delete_025_deleting_a_card_deletes_everything_under_it(sessions):
    """CD-DELETE-025 — tests/brd/cards.feature"""
    async with sessions() as session:
        goal = await create_card(session, kind="goal", title="Health")
        subgoal = await create_card(session, kind="subgoal", title="Sleep better", parent_id=goal.id)
        live = await create_card(
            session, kind="action", title="Buy a pillow", effort_points=2, parent_id=subgoal.id
        )
        closed = await create_card(
            session, kind="action", title="Book the doctor", effort_points=1, parent_id=goal.id
        )
        value = await create_value(session, "Health")
        tag = await create_tag(session, "home")
        await toggle_card_value(session, live.id, value.id)
        await toggle_card_tag(session, live.id, tag.id)
        plain = await create_check(session, title="Slept seven hours?")
        measuring = await create_check(session, title="Rested?")
        await toggle_card_check(session, live.id, plain.id)
        await toggle_card_check(session, closed.id, measuring.id)
        await toggle_check_value(session, measuring.id, value.id)
        await finish_action(
            session, closed.id, check_outcomes={measuring.id: CheckOutcome.PASSED}
        )
        await archive_subtree(session, closed.id)
        await session.commit()

        removed = await delete_subtree(session, goal.id)
        await session.commit()

        assert removed == 4
        for card_id in (goal.id, subgoal.id, live.id, closed.id):
            assert await session.get(Card, card_id) is None
        # A Check with no Value goes with its Card; one that measures a Value stays.
        assert await session.get(Check, plain.id) is None
        assert await session.get(Check, measuring.id) is not None
        assert await check_card_id(session, measuring.id) is None
        # The Values and Tags they carried lose the link and nothing else.
        assert await session.get(Value, value.id) is not None
        assert await session.get(Tag, tag.id) is not None
        assert list(await session.scalars(select(CardValue.card_id))) == []
        assert list(await session.scalars(select(CardTag.card_id))) == []
        assert list(await session.scalars(select(SprintCommitment.card_id))) == []
        assert list(await session.scalars(select(CardEvent.card_id))) == []


async def test_cd_repeat_026_a_closed_repeat_names_its_place_and_the_open_one(sessions):
    """CD-REPEAT-026 — tests/brd/cards.feature"""
    async with sessions() as session:
        first = await create_card(session, kind="action", title="Run", effort_points=2, repeatable=True, stage="today")
        result = await finish_action(session, first.id)
        second = await session.get(Card, result.successor_ids[0])
        result = await finish_action(session, second.id)
        third = await session.get(Card, result.successor_ids[0])
        await session.commit()

        assert await title_marks(session, first) == f" [🔄1, live #{third.id}]"
        assert await title_marks(session, second) == f" [🔄2, live #{third.id}]"
        # The open one is named plainly, and that is what says it is the one to work with.
        assert await title_marks(session, third) == ""

        plain = await create_card(session, kind="action", title="Once", effort_points=1, stage="today")
        await finish_action(session, plain.id)
        await session.commit()
        assert await title_marks(session, plain) == ""

        # With the open one deleted the series has ended, so the marker names no id.
        await delete_subtree(session, third.id)
        await session.commit()
        assert await title_marks(session, second) == " [🔄2]"


async def test_cd_repeat_026_the_views_name_the_series_and_the_open_one(read_views):
    """CD-REPEAT-026 — tests/brd/cards.feature"""
    sessions, runner = read_views
    async with sessions() as session:
        first = await create_card(session, kind="action", title="Run", effort_points=2, repeatable=True, stage="today")
        result = await finish_action(session, first.id)
        plain = await create_card(session, kind="action", title="Once", effort_points=1, stage="today")
        await session.commit()
        first_id, second_id, plain_id = first.id, result.successor_ids[0], plain.id

    rows = (await runner.run("SELECT id, title, series_id FROM ai_cards ORDER BY id")).rows
    by_id = {row["id"]: row for row in rows}

    assert by_id[first_id]["series_id"] == by_id[second_id]["series_id"] == first_id
    # A Card that never repeated is a series of one, named after itself rather than left
    # nameless, so grouping by the series never drops it into a bucket with the others.
    assert by_id[plain_id]["series_id"] == plain_id
    assert by_id[first_id]["title"] == f"Run [🔄1, live #{second_id}]"
    assert by_id[second_id]["title"] == "Run"


async def test_cd_archive_023_an_archived_card_is_marked_and_still_listed(read_views):
    """CD-ARCHIVE-023 — tests/brd/cards.feature"""
    sessions, runner = read_views
    async with sessions() as session:
        goal = await create_card(session, kind="goal", title="Health")
        kept = await create_card(
            session, kind="action", title="Kept", effort_points=2, stage="today", parent_id=goal.id
        )
        gone = await create_card(
            session, kind="action", title="Gone", effort_points=3, stage="today", parent_id=goal.id
        )
        await finish_action(session, kept.id)
        await finish_action(session, gone.id)
        await archive_subtree(session, gone.id)
        await session.commit()

        assert {child.id for child in await card_children(session, goal.id)} == {kept.id, gone.id}
        assert await title_marks(session, gone) == " [📦]"
        # The effort of both is still counted, archived or not.
        assert (await session.get(Card, goal.id)).effort_points == 5
        gone_id = gone.id

    rows = (await runner.run("SELECT id, title FROM ai_cards ORDER BY id")).rows
    assert {row["id"]: row["title"] for row in rows}[gone_id] == "Gone [📦]"




async def a_card(session, **overrides):
    """An Action with every required field filled, for a test that is about the tree."""
    payload = {"title": "Action", "kind": "action", "stage": "backlog", "effort_points": 3}
    payload.update(overrides)
    payload.pop("root_confirmed", None)
    payload.pop("expected_parent_version", None)
    return await create_card(session, **payload)


async def test_parent_stage_propagation_and_reopen(sessions):
    async with sessions() as session:
        goal = await a_card(session, title="Goal", kind="goal", effort_points=None)
        action = await a_card(
            session,
            title="Child",
            parent_id=goal.id,
            expected_parent_version=goal.version,
            root_confirmed=False,
        )
        await session.commit()
        await move_card(session, action.id, CardStage.TODAY)
        assert goal.effective_stage == CardStage.TODAY.value
        await finish_action(session, action.id)
        assert goal.effective_stage == CardStage.DONE.value
        await move_card(session, action.id, CardStage.BACKLOG)
        assert goal.effective_stage == CardStage.BACKLOG.value


async def test_repeat_completion_clones_the_action(sessions):
    async with sessions() as session:
        card = await a_card(session, title="Run", repeatable=True, stage="today")
        result = await finish_action(session, card.id)
        await session.commit()
        successor = await session.get(Card, result.successor_ids[0])
        assert successor.effective_stage == CardStage.TODAY.value
        assert successor.repeat_series_id == card.repeat_series_id


async def test_a_closed_repeat_cannot_be_reopened_and_points_at_the_open_one(sessions):
    async with sessions() as session:
        first = await a_card(session, title="Run", repeatable=True, stage="today")
        result = await finish_action(session, first.id)
        second = await session.get(Card, result.successor_ids[0])
        result = await finish_action(session, second.id)
        third = await session.get(Card, result.successor_ids[0])
        await session.commit()

        with pytest.raises(DomainError, match="closed repeating Action"):
            await move_card(session, first.id, CardStage.TODAY)

        # Across three generations the answer is the newest open row, not the direct
        # successor: only the last one created can still be open.
        assert await live_repeat_instance_id(session, first) == third.id
        assert await live_repeat_instance_id(session, second) == third.id

        # Finishing the newest one carries the series on, so the answer follows to it.
        result = await finish_action(session, third.id)
        await session.commit()
        assert await live_repeat_instance_id(session, first) == result.successor_ids[0]

        # A non-repeating Card is not a series, so reopening it stays ordinary.
        plain = await a_card(session, title="Once", stage="today")
        await finish_action(session, plain.id)
        await move_card(session, plain.id, CardStage.TODAY)
        await session.commit()
        assert (await session.get(Card, plain.id)).effective_stage == CardStage.TODAY.value


async def test_ui_mutations_use_domain_services_and_are_audited(sessions):
    """VL-FOCUS-001 — tests/brd/values.feature"""
    async with sessions() as session:
        tag = await create_tag(session, "Personal")
        value = await create_value(session, "Consistency")
        await set_value_focus(session, value.id, True)
        card = await a_card(session, title="Original")
        assert await toggle_card_tag(session, card.id, tag.id) is True
        await edit_card_text(session, card.id, "title", "Renamed")
        await set_profile_field(
            session,
            ProfileField.ABOUT_ME,
            "Prefers calm, practical planning",
        )
        await finish_action(session, card.id)
        await archive_subtree(session, card.id)
        await session.commit()

        profile = await session.get(UserProfile, 1)
        assert profile.about_me == "Prefers calm, practical planning"
        assert (await session.get(Value, value.id)).active is True
        assert (await session.get(Card, card.id)).title == "Renamed"
        assert await session.get(CardTag, {"card_id": card.id, "tag_id": tag.id}) is not None
        events = list(await session.scalars(select(CardEvent).where(CardEvent.card_id == card.id)))
        assert {event.operation for event in events} >= {"edit_title", "archive"}


async def test_committed_card_relationships_are_validated_propagated_and_audited(sessions):
    """VL-LINK-004 — tests/brd/values.feature"""
    async with sessions() as session:
        first_goal = await a_card(session, title="First goal", kind="goal", effort_points=None)
        second_goal = await a_card(
            session, title="Second goal", kind="goal", effort_points=None
        )
        action = await a_card(
            session,
            title="Move me",
            parent_id=first_goal.id,
            expected_parent_version=first_goal.version,
            root_confirmed=False,
        )
        value = await create_value(session, "Health")
        await move_card(session, action.id, CardStage.TODAY)

        await set_card_parent(session, action.id, second_goal.id)
        assert action.parent_id == second_goal.id
        assert first_goal.effective_stage == CardStage.BACKLOG.value
        assert second_goal.effective_stage == CardStage.TODAY.value

        assert await toggle_card_value(session, action.id, value.id) is True
        assert await toggle_card_value(session, action.id, value.id) is False

        tag = Tag(name="Family")
        session.add(tag)
        await session.flush()
        assert await toggle_card_tag(session, action.id, tag.id) is True

        await update_card_fields(
            session,
            action.id,
            {"blocked": True, "blocked_description": "Waiting for access"},
        )
        assert action.blocked is True
        assert action.blocked_description == "Waiting for access"
        await session.commit()

        events = list(
            await session.scalars(select(CardEvent).where(CardEvent.card_id == action.id))
        )
        assert {event.operation for event in events} >= {
            "set_parent",
            "link_value",
            "unlink_value",
            "update",
        }


async def test_an_action_cannot_reach_a_terminal_stage_through_move(sessions):
    async with sessions() as session:
        action = await a_card(session, title="Ship", stage="today", effort_points=3)
        with pytest.raises(DomainError):
            await move_card(session, action.id, CardStage.DONE)
        assert action.effective_stage == CardStage.TODAY.value


async def test_goal_progress_is_recursive_but_children_count_is_direct(sessions):
    """CD-EFFORT-021 — tests/brd/cards.feature"""
    async with sessions() as session:
        goal = await a_card(session, title="Goal", kind="goal", effort_points=None)
        subgoal = await a_card(
            session,
            title="Subgoal",
            kind="subgoal",
            effort_points=None,
            parent_id=goal.id,
        )
        done = await a_card(
            session,
            title="Done",
            effort_points=3,
            parent_id=subgoal.id,
        )
        await a_card(
            session,
            title="Remaining",
            effort_points=5,
            parent_id=goal.id,
        )
        await finish_action(session, done.id)

        progress = await card_progress(session, goal.id)

        assert progress == {
            "completed_effort": 3,
            "completed_children": 1,
            "total_children": 2,
        }
        # The branch total is the Card's own effort now, so a query reads it too.
        assert (await session.get(Card, goal.id)).effort_points == 8
        assert (await session.get(Card, subgoal.id)).effort_points == 3


async def test_cd_context_028_only_the_critical_cards_still_to_do_are_handed_over(sessions):
    """CD-CONTEXT-028 — tests/brd/cards.feature"""
    async with sessions() as session:
        open_card = await create_card(
            session, title="Fix the roof", kind="action", stage="backlog",
            priority="critical", effort_points=3,
        )
        finished = await create_card(
            session, title="Shipped", kind="action", stage="today",
            priority="critical", effort_points=3,
        )
        await finish_action(session, finished.id)
        await create_card(
            session, title="Ordinary", kind="action", stage="backlog",
            priority="medium", effort_points=3,
        )
        await session.commit()

        state = (await workspace_context(session)).state

    listed = [line for line in state.splitlines() if line.startswith("- [")]

    # One line, and it carries the kind and the stage the Card is in now. What is finished
    # and what is not critical are both looked up when Safwa wants them.
    assert listed == [f"- [Fix the roof](card:{open_card.id}) kind=action stage=backlog"]
    assert "Shipped" not in state
    assert "Ordinary" not in state


def test_cd_effort_008_every_rung_says_what_it_costs():
    """CD-EFFORT-008 — tests/brd/cards.feature"""
    assert list(EFFORT_RUNGS) == [0.5, 1, 2, 3, 5, 8, 13]
    assert EFFORT_POINTS == frozenset(EFFORT_RUNGS)
    assert (effort_label(0.5), effort_label(13), effort_label(None)) == ("0.5", "13", "—")
    # One wording: the model's field description spells the scale the screens offer.
    described = CardToolInput.model_fields["effort_points"].description
    for points in EFFORT_RUNGS:
        assert f"{effort_label(points)} " in described
    # A rung is what it costs, never how long it takes.
    assert "min" not in described and "hour" not in described

async def test_cd_delete_025_deleting_a_goal_alone_leaves_its_children_standing(sessions):
    """CD-DELETE-025 — tests/brd/cards.feature"""
    async with sessions() as session:
        goal = await create_card(session, kind="goal", title="Health")
        subgoal = await create_card(
            session, kind="subgoal", title="Sleep better", parent_id=goal.id
        )
        action = await create_card(
            session, kind="action", title="Buy a pillow", effort_points=1, parent_id=goal.id
        )
        deep = await create_card(
            session, kind="action", title="Read at night", effort_points=1, parent_id=subgoal.id
        )
        await session.commit()

        assert await delete_one_card(session, goal.id) == 1
        await session.commit()

        assert await session.get(Card, goal.id) is None
        # A Subgoal cannot stand without a Goal, so losing one makes it a Goal itself.
        await session.refresh(subgoal)
        await session.refresh(action)
        assert (subgoal.kind, subgoal.parent_id) == (CardKind.GOAL.value, None)
        assert (action.kind, action.parent_id) == (CardKind.ACTION.value, None)
        # Whatever hung under the Subgoal never moved: only the deleted Card's children did.
        await session.refresh(deep)
        assert deep.parent_id == subgoal.id
        # The promotion is written down, so the history says why the kind changed.
        assert await session.scalar(
            select(CardEvent.operation).where(CardEvent.card_id == subgoal.id).order_by(
                CardEvent.id.desc()
            )
        ) == "edit_kind"


async def test_cd_blocked_034_becoming_blocked_is_the_change_a_hook_follows_up(sessions):
    """CD-BLOCKED-034 — tests/brd/cards.feature"""
    async with sessions() as session:
        born = await create_card(
            session, kind="action", title="Call the bank", effort_points=1,
            blocked=True, blocked_description="Line is busy",
        )
        marked = await create_card(session, kind="action", title="Sign the lease", effort_points=1)
        await update_card_fields(
            session, marked.id, {"blocked": True, "blocked_description": "Landlord away"}
        )
        assert take_changes(session.info) == [
            Committed(CARD_BLOCKED, born.id), Committed(CARD_BLOCKED, marked.id),
        ]
        # Staying blocked is not becoming blocked, and a Goal never blocks itself.
        await update_card_fields(session, marked.id, {"title": "Sign the new lease"})
        await update_card_fields(session, marked.id, {"blocked_description": "Landlord abroad"})
        await update_card_fields(session, marked.id, {"blocked": False})
        await update_card_fields(
            session, marked.id, {"blocked": True, "blocked_description": "Again"}
        )
        assert take_changes(session.info) == [Committed(CARD_BLOCKED, marked.id)]
        await session.commit()


async def test_cd_blocked_034_the_request_names_what_is_still_blocked_and_open(sessions):
    """CD-BLOCKED-034 — tests/brd/cards.feature"""
    assert BLOCKER_HOOK.agent_related
    # The switch is named where the Advisor reads it on every turn, not in the request.
    assert "switches off in Profile" in SYSTEM_PROMPT
    async with sessions() as session:
        blocked = [
            await create_card(
                session, kind="action", title=title, effort_points=1,
                blocked=True, blocked_description=reason,
            )
            for title, reason in (
                ("Call the bank", "Line is busy"), ("Sign the lease", "Landlord away"),
                ("Fix the bike", "No parts"), ("Read the contract", "Not received"),
                ("Renew the passport", "Office closed"),
            )
        ]
        await session.commit()
        ids = [card.id for card in blocked]
        await update_card_fields(session, ids[1], {"blocked": False})
        await finish_action(session, ids[2])
        await finish_action(session, ids[3])
        await archive_subtree(session, ids[3])
        await delete_one_card(session, ids[4])
        await update_card_fields(session, ids[0], {"blocked_description": "Line still busy"})
        await session.commit()

        request = await blocker_request(session, ids)
        assert request is not None
        assert f"#{ids[0]} «Call the bank»: Line still busy" in request
        assert "Reminder" in request
        for absent in ("Sign the lease", "Fix the bike", "Read the contract", "Renew the passport"):
            assert absent not in request
        assert await blocker_request(session, ids[1:]) is None


async def test_cd_empty_035_the_request_names_the_parents_old_enough_and_still_without_an_action(sessions):
    """CD-EMPTY-035 — tests/brd/cards.feature"""
    assert EMPTY_PARENTS_HOOK.agent_related
    assert EMPTY_PARENTS_HOOK.on == (OnTick(at=morning_time),)
    now = datetime(2026, 9, 16, 6, 0, tzinfo=UTC)
    old = now - timedelta(days=EMPTY_PARENT_GRACE_DAYS, hours=1)
    async with sessions() as session:
        goal = await create_card(session, kind="goal", title="Learn Spanish")
        subgoal = await create_card(session, kind="subgoal", title="Grammar", parent_id=goal.id)
        fixed = await create_card(session, kind="goal", title="Fix the bike")
        parts = await create_card(
            session, kind="action", title="Buy parts", effort_points=1, parent_id=fixed.id
        )
        fresh = await create_card(session, kind="goal", title="Started last evening")
        shelved = await create_card(session, kind="goal", title="Old idea")
        for card in (goal, subgoal, fixed, shelved):
            card.created_at = old
        # Younger than the grace, by the clock and not by the calendar.
        fresh.created_at = now - timedelta(days=EMPTY_PARENT_GRACE_DAYS) + timedelta(hours=1)
        shelved.archived_at = old
        await session.commit()
        # A finished and archived Action is still the Action its Goal has.
        await finish_action(session, parts.id)
        await archive_subtree(session, parts.id)
        await session.commit()

        request = await empty_parents_request(session, [MORNING_TIME_DEFAULT], now=now)
        assert request is not None
        assert f"#{goal.id} «Learn Spanish» (Goal)" in request
        assert f"#{subgoal.id} «Grammar» (Subgoal)" in request
        for title in ("Fix the bike", "Started last evening", "Old idea"):
            assert title not in request
        assert "plan its Actions now, or create one Action" in request
        assert "Do not create anything without their answer" in request

        # An Action under the Subgoal is the Goal's too; with none left, nothing is asked.
        await create_card(
            session, kind="action", title="Read chapter 1", effort_points=1, parent_id=subgoal.id
        )
        await session.commit()
        assert await empty_parents_request(session, [MORNING_TIME_DEFAULT], now=now) is None


async def test_cd_today_036_entering_today_is_the_change_a_hook_follows_up(sessions):
    """CD-TODAY-036 — tests/brd/cards.feature"""
    async with sessions() as session:
        born = await create_card(
            session, kind="action", title="Born today", stage="today", effort_points=1
        )
        planned = await create_card(
            session, kind="action", title="Planned", stage="sprint", effort_points=1
        )
        assert take_changes(session.info) == [Committed(CARD_TODAY, born.id)]
        await move_card(session, planned.id, CardStage.TODAY)
        # Staying, renamed or re-estimated in Today is not entering it.
        await move_card(session, planned.id, CardStage.TODAY)
        await update_card_fields(session, planned.id, {"title": "Planned twice"})
        await update_card_fields(session, planned.id, {"effort_points": 5})
        assert take_changes(session.info) == [Committed(CARD_TODAY, planned.id)]
        # The next instance of a finished repeating Action opens where it was: in Today.
        habit = await create_card(
            session, kind="action", title="Habit", stage="today", effort_points=1, repeatable=True
        )
        take_changes(session.info)
        result = await finish_action(session, habit.id)
        assert take_changes(session.info) == [Committed(CARD_TODAY, result.successor_ids[0])]
        await session.commit()


async def test_cd_today_036_the_request_sums_the_day_as_it_is_about_to_be_said(sessions):
    """CD-TODAY-036 — tests/brd/cards.feature"""
    assert TODAY_OVERLOAD_HOOK.agent_related
    assert TODAY_OVERLOAD_HOOK.on == (OnCommitted(kind=CARD_TODAY),)
    async with sessions() as session:
        done = await create_card(
            session, kind="action", title="Done already", stage="today", effort_points=5
        )
        await finish_action(session, done.id)
        big = await create_card(session, kind="action", title="Big one", stage="today", effort_points=8)
        small = await create_card(
            session, kind="action", title="Small one", stage="today", effort_points=2
        )
        await session.commit()
        # 5 finished today, 8 and 2 open: exactly the capacity is not over it.
        assert await today_overload_request(session, [big.id, small.id]) is None

        extra = await create_card(
            session, kind="action", title="One more", stage="today", effort_points=1
        )
        await session.commit()
        request = await today_overload_request(session, [extra.id])
        assert request is not None
        assert f"Today holds 16 EP, over the {TODAY_CAPACITY_EP} EP" in request
        assert "5 EP of it is finished already" in request
        for line in (
            f"#{big.id} «Big one» (8 EP)", f"#{small.id} «Small one» (2 EP)",
            f"#{extra.id} «One more» (1 EP)",
        ):
            assert line in request
        assert "Done already" not in request
        assert "Do not move anything without their answer" in request

        # Finished on another day, it is not this day's; moved back, the day is under.
        done.completed_at = utcnow() - timedelta(days=2)
        await session.commit()
        assert await today_overload_request(session, [extra.id]) is None
        done.completed_at = utcnow()
        await move_card(session, big.id, CardStage.SPRINT)
        await session.commit()
        assert await today_overload_request(session, [extra.id]) is None


async def test_cd_rest_037_the_request_names_the_sprints_rest_while_the_day_holds_none(sessions):
    """CD-REST-037 — tests/brd/cards.feature"""
    assert REST_TODAY_HOOK.agent_related
    assert REST_TODAY_HOOK.on == (OnTick(at=morning_time),)
    async with sessions() as session:
        nap = await create_card(
            session, kind="action", title="Nap", stage="sprint", effort_points=1,
            categories={"rest"},
        )
        walk = await create_card(
            session, kind="action", title="Walk", stage="sprint", effort_points=1,
            categories={"rest"},
        )
        await create_card(
            session, kind="action", title="Work", stage="sprint", effort_points=3
        )
        await create_card(
            session, kind="action", title="Someday rest", stage="backlog", effort_points=1,
            categories={"rest"},
        )
        await session.commit()
        # In Planning there is no plan to take rest from.
        assert await rest_today_request(session, [MORNING_TIME_DEFAULT]) is None

        await start_sprint(session, success_criteria="Ship v2")
        await session.commit()
        request = await rest_today_request(session, [MORNING_TIME_DEFAULT])
        assert request is not None
        assert f"- #{nap.id} «Nap»" in request and f"- #{walk.id} «Walk»" in request
        assert "Work" not in request and "Someday rest" not in request
        assert "Do not move anything without their answer" in request

        # A Rest Action in Today by then: the day has its rest.
        await move_card(session, nap.id, CardStage.TODAY)
        await session.commit()
        assert await rest_today_request(session, [MORNING_TIME_DEFAULT]) is None
        # Finished that day, it still counts, while Walk stands in the Sprint.
        await finish_action(session, nap.id)
        await session.commit()
        assert await rest_today_request(session, [MORNING_TIME_DEFAULT]) is None
        # Finished on another day, it is not this day's rest.
        nap.completed_at = utcnow() - timedelta(days=2)
        await session.commit()
        request = await rest_today_request(session, [MORNING_TIME_DEFAULT])
        assert request is not None and "Walk" in request and "Nap" not in request

        # With no open Rest Action left in the Sprint, nothing.
        await move_card(session, walk.id, CardStage.BACKLOG)
        await session.commit()
        assert await rest_today_request(session, [MORNING_TIME_DEFAULT]) is None
