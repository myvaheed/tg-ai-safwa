from __future__ import annotations

import pytest
from sqlalchemy import select

from safwa.domain import (
    DomainError,
    archive_subtree,
    card_progress,
    create_tag,
    create_value,
    edit_card_text,
    finish_action,
    finish_sprint,
    live_repeat_instance_id,
    move_card,
    repeat_marker,
    set_card_parent,
    set_value_focus,
    sprint_metrics,
    start_sprint,
    toggle_card_tag,
    toggle_card_value,
    update_card_fields,
)
from safwa.domain import create_card as create_domain_card
from safwa.enums import CardStage
from safwa.features.profile.model import ProfileField
from safwa.features.profile.use_cases import set_profile_field
from safwa.foundation.clock import SystemClock
from safwa.models import (
    Card,
    CardEvent,
    CardTag,
    FeedbackQueue,
    Tag,
    UserProfile,
    Value,
    Workspace,
)


async def create_card(session, **overrides):
    payload = {
        "title": "Action",
        "kind": "action",
        "stage": "backlog",
        "effort_points": 3,
    }
    payload.update(overrides)
    payload.pop("root_confirmed", None)
    payload.pop("expected_parent_version", None)
    return await create_domain_card(session, **payload)


async def test_parent_stage_propagation_and_reopen(sessions):
    async with sessions() as session:
        goal = await create_card(session, title="Goal", kind="goal", effort_points=None)
        action = await create_card(
            session,
            title="Child",
            parent_id=goal.id,
            expected_parent_version=goal.version,
            root_confirmed=False,
        )
        await session.commit()
        await move_card(session, action.id, CardStage.TODAY)
        assert goal.effective_stage == CardStage.TODAY.value
        await finish_action(session, action.id, CardStage.DONE)
        assert goal.effective_stage == CardStage.DONE.value
        await move_card(session, action.id, CardStage.BACKLOG)
        assert goal.effective_stage == CardStage.BACKLOG.value


async def test_repeat_completion_clones_and_queues_feedback(sessions):
    async with sessions() as session:
        card = await create_card(session, title="Run", repeatable=True, stage="today")
        result = await finish_action(session, card.id, CardStage.DONE)
        await session.commit()
        successor = await session.get(Card, result.successor_ids[0])
        assert successor.effective_stage == CardStage.TODAY.value
        assert successor.repeat_series_id == card.repeat_series_id
        feedback = await session.scalar(
            select(FeedbackQueue).where(FeedbackQueue.card_id == card.id)
        )
        assert feedback is not None


async def test_a_closed_repeat_names_its_place_in_the_series(sessions):
    async with sessions() as session:
        first = await create_card(session, title="Run", repeatable=True, stage="today")
        result = await finish_action(session, first.id, CardStage.DONE)
        second = await session.get(Card, result.successor_ids[0])
        result = await finish_action(session, second.id, CardStage.DONE)
        third = await session.get(Card, result.successor_ids[0])
        await session.commit()

        assert await repeat_marker(session, first) == " [🔄1]"
        assert await repeat_marker(session, second) == " [🔄2]"
        # The open row carries the series, so it is named plainly — that is what says it is
        # the one to work with.
        assert await repeat_marker(session, third) == ""

        # A Card that does not repeat is not a series, closed or not.
        plain = await create_card(session, title="Once", stage="today")
        await finish_action(session, plain.id, CardStage.DONE)
        await session.commit()
        assert await repeat_marker(session, plain) == ""


async def test_a_closed_repeat_cannot_be_reopened_and_points_at_the_open_one(sessions):
    async with sessions() as session:
        first = await create_card(session, title="Run", repeatable=True, stage="today")
        result = await finish_action(session, first.id, CardStage.DONE)
        second = await session.get(Card, result.successor_ids[0])
        result = await finish_action(session, second.id, CardStage.DONE)
        third = await session.get(Card, result.successor_ids[0])
        await session.commit()

        with pytest.raises(DomainError, match="closed repeating Action"):
            await move_card(session, first.id, CardStage.TODAY)

        # Across three generations the answer is the newest open row, not the direct
        # successor: only the last one created can still be open.
        assert await live_repeat_instance_id(session, first) == third.id
        assert await live_repeat_instance_id(session, second) == third.id

        # Cancelling continues the series too, so the answer follows to the new row.
        result = await finish_action(session, third.id, CardStage.CANCELLED)
        await session.commit()
        assert await live_repeat_instance_id(session, first) == result.successor_ids[0]

        # A non-repeating Card is not a series, so reopening it stays ordinary.
        plain = await create_card(session, title="Once", stage="today")
        await finish_action(session, plain.id, CardStage.DONE)
        await move_card(session, plain.id, CardStage.TODAY)
        await session.commit()
        assert (await session.get(Card, plain.id)).effective_stage == CardStage.TODAY.value


