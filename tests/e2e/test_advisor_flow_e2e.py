from __future__ import annotations

import json

import pytest
from sqlalchemy import func, select

from safwa.ai.service import AIOutcome
from safwa.analytics import render_retrospective_png, retrospective_data
from safwa.domain import finish_action, finish_sprint, move_card, sprint_metrics, start_sprint
from safwa.drafts import DraftService
from safwa.enums import CardStage, DraftStatus
from safwa.models import (
    AgentRun,
    AgentStep,
    Card,
    CardCategory,
    CardDraft,
    CardEnergyType,
    CardTag,
    CardValue,
    FeedbackQueue,
    Tag,
    Value,
    Workspace,
)

pytestmark = pytest.mark.e2e


async def create_manual_card(session, **overrides) -> Card:
    payload = {
        "title": "Action",
        "kind": "action",
        "root_confirmed": True,
        "stage": "backlog",
        "effort_points": 3,
    }
    payload.update(overrides)
    drafts = DraftService(session)
    bundle = await drafts.create_bundle("manual", [payload])
    draft = (await drafts.get_bundle_drafts(bundle.id))[0]
    await drafts.mark_reviewed(draft.id)
    return (await drafts.commit_bundle(bundle.id))[0]


async def test_ai_card_review_to_repeat_sprint_and_retrospective(e2e_harness):
    async with e2e_harness.sessions() as session:
        goal = await create_manual_card(
            session,
            title="To be fit",
            kind="goal",
            effort_points=None,
        )
        fitness = Value(name="Fitness", description="Build a healthy body", active=True)
        family = Tag(name="Family")
        session.add(fitness)
        session.add(family)
        await session.commit()

    response = json.dumps(
        {
            "kind": "proposal",
            "message": "I prepared the Action for your review.",
            "changes": [
                {
                    "entity": "card",
                    "action": "create",
                    "values": {
                        "kind": "action",
                        "title": "Push ups 30 times",
                        "parent_query": "To be fit",
                        "effort_points": None,
                        "repeatable": True,
                        "categories": ["self"],
                        "energy_types": ["physical"],
                        "value_query": "Fitness",
                        "tag_query": "Family",
                    },
                }
            ],
        }
    )
    advisor, provider = e2e_harness.advisor([response])
    outcome: AIOutcome = await advisor.handle(
        "Please create a new action Push ups 30 times and link it to To be fit goal"
    )

    assert outcome.kind == "proposal"
    assert len(outcome.draft_bundle_ids) == 1
    assert outcome.proposal_id is None
    assert len(provider.calls) == 1

    bundle_id = outcome.draft_bundle_ids[0]
    async with e2e_harness.sessions() as session:
        assert await session.scalar(select(func.count(Card.id))) == 1
        draft = (await DraftService(session).get_bundle_drafts(bundle_id))[0]
        assert draft.status == DraftStatus.EDITING.value
        assert draft.parent_id == goal.id
        assert any("effort" in error.lower() for error in draft.validation_errors)

        await DraftService(session).update(draft.id, effort_points=2)
        await DraftService(session).mark_reviewed(draft.id)
        action = (await DraftService(session).commit_bundle(bundle_id))[0]
        await session.commit()

        assert action.title == "Push ups 30 times"
        assert action.parent_id == goal.id
        assert action.repeatable is True
        assert action.effort_points == 2
        assert (
            await session.scalar(
                select(func.count(CardCategory.card_id)).where(CardCategory.card_id == action.id)
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count(CardEnergyType.card_id)).where(
                    CardEnergyType.card_id == action.id
                )
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count(CardValue.card_id)).where(CardValue.card_id == action.id)
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count(CardTag.card_id)).where(CardTag.card_id == action.id)
            )
            == 1
        )

        await move_card(session, action.id, CardStage.SPRINT)
        sprint = await start_sprint(session, capacity=8)
        await move_card(session, action.id, CardStage.TODAY)
        completion = await finish_action(session, action.id, CardStage.DONE)
        assert len(completion.successor_ids) == 1
        successor = await session.get(Card, completion.successor_ids[0])
        assert successor is not None
        assert successor.effective_stage == CardStage.TODAY.value
        assert successor.parent_id == goal.id
        current_goal = await session.get(Card, goal.id)
        assert current_goal is not None
        assert current_goal.effective_stage == CardStage.TODAY.value

        feedback = await session.scalar(
            select(FeedbackQueue).where(FeedbackQueue.card_id == action.id)
        )
        assert feedback is not None
        assert await sprint_metrics(session, sprint.id) == {
            "committed": 2,
            "added": 2,
            "removed": 0,
            "completed": 2,
            "cancelled": 0,
        }

        retro = await retrospective_data(session, sprint.id)
        png = render_retrospective_png(retro)
        assert png.startswith(b"\x89PNG\r\n\x1a\n")
        assert len(png) > 10_000

        await finish_sprint(session, reason="finished_early")
        workspace = await session.get(Workspace, 1)
        assert workspace is not None
        assert workspace.mode == "planning"
        assert successor.effective_stage == CardStage.TODAY.value
        await session.commit()


