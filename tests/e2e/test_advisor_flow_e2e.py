from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from safwa.ai.context import DialogueMessage
from safwa.ai.provider import ProviderToolCall, ProviderTurn
from safwa.ai.service import AIOutcome, ProposalService
from safwa.analytics import render_retrospective_png, retrospective_data
from safwa.domain import (
    StaleStateError,
    create_saved_request,
    finish_action,
    finish_sprint,
    move_card,
    sprint_metrics,
    start_sprint,
)
from safwa.drafts import DraftService
from safwa.enums import CardStage, DraftStatus
from safwa.models import (
    AgentRun,
    AgentStep,
    CallbackToken,
    Card,
    CardCategory,
    CardDraft,
    CardEnergyType,
    CardTag,
    CardValue,
    ChangeProposal,
    FeedbackQueue,
    SavedRequest,
    Tag,
    Value,
    Workspace,
)
from safwa.saved_requests import request_cards
from safwa.telegram import GenerationGuard, callback_token_handler, render_proposal

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


def mutation_turn(*calls: tuple[str, dict[str, object]]) -> ProviderTurn:
    return ProviderTurn(
        content="",
        tool_calls=tuple(
            ProviderToolCall(id=f"mutation-{index}", name=name, arguments=json.dumps(arguments))
            for index, (name, arguments) in enumerate(calls, start=1)
        ),
    )


async def test_ai_parent_query_sql_resolves_before_draft_review(e2e_harness):
    async with e2e_harness.sessions() as session:
        parent = await create_manual_card(
            session,
            title="Реализовать новый Дизайн",
            kind="idea",
            effort_points=None,
        )
        await session.commit()

    response = mutation_turn(
        (
            "card",
            {
                "mode": "draft",
                "kind": "action",
                "title": "Применить новый дизайн",
                "parent_query": (
                    "SELECT id FROM ai_cards WHERE title = 'Реализовать новый Дизайн'"
                ),
                "effort_points": 5,
            },
        )
    )
    advisor, _provider = e2e_harness.advisor([response])

    outcome = await advisor.handle("Создай экшен для идеи Реализовать новый Дизайн")

    async with e2e_harness.sessions() as session:
        draft = (await DraftService(session).get_bundle_drafts(outcome.draft_bundle_ids[0]))[0]
        assert draft.parent_id == parent.id
        assert not any("parent" in error.casefold() for error in draft.validation_errors)
        assert "parent_query" not in draft.field_provenance


