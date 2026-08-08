from __future__ import annotations

from sqlalchemy import select

from safwa.domain import (
    DomainError,
    archive_subtree,
    create_tag,
    create_value,
    edit_card_text,
    effective_value_ids,
    finish_action,
    finish_sprint,
    move_card,
    set_card_parent,
    set_value_focus,
    sprint_metrics,
    start_sprint,
    toggle_card_dependency,
    toggle_card_tag,
    toggle_card_value,
    update_profile,
)
from safwa.drafts import DraftService
from safwa.enums import CardStage
from safwa.models import (
    Card,
    CardEvent,
    CardTag,
    CardValue,
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
        "root_confirmed": True,
        "stage": "backlog",
        "effort_points": 3,
    }
    payload.update(overrides)
    bundle = await DraftService(session).create_bundle("manual", [payload])
    draft = (await DraftService(session).get_bundle_drafts(bundle.id))[0]
    await DraftService(session).mark_reviewed(draft.id)
    return (await DraftService(session).commit_bundle(bundle.id))[0]


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


async def test_sprint_snapshots_and_carryover(sessions):
    async with sessions() as session:
        initial = await create_card(session, title="Initial", stage="sprint", effort_points=5)
        sprint = await start_sprint(session)
        added = await create_card(session, title="Added", stage="today", effort_points=3)
        await finish_action(session, initial.id, CardStage.DONE)
        metrics = await sprint_metrics(session, sprint.id)
        assert metrics == {"committed": 5, "added": 3, "removed": 0, "completed": 5, "cancelled": 0}
        await finish_sprint(session, reason="finished_early")
        workspace = await session.get(Workspace, 1)
        assert workspace.mode == "planning"
        assert added.effective_stage == "today"


async def test_parent_effective_values_are_derived_from_descendants(sessions):
    async with sessions() as session:
        goal = await create_card(session, title="Goal", kind="goal", effort_points=None)
        action = await create_card(
            session,
            title="Child",
            parent_id=goal.id,
            expected_parent_version=goal.version,
            root_confirmed=False,
        )
        value = Value(name="Fitness", active=True)
        session.add(value)
        await session.flush()
        session.add(CardValue(card_id=action.id, value_id=value.id))
        await session.flush()
        assert await effective_value_ids(session, goal.id) == {value.id}


async def test_ui_mutations_use_domain_services_and_are_audited(sessions):
    async with sessions() as session:
        tag = await create_tag(session, "Personal")
        value = await create_value(session, "Consistency")
        await set_value_focus(session, value.id, True)
        card = await create_card(session, title="Original")
        assert await toggle_card_tag(session, card.id, tag.id) is True
        await edit_card_text(session, card.id, "title", "Renamed")
        await update_profile(session, about_me="Prefers calm, practical planning")
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
        blocker = await create_card(session, title="Blocker")
        value = await create_value(session, "Health")
        await move_card(session, action.id, CardStage.TODAY)

        await set_card_parent(session, action.id, second_goal.id)
        assert action.parent_id == second_goal.id
        assert first_goal.effective_stage == CardStage.BACKLOG.value
        assert second_goal.effective_stage == CardStage.TODAY.value

        assert await toggle_card_value(session, action.id, value.id) is True
        assert await effective_value_ids(session, second_goal.id) == {value.id}
        assert await toggle_card_value(session, action.id, value.id) is False

        tag = Tag(name="Family")
        session.add(tag)
        await session.flush()
        assert await toggle_card_tag(session, action.id, tag.id) is True

        assert await toggle_card_dependency(session, action.id, blocker.id) is True
        try:
            await toggle_card_dependency(session, blocker.id, action.id)
        except DomainError:
            pass
        else:
            raise AssertionError("dependency cycle should be rejected")
        assert await toggle_card_dependency(session, action.id, blocker.id) is False
        await session.commit()

        events = list(
            await session.scalars(select(CardEvent).where(CardEvent.card_id == action.id))
        )
        assert {event.operation for event in events} >= {
            "set_parent",
            "link_value",
            "unlink_value",
            "link_dependency",
            "unlink_dependency",
        }