async def test_ai_read_query_round_trip_uses_safe_view(e2e_harness):
    async with e2e_harness.sessions() as session:
        action = await create_manual_card(
            session,
            title="Prepare release",
            stage="sprint",
            effort_points=5,
        )
        await start_sprint(session, capacity=8)
        await finish_action(session, action.id, CardStage.DONE)
        await session.commit()

    query_response = json.dumps(
        {
            "kind": "query",
            "message": "I will calculate the Sprint totals.",
            "sql": "SELECT committed, completed FROM ai_current_sprint_metrics",
        }
    )
    answer_response = json.dumps(
        {
            "kind": "answer",
            "message": "You committed 5 effort points and completed all 5.",
        }
    )
    advisor, provider = e2e_harness.advisor([query_response, answer_response])
    outcome = await advisor.handle(
        "How many effort points did I commit and complete in this sprint?"
    )

    assert outcome.kind == "answer"
    assert outcome.message == "You committed 5 effort points and completed all 5."
    assert len(provider.calls) == 2
    follow_up_context = "\n".join(message["content"] for message in provider.calls[1])
    assert "Read-query result" in follow_up_context
    assert '"committed": 5' in follow_up_context
    assert '"completed": 5' in follow_up_context

    async with e2e_harness.sessions() as session:
        run = await session.scalar(select(AgentRun).order_by(AgentRun.created_at.desc()))
        assert run is not None
        assert run.status == "completed"
        step = await session.scalar(select(AgentStep).where(AgentStep.run_id == run.id))
        assert step is not None
        assert step.kind == "read_query"
        assert step.metadata_json["row_count"] == 1
        assert set(step.metadata_json["columns"]) == {"committed", "completed"}


async def test_multi_card_ai_bundle_is_reviewed_and_committed_atomically(e2e_harness):
    response = json.dumps(
        {
            "kind": "proposal",
            "message": "I prepared a Goal and two Actions for review.",
            "changes": [
                {
                    "entity": "card",
                    "action": "create",
                    "values": {
                        "kind": "goal",
                        "title": "Read more",
                        "draft_ref": "goal",
                    },
                },
                {
                    "entity": "card",
                    "action": "create",
                    "values": {
                        "kind": "action",
                        "title": "Read ten pages",
                        "effort_points": 2,
                        "draft_ref": "read",
                        "parent_draft_ref": "goal",
                    },
                },
                {
                    "entity": "card",
                    "action": "create",
                    "values": {
                        "kind": "action",
                        "title": "Write reading notes",
                        "effort_points": 2,
                        "draft_ref": "notes",
                        "parent_draft_ref": "goal",
                    },
                },
            ],
        }
    )
    advisor, _provider = e2e_harness.advisor([response])
    outcome = await advisor.handle("Create a reading goal with two supporting actions")
    bundle_id = outcome.draft_bundle_ids[0]

    async with e2e_harness.sessions() as session:
        assert await session.scalar(select(func.count(Card.id))) == 0
        drafts = await DraftService(session).get_bundle_drafts(bundle_id)
        assert len(drafts) == 3
        for draft in drafts:
            await DraftService(session).mark_reviewed(draft.id)
        cards = await DraftService(session).commit_bundle(bundle_id)
        await session.commit()

        assert len(cards) == 3
        goal = next(card for card in cards if card.kind == "goal")
        actions = [card for card in cards if card.kind == "action"]
        assert {action.parent_id for action in actions} == {goal.id}
        assert await session.scalar(select(func.count(CardDraft.id))) == 3
        assert all(
            draft.status == DraftStatus.COMMITTED.value
            for draft in await DraftService(session).get_bundle_drafts(bundle_id)
        )
