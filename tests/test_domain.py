from __future__ import annotations

from sqlalchemy import select

from safwa.domain import (
    effective_value_ids,
    finish_action,
    finish_sprint,
    move_card,
    sprint_metrics,
    start_sprint,
)
from safwa.drafts import DraftService
from safwa.enums import CardStage
from safwa.models import Board, Card, CardValue, FeedbackQueue, Value, Workspace


async def create_card(session, **overrides):
    inbox = await session.scalar(select(Board).where(Board.name == "Inbox"))
    payload = {
        "title": "Action",
        "kind": "action",
        "board_id": inbox.id,
        "expected_board_version": inbox.version,
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