async def test_sprint_snapshots_and_carryover(sessions):
    async with sessions() as session:
        initial = await create_card(session, title="Initial", stage="sprint", effort_points=5)
        sprint = await start_sprint(session, success_criteria="Ship the release")
        added = await create_card(session, title="Added", stage="today", effort_points=3)
        await finish_action(session, initial.id, CardStage.DONE)
        metrics = await sprint_metrics(session, sprint.id)
        assert metrics == {"committed": 5, "added": 3, "removed": 0, "completed": 5, "cancelled": 0}
        await finish_sprint(session, reason="finished_early")
        workspace = await session.get(Workspace, 1)
        assert workspace.mode == "planning"
        assert added.effective_stage == "today"


async def test_ui_mutations_use_domain_services_and_are_audited(sessions):
    """PL-VALUE-001 — tests/brd/values.feature"""
    async with sessions() as session:
        tag = await create_tag(session, "Personal")
        value = await create_value(session, "Consistency")
        await set_value_focus(session, value.id, True)
        card = await create_card(session, title="Original")
        assert await toggle_card_tag(session, card.id, tag.id) is True
        await edit_card_text(session, card.id, "title", "Renamed")
        await set_profile_field(
            session,
            ProfileField.ABOUT_ME,
            "Prefers calm, practical planning",
            clock=SystemClock(),
        )
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
    """PL-VALUE-004 — tests/brd/values.feature"""
    async with sessions() as session:
        first_goal = await create_card(session, title="First goal", kind="goal", effort_points=None)
        second_goal = await create_card(
            session, title="Second goal", kind="goal", effort_points=None
        )
        action = await create_card(
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


async def test_reopening_a_finished_action_clears_its_sprint_result(sessions):
    async with sessions() as session:
        action = await create_card(session, title="Ship", stage="sprint", effort_points=5)
        sprint = await start_sprint(session, success_criteria="Ship the release")
        await finish_action(session, action.id, CardStage.DONE)
        assert (await sprint_metrics(session, sprint.id))["completed"] == 5

        await move_card(session, action.id, CardStage.SPRINT)

        # A reopened Action is live again, so its effort must stop counting as completed.
        assert (await sprint_metrics(session, sprint.id))["completed"] == 0
        assert action.effective_stage == CardStage.SPRINT.value


async def test_returning_to_sprint_scope_cancels_the_earlier_removal(sessions):
    async with sessions() as session:
        action = await create_card(session, title="Ship", stage="sprint", effort_points=5)
        sprint = await start_sprint(session, success_criteria="Ship the release")
        await move_card(session, action.id, CardStage.BACKLOG)
        assert (await sprint_metrics(session, sprint.id))["removed"] == 5

        await move_card(session, action.id, CardStage.TODAY)

        # The same effort must not be reported as both removed and selected.
        assert (await sprint_metrics(session, sprint.id))["removed"] == 0


async def test_an_action_cannot_reach_a_terminal_stage_through_move(sessions):
    async with sessions() as session:
        action = await create_card(session, title="Ship", stage="today", effort_points=3)
        with pytest.raises(DomainError):
            await move_card(session, action.id, CardStage.DONE)
        with pytest.raises(DomainError):
            await move_card(session, action.id, CardStage.CANCELLED)
        assert action.effective_stage == CardStage.TODAY.value


async def test_goal_progress_is_recursive_but_children_count_is_direct(sessions):
    async with sessions() as session:
        goal = await create_card(session, title="Goal", kind="goal", effort_points=None)
        idea = await create_card(
            session,
            title="Idea",
            kind="idea",
            effort_points=None,
            parent_id=goal.id,
        )
        done = await create_card(
            session,
            title="Done",
            effort_points=3,
            parent_id=idea.id,
        )
        await create_card(
            session,
            title="Remaining",
            effort_points=5,
            parent_id=goal.id,
        )
        await finish_action(session, done.id, CardStage.DONE)

        progress = await card_progress(session, goal.id)

        assert progress == {
            "completed_effort": 3,
            "total_effort": 8,
            "completed_children": 1,
            "total_children": 2,
        }