async def test_ai_parent_query_rejects_non_ai_card_sql_and_leaves_review_unresolved(
    e2e_harness,
):
    response = mutation_turn(
        (
            "card",
            {
                "mode": "draft",
                "kind": "action",
                "title": "Unsafe parent lookup",
                "parent_query": "SELECT id FROM cards WHERE title = 'Hidden table'",
                "effort_points": 2,
            },
        )
    )
    advisor, _provider = e2e_harness.advisor([response])

    outcome = await advisor.handle("Create an action under that parent")

    async with e2e_harness.sessions() as session:
        draft = (await DraftService(session).get_bundle_drafts(outcome.draft_bundle_ids[0]))[0]
        assert draft.parent_id is None
        assert "Parent query was invalid or unsafe" in draft.field_provenance["unresolved"]
        assert any("parent" in error.casefold() for error in draft.validation_errors)


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

    response = mutation_turn(
        (
            "card",
            {
                "mode": "draft",
                "kind": "action",
                "title": "Push ups 30 times",
                "parent_query": "SELECT id FROM ai_cards WHERE title = 'To be fit'",
                "repeatable": True,
                "categories": ["self"],
                "energy_types": ["physical"],
                "value_query": "Fitness",
                "tag_query": "Family",
            },
        )
    )
    advisor, provider = e2e_harness.advisor([response])
    outcome: AIOutcome = await advisor.handle(
        "Please create a new action Push ups 30 times and link it to To be fit goal"
    )

    assert outcome.kind == "proposal"
    assert len(outcome.draft_bundle_ids) == 1
    assert outcome.proposal_id is None
    assert len(provider.calls) == 1
    assert not provider.responses

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

    query_response = ProviderTurn(
        content="",
        tool_calls=(
            ProviderToolCall(
                id="read-1",
                name="query_safwa",
                arguments=json.dumps(
                    {"sql": "SELECT committed, completed FROM ai_current_sprint_metrics"}
                ),
            ),
        ),
    )
    answer_response = "You committed 5 effort points and completed all 5."
    advisor, provider = e2e_harness.advisor([query_response, answer_response])
    outcome = await advisor.handle(
        "How many effort points did I commit and complete in this sprint?"
    )

    assert outcome.kind == "answer"
    assert outcome.message == "You committed 5 effort points and completed all 5."
    assert len(provider.calls) == 2
    assert provider.calls[1][-2]["role"] == "assistant"
    assert provider.calls[1][-1]["role"] == "tool"
    follow_up_context = str(provider.calls[1][-1]["content"])
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
    response = mutation_turn(
        ("card", {"mode": "draft", "kind": "goal", "title": "Read more", "draft_ref": "goal"}),
        (
            "card",
            {
                "mode": "draft",
                "kind": "action",
                "title": "Read ten pages",
                "effort_points": 2,
                "draft_ref": "read",
                "parent_draft_ref": "goal",
            },
        ),
        (
            "card",
            {
                "mode": "draft",
                "kind": "action",
                "title": "Write reading notes",
                "effort_points": 2,
                "draft_ref": "notes",
                "parent_draft_ref": "goal",
            },
        ),
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


async def test_ai_creates_an_approved_saved_tag_request(e2e_harness):
    async with e2e_harness.sessions() as session:
        family = Tag(name="Family")
        session.add(family)
        action = await create_manual_card(session, title="Call parents", effort_points=1)
        session.add(CardTag(card_id=action.id, tag_id=family.id))
        await session.commit()

    response = mutation_turn(
        (
            "request",
            {
                "mode": "create",
                "name": "Family actions",
                "description": "All active Actions tagged Family.",
                "sql": "SELECT id FROM ai_cards WHERE kind = 'action' "
                "AND direct_tags LIKE '%Family%'",
            },
        )
    )
    advisor, _provider = e2e_harness.advisor([response])
    outcome = await advisor.handle("Create a Request for my Family actions")

    assert outcome.proposal_id is not None
    async with e2e_harness.sessions() as session:
        affected = await ProposalService(session).apply(outcome.proposal_id)
        await session.commit()
        request = await session.get(SavedRequest, affected[0])
        assert request is not None
        matches = await request_cards(session, request.query_sql)
        assert [card.id for card in matches] == [action.id]


async def test_repeatable_action_preserves_tags_in_e2e_flow(e2e_harness):
    async with e2e_harness.sessions() as session:
        tag = Tag(name="Health")
        session.add(tag)
        action = await create_manual_card(
            session,
            title="Run outside",
            stage=CardStage.TODAY.value,
            effort_points=2,
            repeatable=True,
        )
        session.add(CardTag(card_id=action.id, tag_id=tag.id))
        await session.flush()
        completion = await finish_action(session, action.id, CardStage.DONE)
        await session.commit()

    async with e2e_harness.sessions() as session:
        successor = await session.get(Card, completion.successor_ids[0])
        assert successor is not None
        copied_tag = await session.get(CardTag, {"card_id": successor.id, "tag_id": tag.id})
        assert copied_tag is not None


async def test_ai_approved_tag_proposal_creates_a_reusable_tag(e2e_harness):
    response = mutation_turn(
        ("tag", {"mode": "create", "name": "Learning", "description": "Study and practice."})
    )
    advisor, _provider = e2e_harness.advisor([response])
    outcome = await advisor.handle("Create a Learning tag")

    async with e2e_harness.sessions() as session:
        affected = await ProposalService(session).apply(outcome.proposal_id or 0)
        await session.commit()
        tag = await session.get(Tag, affected[0])
        assert tag is not None
        assert (tag.name, tag.description) == ("Learning", "Study and practice.")


async def test_ai_can_create_and_link_a_tag_in_one_approved_proposal(e2e_harness):
    async with e2e_harness.sessions() as session:
        goal = await create_manual_card(
            session, title="Release VrWalk", kind="goal", effort_points=None
        )
        action = await create_manual_card(session, title="Refactor design", effort_points=3)
        await session.commit()

    read_turn = ProviderTurn(
        content="",
        tool_calls=(
            ProviderToolCall(
                id="recent-cards",
                name="query_safwa",
                arguments=json.dumps(
                    {
                        "sql": "SELECT id, title, created_at FROM ai_cards "
                        "ORDER BY created_at DESC LIMIT 10"
                    }
                ),
            ),
        ),
    )
    response = mutation_turn(
        ("tag", {"mode": "create", "name": "VrWalk"}),
        ("card", {"mode": "link", "id": goal.id, "tag_query": "VrWalk"}),
        ("card", {"mode": "link", "id": action.id, "tag_query": "VrWalk"}),
    )
    advisor, provider = e2e_harness.advisor([read_turn, response])
    outcome = await advisor.handle("Create VrWalk and attach it to my recent cards")

    async with e2e_harness.sessions() as session:
        affected = await ProposalService(session).apply(outcome.proposal_id or "")
        await session.commit()
        tag = await session.scalar(select(Tag).where(Tag.name == "VrWalk"))
        assert tag is not None
        assert set(affected) == {tag.id, goal.id, action.id}
        for card_id in (goal.id, action.id):
            assert await session.get(CardTag, {"card_id": card_id, "tag_id": tag.id}) is not None
    assert provider.calls[1][-1]["role"] == "tool"


async def test_ai_can_create_and_link_a_value_in_one_approved_proposal(e2e_harness):
    async with e2e_harness.sessions() as session:
        action = await create_manual_card(session, title="Morning run", effort_points=2)
        await session.commit()

    response = mutation_turn(
        ("value", {"mode": "create", "name": "Health", "active": True}),
        ("card", {"mode": "link", "id": action.id, "value_query": "Health"}),
    )
    advisor, _provider = e2e_harness.advisor([response])
    outcome = await advisor.handle("Create Health and link it to Morning run")

    async with e2e_harness.sessions() as session:
        affected = await ProposalService(session).apply(outcome.proposal_id or 0)
        await session.commit()
        value = await session.scalar(select(Value).where(Value.name == "Health"))
        assert value is not None and value.active is True
        assert set(affected) == {value.id, action.id}
        assert (
            await session.get(CardValue, {"card_id": action.id, "value_id": value.id}) is not None
        )


async def test_ai_request_update_is_rejected_when_the_request_becomes_stale(e2e_harness):
    async with e2e_harness.sessions() as session:
        request = await create_saved_request(
            session,
            "All goals",
            "SELECT id FROM ai_cards WHERE kind = 'goal'",
        )
        await session.commit()

    response = mutation_turn(
        (
            "request",
            {"mode": "edit", "id": request.id, "description": "Every active Goal."},
        )
    )
    advisor, _provider = e2e_harness.advisor([response])
    outcome = await advisor.handle("Clarify my All goals Request")

    async with e2e_harness.sessions() as session:
        changed = await session.get(SavedRequest, request.id)
        assert changed is not None
        changed.version += 1
        await session.commit()

    async with e2e_harness.sessions() as session:
        try:
            await ProposalService(session).apply(outcome.proposal_id or "")
        except StaleStateError:
            pass
        else:
            raise AssertionError("Request proposal must reject a stale version")


async def test_ai_can_query_saved_requests_through_the_safe_view(e2e_harness):
    async with e2e_harness.sessions() as session:
        await create_saved_request(
            session,
            "All goals",
            "SELECT id FROM ai_cards WHERE kind = 'goal'",
        )
        await session.commit()

    responses = [
        ProviderTurn(
            content="",
            tool_calls=(
                ProviderToolCall(
                    id="read-requests",
                    name="query_safwa",
                    arguments=json.dumps({"sql": "SELECT name FROM ai_requests"}),
                ),
            ),
        ),
        "You have a saved Request named All goals.",
    ]
    advisor, provider = e2e_harness.advisor(responses)
    outcome = await advisor.handle("What saved Requests do I have?")

    assert outcome.kind == "answer"
    assert len(provider.calls) == 2
    follow_up_context = str(provider.calls[1][-1]["content"])
    assert '"name": "All goals"' in follow_up_context


async def test_ai_request_query_values_and_ignores_archived_cards(e2e_harness):
    async with e2e_harness.sessions() as session:
        value = Value(name="Family")
        session.add(value)
        await session.flush()
        live = await create_manual_card(session, title="Call parents", effort_points=1)
        archived = await create_manual_card(session, title="Old family task", effort_points=1)
        session.add_all(
            [
                CardValue(card_id=live.id, value_id=value.id),
                CardValue(card_id=archived.id, value_id=value.id),
            ]
        )
        archived.archived_at = archived.created_at
        await session.commit()

    response = mutation_turn(
        (
            "request",
            {
                "mode": "create",
                "name": "Family value actions",
                "sql": "SELECT id FROM ai_cards WHERE kind = 'action' "
                "AND direct_values LIKE '%Family%'",
            },
        )
    )
    advisor, _provider = e2e_harness.advisor([response])
    outcome = await advisor.handle("Create a Request for Family value actions")

    async with e2e_harness.sessions() as session:
        affected = await ProposalService(session).apply(outcome.proposal_id or "")
        await session.commit()
        request = await session.get(SavedRequest, affected[0])
        assert request is not None
        matches = await request_cards(session, request.query_sql)
        assert [card.id for card in matches] == [live.id]


async def test_ai_request_query_supports_complex_boolean_logic(e2e_harness):
    async with e2e_harness.sessions() as session:
        today = await create_manual_card(
            session,
            title="Today action",
            stage=CardStage.TODAY.value,
            effort_points=1,
        )
        critical = await create_manual_card(
            session,
            title="Critical action",
            effort_points=1,
            priority="critical",
        )
        ordinary = await create_manual_card(session, title="Ordinary action", effort_points=1)
        await session.commit()

    response = mutation_turn(
        (
            "request",
            {
                "mode": "create",
                "name": "Urgent actions",
                "sql": "SELECT id FROM ai_cards WHERE kind = 'action' "
                "AND (stage = 'today' OR priority = 'critical') ORDER BY title",
            },
        )
    )
    advisor, _provider = e2e_harness.advisor([response])
    outcome = await advisor.handle("Create an urgent actions Request")

    async with e2e_harness.sessions() as session:
        affected = await ProposalService(session).apply(outcome.proposal_id or "")
        await session.commit()
        request = await session.get(SavedRequest, affected[0])
        assert request is not None
        matches = await request_cards(session, request.query_sql)
        assert {card.id for card in matches} == {today.id, critical.id}
        assert ordinary.id not in {card.id for card in matches}


async def test_advisor_sends_one_system_message_and_canonical_dialogue(e2e_harness):
    response = "I remember the context."
    advisor, provider = e2e_harness.advisor([response])
    dialogue = [
        DialogueMessage(role="user", content="[Initial request]: Plan this week."),
        DialogueMessage(role="assistant", content="What matters most this week?"),
        DialogueMessage(
            role="user",
            content="[User]: Health and work.\n[User]: I also need time with family.",
        ),
    ]

    outcome = await advisor.handle("I also want a calmer evening.", dialogue=dialogue)

    assert outcome.kind == "answer"
    messages = provider.calls[0]
    assert [message["role"] for message in messages] == [
        "system",
        "user",
        "assistant",
        "user",
    ]
    assert sum(message["role"] == "system" for message in messages) == 1
    assert messages[-1]["content"] == dialogue[-1].content
    system = str(messages[0]["content"])
    assert "query_safwa" in system
    assert "ai_cards(id, title" in system
    assert "Workspace revision:" not in system
    assert "Saved Requests:" not in system
    assert "Sprint cards:" not in system
    assert "Recent cards" not in system
    assert "Lexical card candidates" not in system
    tools = provider.options[0]["tools"]
    assert isinstance(tools, list) and [tool["function"]["name"] for tool in tools] == [
        "query_safwa",
        "card",
        "value",
        "tag",
        "request",
        "remove",
    ]


async def test_mixed_query_and_mutation_resumes_only_after_approval(e2e_harness):
    async with e2e_harness.sessions() as session:
        card = await create_manual_card(session, title="Release VrWalk", effort_points=3)
        await session.commit()

    mixed_turn = ProviderTurn(
        content="",
        tool_calls=(
            ProviderToolCall(
                id="create-tag",
                name="tag",
                arguments=json.dumps({"mode": "create", "name": "VrWalk"}),
            ),
            ProviderToolCall(
                id="recent-cards",
                name="query_safwa",
                arguments=json.dumps(
                    {"sql": "SELECT id, title FROM ai_cards ORDER BY created_at DESC LIMIT 10"}
                ),
            ),
        ),
    )
    advisor, provider = e2e_harness.advisor([mixed_turn, "The tag was saved."])

    outcome = await advisor.handle("Create VrWalk and tag my recent cards")

    assert outcome.proposal_id is not None
    assert len(provider.calls) == 1
    async with e2e_harness.sessions() as session:
        affected = await ProposalService(session).apply(outcome.proposal_id)
        await session.commit()

    resumed = await advisor.resolve_approval(
        "proposal",
        outcome.proposal_id,
        decision="approved",
        result={"affected_ids": affected},
        dialogue=[
            DialogueMessage(
                role="user",
                content="[Initial request]: Create VrWalk and tag my recent cards",
            )
        ],
    )

    assert resumed is not None and resumed.kind == "answer"
    assert resumed.message == "The tag was saved."
    assert len(provider.calls) == 2
    tool_messages = [message for message in provider.calls[1] if message["role"] == "tool"]
    assert [message["name"] for message in tool_messages] == ["tag", "query_safwa"]
    assert '"status": "approved"' in str(tool_messages[0]["content"])
    assert f'"id": {card.id}' in str(tool_messages[1]["content"])

    async with e2e_harness.sessions() as session:
        run = await session.scalar(select(AgentRun).order_by(AgentRun.id.desc()))
        batch = await session.scalar(
            select(AgentStep)
            .where(AgentStep.run_id == run.id, AgentStep.kind == "approval_batch")
            .order_by(AgentStep.id.desc())
        )
        assert run.status == "completed"
        assert batch.metadata_json["status"] == "completed"


async def test_independent_mutations_are_reviewed_in_order_before_one_resume(e2e_harness):
    advisor, provider = e2e_harness.advisor(
        [
            mutation_turn(
                ("tag", {"mode": "create", "name": "VrWalk"}),
                ("value", {"mode": "create", "name": "Health"}),
            ),
            "Both decisions are resolved.",
        ]
    )
    first = await advisor.handle("Create a VrWalk tag and a Health value")
    assert first.proposal_id is not None

    async with e2e_harness.sessions() as session:
        first_ids = await ProposalService(session).apply(first.proposal_id)
        await session.commit()
    second = await advisor.resolve_approval(
        "proposal",
        first.proposal_id,
        decision="approved",
        result={"affected_ids": first_ids},
        dialogue=[DialogueMessage(role="user", content="[Initial request]: Create both")],
    )

    assert second is not None and second.proposal_id is not None
    assert second.proposal_id != first.proposal_id
    assert len(provider.calls) == 1
    async with e2e_harness.sessions() as session:
        second_ids = await ProposalService(session).apply(second.proposal_id)
        await session.commit()
    final = await advisor.resolve_approval(
        "proposal",
        second.proposal_id,
        decision="approved",
        result={"affected_ids": second_ids},
        dialogue=[DialogueMessage(role="user", content="[Initial request]: Create both")],
    )

    assert final is not None and final.message == "Both decisions are resolved."
    assert len(provider.calls) == 2
    tool_messages = [message for message in provider.calls[1] if message["role"] == "tool"]
    assert len(tool_messages) == 2
    assert all('"status": "approved"' in str(message["content"]) for message in tool_messages)


async def test_discarded_proposal_result_is_returned_with_later_approval(e2e_harness):
    advisor, provider = e2e_harness.advisor(
        [
            mutation_turn(
                ("tag", {"mode": "create", "name": "Skip me"}),
                ("value", {"mode": "create", "name": "Keep me"}),
            ),
            "I kept only the Value.",
        ]
    )
    first = await advisor.handle("Prepare two independent changes")
    assert first.proposal_id is not None
    async with e2e_harness.sessions() as session:
        await ProposalService(session).reject(first.proposal_id)
        await session.commit()
    second = await advisor.resolve_approval(
        "proposal",
        first.proposal_id,
        decision="discarded",
        result={"message": "The user discarded this proposed change."},
        dialogue=[DialogueMessage(role="user", content="[Initial request]: Prepare changes")],
    )
    assert second is not None and second.proposal_id is not None

    async with e2e_harness.sessions() as session:
        affected = await ProposalService(session).apply(second.proposal_id)
        await session.commit()
    final = await advisor.resolve_approval(
        "proposal",
        second.proposal_id,
        decision="approved",
        result={"affected_ids": affected},
        dialogue=[DialogueMessage(role="user", content="[Initial request]: Prepare changes")],
    )

    assert final is not None and final.message == "I kept only the Value."
    tool_messages = [message for message in provider.calls[1] if message["role"] == "tool"]
    assert '"status": "discarded"' in str(tool_messages[0]["content"])
    assert '"status": "approved"' in str(tool_messages[1]["content"])


async def test_new_dialogue_cancels_every_unresolved_item_in_suspended_batch(e2e_harness):
    advisor, provider = e2e_harness.advisor(
        [
            mutation_turn(
                ("tag", {"mode": "create", "name": "VrWalk"}),
                ("value", {"mode": "create", "name": "Health"}),
            )
        ]
    )
    first = await advisor.handle("Prepare two changes")
    assert first.proposal_id is not None

    cancelled = await advisor.cancel_approval_for_target("proposal", first.proposal_id)

    assert cancelled is True
    assert len(provider.calls) == 1
    async with e2e_harness.sessions() as session:
        proposals = list(await session.scalars(select(ChangeProposal).order_by(ChangeProposal.id)))
        run = await session.scalar(select(AgentRun).order_by(AgentRun.id.desc()))
        batch = await session.scalar(
            select(AgentStep).where(
                AgentStep.run_id == run.id,
                AgentStep.kind == "approval_batch",
            )
        )
        assert [proposal.status for proposal in proposals] == ["rejected", "rejected"]
        assert run.status == "cancelled"
        assert batch.metadata_json["status"] == "cancelled"


async def test_query_then_link_continuation_can_suspend_for_a_second_queue(e2e_harness):
    async with e2e_harness.sessions() as session:
        goal = await create_manual_card(
            session, title="Release VrWalk", kind="goal", effort_points=None
        )
        action = await create_manual_card(session, title="Refactor VrWalk design")
        await session.commit()

    first_turn = ProviderTurn(
        content="",
        tool_calls=(
            ProviderToolCall(
                id="tag-create",
                name="tag",
                arguments=json.dumps({"mode": "create", "name": "VrWalk"}),
            ),
            ProviderToolCall(
                id="find-recent",
                name="query_safwa",
                arguments=json.dumps(
                    {"sql": "SELECT id, title FROM ai_cards ORDER BY created_at DESC LIMIT 10"}
                ),
            ),
        ),
    )
    link_turn = mutation_turn(
        ("card", {"mode": "link", "id": goal.id, "tag_query": "VrWalk"}),
        ("card", {"mode": "link", "id": action.id, "tag_query": "VrWalk"}),
    )
    advisor, provider = e2e_harness.advisor(
        [first_turn, link_turn, "VrWalk is now linked to both recent cards."]
    )
    first = await advisor.handle("Create VrWalk and attach it to recent cards")
    assert first.proposal_id is not None

    async with e2e_harness.sessions() as session:
        created_tag_ids = await ProposalService(session).apply(first.proposal_id)
        await session.commit()
    first_link = await advisor.resolve_approval(
        "proposal",
        first.proposal_id,
        decision="approved",
        result={"affected_ids": created_tag_ids},
        dialogue=[DialogueMessage(role="user", content="[Initial request]: Tag recent cards")],
    )
    assert first_link is not None and first_link.proposal_id is not None
    assert len(provider.calls) == 2

    async with e2e_harness.sessions() as session:
        first_link_ids = await ProposalService(session).apply(first_link.proposal_id)
        await session.commit()
    second_link = await advisor.resolve_approval(
        "proposal",
        first_link.proposal_id,
        decision="approved",
        result={"affected_ids": first_link_ids},
        dialogue=[DialogueMessage(role="user", content="[Initial request]: Tag recent cards")],
    )
    assert second_link is not None and second_link.proposal_id is not None
    assert len(provider.calls) == 2

    async with e2e_harness.sessions() as session:
        second_link_ids = await ProposalService(session).apply(second_link.proposal_id)
        await session.commit()
    final = await advisor.resolve_approval(
        "proposal",
        second_link.proposal_id,
        decision="approved",
        result={"affected_ids": second_link_ids},
        dialogue=[DialogueMessage(role="user", content="[Initial request]: Tag recent cards")],
    )
    assert final is not None and final.kind == "answer"
    assert len(provider.calls) == 3

    async with e2e_harness.sessions() as session:
        tag = await session.scalar(select(Tag).where(Tag.name == "VrWalk"))
        assert tag is not None
        assert await session.get(CardTag, {"card_id": goal.id, "tag_id": tag.id}) is not None
        assert await session.get(CardTag, {"card_id": action.id, "tag_id": tag.id}) is not None


class _QueueTestBot:
    def __init__(self) -> None:
        self.typing_calls = 0

    async def send_chat_action(self, _chat_id, _action) -> None:
        self.typing_calls += 1


class _QueueTestMessage:
    def __init__(self) -> None:
        self.message_id = 900
        self.chat = SimpleNamespace(id=700, type="private")
        self.from_user = SimpleNamespace(id=42, is_bot=True)
        self.bot = _QueueTestBot()
        self.text = ""
        self.rendered: list[str] = []

    async def edit_text(self, text, *, reply_markup=None, parse_mode=None):
        del reply_markup, parse_mode
        self.rendered.append(text)
        return self

    async def answer(self, text, *, reply_markup=None, parse_mode=None):
        del reply_markup, parse_mode
        self.rendered.append(text)
        return self


class _QueueTestCallback:
    def __init__(self, token: str, message: _QueueTestMessage) -> None:
        self.data = f"cb:{token}"
        self.message = message

    async def answer(self, text=None, *, show_alert=False) -> None:
        del text, show_alert


class _QueueTestHistory:
    async def dialogue(self, _chat_id):
        return [DialogueMessage(role="user", content="[Initial request]: Create a VrWalk tag")]


@pytest.mark.parametrize(
    ("callback_action", "expected_status", "final_text"),
    [
        ("proposal_approve", "approved", "The VrWalk tag was saved."),
        ("proposal_reject", "rejected", "The VrWalk tag was discarded."),
    ],
)
async def test_single_tag_proposal_save_and_discard_callbacks_resume_agent(
    e2e_harness,
    callback_action,
    expected_status,
    final_text,
):
    advisor, provider = e2e_harness.advisor(
        [
            mutation_turn(("tag", {"mode": "create", "name": "VrWalk"})),
            final_text,
        ]
    )
    outcome = await advisor.handle("Create a VrWalk tag")
    assert outcome.proposal_id is not None
    message = _QueueTestMessage()
    services = SimpleNamespace(
        sessions=e2e_harness.sessions,
        advisor=advisor,
        history=_QueueTestHistory(),
        owner_id=42,
        guard=GenerationGuard(),
    )
    await render_proposal(message, services, outcome.proposal_id)
    async with e2e_harness.sessions() as session:
        token = await session.scalar(
            select(CallbackToken).where(CallbackToken.action == callback_action)
        )
        assert token is not None

    await callback_token_handler(_QueueTestCallback(token.token, message), services)

    async with e2e_harness.sessions() as session:
        proposal = await session.get(ChangeProposal, outcome.proposal_id)
        tag = await session.scalar(select(Tag).where(Tag.name == "VrWalk"))
    assert proposal.status == expected_status
    assert (tag is not None) is (callback_action == "proposal_approve")
    assert message.rendered[-1] == final_text
    assert message.bot.typing_calls == 1
    assert len(provider.calls) == 2


@pytest.mark.parametrize(
    ("callback_action", "expected_status", "resolved_text"),
    [
        ("proposal_approve", "approved", "Saved"),
        ("proposal_reject", "rejected", "Discarded"),
    ],
)
async def test_single_tag_callback_never_leaves_dead_buttons_when_follow_up_fails(
    e2e_harness,
    callback_action,
    expected_status,
    resolved_text,
):
    advisor, provider = e2e_harness.advisor(
        [mutation_turn(("tag", {"mode": "create", "name": "VrWalk"}))]
    )
    outcome = await advisor.handle("Create a VrWalk tag")
    assert outcome.proposal_id is not None
    message = _QueueTestMessage()
    services = SimpleNamespace(
        sessions=e2e_harness.sessions,
        advisor=advisor,
        history=_QueueTestHistory(),
        owner_id=42,
        guard=GenerationGuard(),
    )
    await render_proposal(message, services, outcome.proposal_id)
    async with e2e_harness.sessions() as session:
        token = await session.scalar(
            select(CallbackToken).where(CallbackToken.action == callback_action)
        )
        assert token is not None

    await callback_token_handler(_QueueTestCallback(token.token, message), services)

    async with e2e_harness.sessions() as session:
        proposal = await session.get(ChangeProposal, outcome.proposal_id)
    assert proposal.status == expected_status
    assert resolved_text in message.rendered[-1]
    assert "could not generate its follow-up" in message.rendered[-1]
    assert len(provider.calls) == 2
