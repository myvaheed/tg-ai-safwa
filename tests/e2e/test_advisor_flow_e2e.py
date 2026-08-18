from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, func, select

from safwa.ai.context import SYSTEM_PROMPT, DialogueMessage
from safwa.ai.provider import ProviderToolCall, ProviderTurn
from safwa.ai.service import AIOutcome, ProposalService
from safwa.analytics import render_retrospective_png, retrospective_data
from safwa.constants import MAX_TOOL_CALLS
from safwa.domain import (
    StaleStateError,
    create_card,
    create_saved_request,
    create_tag,
    finish_action,
    finish_sprint,
    move_card,
    sprint_metrics,
    start_sprint,
    utcnow,
)
from safwa.enums import CardStage
from safwa.models import (
    AgentRun,
    AgentStep,
    CallbackToken,
    Card,
    CardCategory,
    CardEnergyType,
    CardTag,
    CardValue,
    ChangeProposal,
    FeedbackQueue,
    ProposalChange,
    SavedRequest,
    Tag,
    Value,
    Workspace,
)
from safwa.saved_requests import request_cards
from safwa.telegram import (
    GenerationGuard,
    callback_token_handler,
    dismiss_prior_ui,
    render_proposal,
)

pytestmark = pytest.mark.e2e


async def create_manual_card(session, **overrides) -> Card:
    payload = {
        "title": "Action",
        "kind": "action",
        "stage": "backlog",
        "effort_points": 3,
    }
    payload.update(overrides)
    payload.pop("root_confirmed", None)
    payload.pop("expected_parent_version", None)
    return await create_card(session, **payload)


def mutation_turn(
    *calls: tuple[str, dict[str, object]], prefix: str = "mutation"
) -> ProviderTurn:
    return ProviderTurn(
        content="",
        tool_calls=tuple(
            ProviderToolCall(
                id=f"{prefix}-{index}", name=name, arguments=json.dumps(arguments)
            )
            for index, (name, arguments) in enumerate(calls, start=1)
        ),
    )


async def test_placeholder_heavy_card_tool_payload_stays_a_root_action(e2e_harness):
    response = mutation_turn(
        (
            "card",
            {
                "mode": "create",
                "id": 0,
                "kind": "action",
                "title": "Подтянуться 20 раз",
                "note": "",
                "stage": "backlog",
                "priority": "medium",
                "hard_time": False,
                "blocked": False,
                "blocked_description": "",
                "effort_points": 1,
                "repeatable": False,
                "categories": ["self"],
                "energy_types": ["physical"],
                "value_id": 0,
                "value_ids": [],
                "value_query": "",
                "tag_id": 0,
                "tag_ids": [],
                "tag_query": "",
                "check_id": 0,
                "check_ids": [],
                "check_query": "",
                "parent_id": None,
                "parent_query": "",
            },
        )
    )
    advisor, _provider = e2e_harness.advisor([response])

    outcome = await advisor.handle("Сделай один Action: подтянуться 20 раз")

    assert outcome.kind == "proposal"
    async with e2e_harness.sessions() as session:
        change = await session.scalar(
            select(ProposalChange).where(ProposalChange.proposal_id == outcome.proposal_id)
        )
        assert change.values["title"] == "Подтянуться 20 раз"
        assert "parent_id" not in change.values
        assert "parent_query" not in change.values


async def test_invalid_create_returns_minimal_repair_arguments_to_the_model(e2e_harness):
    invalid = mutation_turn(
        (
            "card",
            {
                "mode": "create",
                "id": 1,
                "kind": "action",
                "title": "Подтянуться 20 раз",
                "note": "",
                "stage": "backlog",
                "priority": "medium",
                "hard_time": False,
                "blocked": False,
                "blocked_description": "",
                "effort_points": 1,
                "repeatable": False,
                "categories": ["self"],
                "energy_types": ["physical"],
                "value_id": 1,
                "value_ids": [],
                "value_query": "",
                "tag_id": 1,
                "tag_ids": [],
                "tag_query": "",
                "check_id": 1,
                "check_ids": [],
                "check_query": "",
                "parent_id": None,
                "parent_query": "",
            },
        )
    )
    repaired = mutation_turn(
        (
            "card",
            {
                "mode": "create",
                "kind": "action",
                "title": "Подтянуться 20 раз",
                "stage": "backlog",
                "priority": "medium",
                "hard_time": False,
                "blocked": False,
                "effort_points": 1,
                "repeatable": False,
                "categories": ["self"],
                "energy_types": ["physical"],
            },
        )
    )
    advisor, provider = e2e_harness.advisor([invalid, repaired])

    outcome = await advisor.handle("Сделай один Action: подтянуться 20 раз")

    assert outcome.kind == "proposal"
    repair_result = next(
        json.loads(str(message["content"]))
        for message in provider.calls[1]
        if message.get("role") == "tool"
    )
    assert repair_result["code"] == "invalid_arguments"
    assert repair_result["expected_arguments"] == {
        "mode": "create",
        "kind": "action",
        "title": "Подтянуться 20 раз",
        "stage": "backlog",
        "priority": "medium",
        "hard_time": False,
        "blocked": False,
        "effort_points": 1,
        "repeatable": False,
        "categories": ["self"],
        "energy_types": ["physical"],
    }
    assert "id" not in repair_result["expected_arguments"]
    assert "value_id" not in repair_result["expected_arguments"]
    assert any("placeholder 0 or 1" in rule for rule in repair_result["argument_rules"])
    assert "pydantic.dev" not in repair_result["error"]


async def test_ai_parent_query_sql_resolves_before_card_proposal(e2e_harness):
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
                "mode": "create",
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
        change = await session.scalar(
            select(ProposalChange).where(ProposalChange.proposal_id == outcome.proposal_id)
        )
        assert change.action == "create"
        assert change.values["parent_id"] == parent.id
        assert "parent_query" not in change.values
        assert await session.scalar(select(func.count(Card.id))) == 1


async def test_ai_goal_proposal_reports_a_parent_instead_of_dropping_it(e2e_harness):
    async with e2e_harness.sessions() as session:
        parent = await create_manual_card(
            session, title="Ship product", kind="goal", effort_points=None
        )
        await session.commit()

    advisor, _provider = e2e_harness.advisor(
        [
            mutation_turn(
                (
                    "card",
                    {
                        "mode": "create",
                        "kind": "goal",
                        "title": "Nested Goal",
                        "parent_id": parent.id,
                    },
                )
            ),
            "A Goal has to stay root-level, so I left it there.",
        ]
    )

    outcome = await advisor.handle("Create a Goal under Ship product")

    # Silently dropping the parent would show a review screen with no parent change
    # and never tell the model its call was wrong.
    assert outcome.proposal_id is None
    async with e2e_harness.sessions() as session:
        assert await session.scalar(select(func.count(ChangeProposal.id))) == 0
    tool_messages = [
        message
        for call in _provider.calls
        for message in call
        if message.get("role") == "tool"
    ]
    assert any("root-level" in str(message["content"]) for message in tool_messages)


async def test_ai_stage_update_to_done_keeps_completion_accounting(e2e_harness):
    async with e2e_harness.sessions() as session:
        action = await create_manual_card(session, title="Ship", stage="sprint", effort_points=5)
        sprint = await start_sprint(session, success_criteria="Ship the release")
        await session.commit()
        action_id, sprint_id = action.id, sprint.id

    advisor, _provider = e2e_harness.advisor(
        [
            mutation_turn(
                ("card", {"mode": "update", "id": action_id, "title": "Ship it", "note": "Done"})
            ),
            "Saved.",
        ]
    )
    outcome = await advisor.handle("Rename it")
    assert outcome.proposal_id is not None

    async with e2e_harness.sessions() as session:
        change = await session.scalar(
            select(ProposalChange).where(ProposalChange.proposal_id == outcome.proposal_id)
        )
        # An approved stage change routes terminal stages through finish_action, so the
        # completion timestamp, feedback item and Sprint result are never skipped.
        change.values = {**change.values, "stage": CardStage.DONE.value}
        await session.commit()

    async with e2e_harness.sessions() as session:
        await ProposalService(session).apply(outcome.proposal_id)
        await session.commit()

    async with e2e_harness.sessions() as session:
        stored = await session.get(Card, action_id)
        feedback = await session.scalar(
            select(FeedbackQueue).where(FeedbackQueue.card_id == action_id)
        )
        assert stored.effective_stage == CardStage.DONE.value
        assert stored.completed_at is not None
        assert feedback is not None
        assert (await sprint_metrics(session, sprint_id))["completed"] == 5


async def test_ai_parent_query_rejects_non_ai_card_sql(
    e2e_harness,
):
    response = mutation_turn(
        (
            "card",
            {
                "mode": "create",
                "kind": "action",
                "title": "Unsafe parent lookup",
                "parent_query": "SELECT id FROM cards WHERE title = 'Hidden table'",
                "effort_points": 2,
            },
        )
    )
    advisor, provider = e2e_harness.advisor(
        [response, "I could not safely resolve that parent, so nothing was proposed."]
    )

    outcome = await advisor.handle("Create an action under that parent")

    assert outcome.kind == "answer"
    assert len(provider.calls) == 2
    tool_result = json.loads(str(provider.calls[1][-1]["content"]))
    assert tool_result["code"] == "unsafe_query"
    assert "read-only SELECT over ai_cards" in tool_result["hint"]


async def test_ai_card_proposal_to_repeat_sprint_and_retrospective(e2e_harness):
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
                "mode": "create",
                "kind": "action",
                "title": "Push ups 30 times",
                "parent_query": "SELECT id FROM ai_cards WHERE title = 'To be fit'",
                "repeatable": True,
                "categories": ["self"],
                "energy_types": ["physical"],
                "value_query": "Fitness",
                "tag_query": "Family",
                "effort_points": 2,
            },
        )
    )
    advisor, provider = e2e_harness.advisor([response])
    outcome: AIOutcome = await advisor.handle(
        "Please create a new action Push ups 30 times and link it to To be fit goal"
    )

    assert outcome.kind == "proposal"
    assert outcome.proposal_id is not None
    assert len(provider.calls) == 1
    assert not provider.responses

    async with e2e_harness.sessions() as session:
        assert await session.scalar(select(func.count(Card.id))) == 1
        affected = await ProposalService(session).apply(outcome.proposal_id)
        await session.commit()
        action = await session.get(Card, affected[0])

        assert action is not None
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
        sprint = await start_sprint(session, success_criteria="Ship the release", capacity=8)
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
        await start_sprint(session, success_criteria="Ship the release", capacity=8)
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


async def test_a_turn_that_stops_without_words_is_asked_again(e2e_harness):
    query_response = ProviderTurn(
        content="",
        tool_calls=(
            ProviderToolCall(
                id="read-1",
                name="query_safwa",
                arguments=json.dumps({"sql": "SELECT id FROM ai_cards"}),
            ),
        ),
    )
    advisor, provider = e2e_harness.advisor(
        [query_response, "", "Isha is the night prayer."]
    )
    outcome = await advisor.handle("What is isha?")

    assert outcome.kind == "answer"
    assert outcome.message == "Isha is the night prayer."
    assert len(provider.calls) == 3
    nudge = str(provider.calls[2][-1]["content"])
    assert nudge.startswith("[System]: You stopped without answering.")
    assert provider.calls[2][-2]["role"] == "tool"


async def test_a_turn_that_never_finds_words_still_reaches_the_owner(e2e_harness):
    advisor, provider = e2e_harness.advisor([""] * 6)
    outcome = await advisor.handle("What is isha?")

    assert outcome.kind == "answer"
    assert outcome.message == "⚠️ Safwa had nothing to say about that. You can ask again."
    assert len(provider.calls) == 6


async def test_read_and_mutation_in_one_turn_rejects_only_the_mutation(e2e_harness):
    mixed = ProviderTurn(
        content="",
        tool_calls=(
            ProviderToolCall(
                id="mixed-read",
                name="query_safwa",
                arguments=json.dumps({"sql": "SELECT id FROM ai_cards LIMIT 1"}),
            ),
            ProviderToolCall(
                id="mixed-write",
                name="card",
                arguments=json.dumps(
                    {"mode": "create", "kind": "goal", "title": "Be healthy"}
                ),
            ),
        ),
    )
    repaired = mutation_turn(
        ("card", {"mode": "create", "kind": "goal", "title": "Be healthy"}),
        prefix="after-read",
    )
    advisor, provider = e2e_harness.advisor([mixed, repaired])

    outcome = await advisor.handle("Inspect my cards, then create the unrelated health goal")

    assert outcome.proposal_id is not None
    tool_results = {
        item["name"]: json.loads(item["content"])
        for item in provider.calls[1]
        if item.get("role") == "tool"
    }
    assert isinstance(tool_results["query_safwa"], list)
    assert tool_results["card"]["code"] == "mixed_read_and_mutation_tools"
    assert tool_results["card"]["retryable"] is True


async def test_multiple_ai_card_creations_are_reviewed_sequentially(e2e_harness):
    first_turn = mutation_turn(
        ("card", {"mode": "create", "kind": "goal", "title": "Быть здоровым"}),
        (
            "card",
            {
                "mode": "create",
                "kind": "action",
                "title": "Подтягиваться 20 раз",
                "effort_points": 1,
                "parent_query": "SELECT id FROM ai_cards WHERE title = 'Быть здоровым'",
            },
        ),
        (
            "card",
            {
                "mode": "create",
                "kind": "action",
                "title": "Гулять утром",
                "effort_points": 2,
                "parent_query": "SELECT id FROM ai_cards WHERE title = 'Быть здоровым'",
            },
        ),
    )
    repaired_turn = mutation_turn(
        (
            "card",
            {
                "mode": "create",
                "kind": "action",
                "title": "Подтягиваться 20 раз",
                "effort_points": 1,
                "parent_id": 1,
            },
        ),
        (
            "card",
            {
                "mode": "create",
                "kind": "action",
                "title": "Гулять утром",
                "effort_points": 2,
                "parent_id": 1,
            },
        ),
    )
    advisor, provider = e2e_harness.advisor(
        [first_turn, repaired_turn, "The three cards were reviewed."]
    )
    current = await advisor.handle("Создай цель Быть здоровым и два действия под нее")
    proposal_ids: list[int] = []

    async with e2e_harness.sessions() as session:
        batch = await session.scalar(
            select(AgentStep).where(AgentStep.kind == "approval_batch")
        )
        assert batch is not None
        tool_results = batch.metadata_json["tool_calls"]
        assert tool_results[0]["status"] == "pending"
        assert [item["result"]["code"] for item in tool_results[1:]] == [
            "reference_not_found",
            "reference_not_found",
        ]

    for _index in range(3):
        assert current.proposal_id is not None
        proposal_ids.append(current.proposal_id)
        async with e2e_harness.sessions() as session:
            affected = await ProposalService(session).apply(current.proposal_id)
            await session.commit()
        current = await advisor.resolve_approval(
            "proposal",
            proposal_ids[-1],
            decision="approved",
            result={"affected_ids": affected},
            dialogue=[DialogueMessage(role="user", content="[Initial request]: Create cards")],
        )
        assert current is not None
        if len(proposal_ids) == 1:
            assert "⚠️ Failed" not in current.message

    assert len(set(proposal_ids)) == 3
    assert current.kind == "answer"
    assert "The three cards were reviewed." in current.message
    assert len(provider.calls) == 3
    first_results = [
        json.loads(str(message["content"]))
        for message in provider.calls[1]
        if message["role"] == "tool"
    ]
    assert first_results[0]["status"] == "approved"
    assert first_results[0]["affected_ids"] == [1]
    assert [result["code"] for result in first_results[1:]] == [
        "reference_not_found",
        "reference_not_found",
    ]
    # The last continuation still sees every step of the request, each as its own call
    # and result rather than as a digest restated on the assistant turn.
    final_results = [
        json.loads(str(message["content"]))
        for message in provider.calls[2]
        if message["role"] == "tool"
    ]
    assert [result.get("status") for result in final_results] == [
        "approved",
        "error",
        "error",
        "approved",
        "approved",
    ]
    assert final_results[0]["summary"] == "Create Card “Быть здоровым”"
    assert final_results[0]["affected_ids"] == [1]
    assert final_results[1]["code"] == "reference_not_found"
    assert final_results[3]["summary"] == "Create Card “Подтягиваться 20 раз”"
    assert final_results[3]["affected_ids"] == [2]
    assert "Kind: action" in final_results[3]["fields"]
    assert "Parent ID: 1" in final_results[3]["fields"]
    assert "Effort: 1" in final_results[3]["fields"]
    assert "Do not propose it again" in final_results[3]["next"]
    async with e2e_harness.sessions() as session:
        cards = list(await session.scalars(select(Card).order_by(Card.id)))
        assert [card.title for card in cards] == [
            "Быть здоровым",
            "Подтягиваться 20 раз",
            "Гулять утром",
        ]
        goal = cards[0]
        assert {card.parent_id for card in cards[1:]} == {goal.id}


async def test_current_request_progress_includes_current_card_update_diffs(e2e_harness):
    async with e2e_harness.sessions() as session:
        card = await create_manual_card(
            session,
            title="Evening walk",
            note="Before dinner",
            effort_points=2,
            categories={"self"},
        )
        await session.commit()

    response = mutation_turn(
        (
            "card",
            {
                "mode": "update",
                "id": card.id,
                "note": "After dinner",
                "categories": ["rest"],
            },
        )
    )
    advisor, provider = e2e_harness.advisor([response, "The Action was updated."])
    proposal = await advisor.handle("Move my walk after dinner and make it Rest")
    assert proposal.proposal_id is not None
    async with e2e_harness.sessions() as session:
        affected = await ProposalService(session).apply(proposal.proposal_id)
        await session.commit()

    outcome = await advisor.resolve_approval(
        "proposal",
        proposal.proposal_id,
        decision="approved",
        result={"affected_ids": affected},
        dialogue=[DialogueMessage(role="user", content="[Initial request]: Update my walk")],
    )

    assert outcome is not None and outcome.kind == "answer"
    saved = json.loads(
        str(next(message for message in provider.calls[1] if message["role"] == "tool")["content"])
    )
    assert saved["status"] == "approved"
    assert saved["affected_ids"] == [card.id]
    assert saved["summary"] == f"Update Card #{card.id}"
    assert "Note: Before dinner → After dinner" in saved["fields"]
    assert "Categories: self → rest" in saved["fields"]


async def test_child_proposal_fails_cleanly_when_earlier_parent_is_discarded(e2e_harness):
    response = mutation_turn(
        ("card", {"mode": "create", "kind": "goal", "title": "Be healthy"}),
        (
            "card",
            {
                "mode": "create",
                "kind": "action",
                "title": "Do twenty pull-ups",
                "effort_points": 1,
                "parent_query": "SELECT id FROM ai_cards WHERE title = 'Be healthy'",
            },
        ),
    )
    advisor, provider = e2e_harness.advisor(
        [response, "The Goal was discarded, so I did not retry its child Action."]
    )
    first = await advisor.handle("Create a health goal and a child action")
    assert first.proposal_id is not None

    async with e2e_harness.sessions() as session:
        await ProposalService(session).reject(first.proposal_id)
        await session.commit()
    second = await advisor.resolve_approval(
        "proposal",
        first.proposal_id,
        decision="rejected",
        result={},
        dialogue=[DialogueMessage(role="user", content="[Initial request]: Create cards")],
    )

    assert second is not None and second.kind == "answer"
    assert "did not retry" in second.message
    assert len(provider.calls) == 2
    continuation_results = [
        json.loads(str(message["content"]))
        for message in provider.calls[1]
        if message["role"] == "tool"
    ]
    assert continuation_results[0]["status"] == "rejected"
    assert continuation_results[1]["code"] == "reference_not_found"
    async with e2e_harness.sessions() as session:
        assert await session.scalar(select(func.count(Card.id))) == 0
        assert await session.scalar(select(func.count(ChangeProposal.id))) == 1


async def test_new_tag_and_dependent_card_link_use_one_repair_round(e2e_harness):
    async with e2e_harness.sessions() as session:
        card = await create_manual_card(session, title="Configure environment")
        await session.commit()

    first_turn = mutation_turn(
        ("tag", {"mode": "create", "name": "VrWalk"}),
        ("card", {"mode": "link", "id": card.id, "tag_query": "VrWalk"}),
    )
    repaired_turn = mutation_turn(
        ("card", {"mode": "link", "id": card.id, "tag_id": 1}),
    )
    advisor, provider = e2e_harness.advisor(
        [first_turn, repaired_turn, "The Tag was created and linked to the Card."]
    )

    tag_proposal = await advisor.handle("Create VrWalk and link it to Configure environment")
    assert tag_proposal.proposal_id is not None
    async with e2e_harness.sessions() as session:
        tag_ids = await ProposalService(session).apply(tag_proposal.proposal_id)
        await session.commit()

    card_proposal = await advisor.resolve_approval(
        "proposal",
        tag_proposal.proposal_id,
        decision="approved",
        result={"affected_ids": tag_ids},
        dialogue=[DialogueMessage(role="user", content="[Initial request]: Create and link tag")],
    )
    assert card_proposal is not None and card_proposal.proposal_id is not None
    async with e2e_harness.sessions() as session:
        affected = await ProposalService(session).apply(card_proposal.proposal_id)
        await session.commit()

    outcome = await advisor.resolve_approval(
        "proposal",
        card_proposal.proposal_id,
        decision="approved",
        result={"affected_ids": affected},
        dialogue=[DialogueMessage(role="user", content="[Initial request]: Create and link tag")],
    )

    assert outcome is not None and outcome.kind == "answer"
    assert len(provider.calls) == 3
    first_continuation_results = [
        json.loads(str(message["content"]))
        for message in provider.calls[1]
        if message["role"] == "tool"
    ]
    assert first_continuation_results[0]["affected_ids"] == [1]
    assert first_continuation_results[1]["code"] == "reference_not_found"
    async with e2e_harness.sessions() as session:
        assert await session.get(CardTag, {"card_id": card.id, "tag_id": 1}) is not None


async def test_mutation_repair_loop_stops_after_five_rounds(e2e_harness):
    invalid_turn = mutation_turn(
        (
            "card",
            {
                "mode": "create",
                "kind": "action",
                "title": "Child without saved parent",
                "effort_points": 1,
                "parent_query": "SELECT id FROM ai_cards WHERE title = 'Still missing'",
            },
        )
    )
    advisor, provider = e2e_harness.advisor([invalid_turn] * 6)

    outcome = await advisor.handle("Keep trying an unavailable parent")

    assert outcome.kind == "answer"
    assert "five repair attempts" in outcome.message
    assert len(provider.calls) == 6
    async with e2e_harness.sessions() as session:
        assert await session.scalar(select(func.count(ChangeProposal.id))) == 0
        assert await session.scalar(select(func.count(Card.id))) == 0


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


async def test_ai_create_tag_and_links_are_reviewed_as_separate_proposals(e2e_harness):
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
    repaired_turn = mutation_turn(
        ("card", {"mode": "link", "id": goal.id, "tag_id": 1}),
        ("card", {"mode": "link", "id": action.id, "tag_id": 1}),
    )
    advisor, provider = e2e_harness.advisor(
        [read_turn, response, repaired_turn, "All links are resolved."]
    )
    current = await advisor.handle("Create VrWalk and attach it to my recent cards")
    proposal_ids: list[int] = []
    for _expected in range(3):
        assert current.proposal_id is not None
        proposal_ids.append(current.proposal_id)
        async with e2e_harness.sessions() as session:
            changes = list(
                await session.scalars(
                    select(ProposalChange).where(ProposalChange.proposal_id == current.proposal_id)
                )
            )
            assert len(changes) == 1
            affected = await ProposalService(session).apply(current.proposal_id)
            await session.commit()
        current = await advisor.resolve_approval(
            "proposal",
            proposal_ids[-1],
            decision="approved",
            result={"affected_ids": affected},
            dialogue=[DialogueMessage(role="user", content="[Initial request]: Link recent cards")],
        )
        assert current is not None

    assert len(set(proposal_ids)) == 3
    assert current.kind == "answer"
    assert "✅ Saved — Link Tag “VrWalk” to Goal “Release VrWalk”" in current.message
    assert current.message.count("✅ Saved") == 3
    assert "All links are resolved." in current.message
    assert len(provider.calls) == 4
    async with e2e_harness.sessions() as session:
        tag = await session.scalar(select(Tag).where(Tag.name == "VrWalk"))
        assert tag is not None
        for card_id in (goal.id, action.id):
            assert await session.get(CardTag, {"card_id": card_id, "tag_id": tag.id}) is not None


async def test_ai_create_value_and_link_are_reviewed_as_separate_proposals(e2e_harness):
    async with e2e_harness.sessions() as session:
        action = await create_manual_card(session, title="Morning run", effort_points=2)
        await session.commit()

    response = mutation_turn(
        ("value", {"mode": "create", "name": "Health", "active": True}),
        ("card", {"mode": "link", "id": action.id, "value_query": "Health"}),
    )
    repaired_turn = mutation_turn(
        ("card", {"mode": "link", "id": action.id, "value_id": 1}),
    )
    advisor, provider = e2e_harness.advisor(
        [response, repaired_turn, "Health is resolved."]
    )
    first = await advisor.handle("Create Health and link it to Morning run")
    assert first.proposal_id is not None
    async with e2e_harness.sessions() as session:
        first_ids = await ProposalService(session).apply(first.proposal_id)
        await session.commit()
    second = await advisor.resolve_approval(
        "proposal",
        first.proposal_id,
        decision="approved",
        result={"affected_ids": first_ids},
        dialogue=[DialogueMessage(role="user", content="[Initial request]: Create Health")],
    )
    assert second is not None and second.proposal_id is not None
    assert second.proposal_id != first.proposal_id
    assert len(provider.calls) == 2

    async with e2e_harness.sessions() as session:
        second_ids = await ProposalService(session).apply(second.proposal_id)
        await session.commit()
    final = await advisor.resolve_approval(
        "proposal",
        second.proposal_id,
        decision="approved",
        result={"affected_ids": second_ids},
        dialogue=[DialogueMessage(role="user", content="[Initial request]: Create Health")],
    )

    assert final is not None and "Health is resolved." in final.message
    assert final.message.count("✅ Saved") == 2
    assert len(provider.calls) == 3
    async with e2e_harness.sessions() as session:
        value = await session.scalar(select(Value).where(Value.name == "Health"))
        assert value is not None and value.active is True
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
            {"mode": "update", "id": request.id, "description": "Every active Goal."},
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


async def test_cacheable_prefix_is_byte_stable_across_turns(e2e_harness):
    dialogue = [DialogueMessage(role="user", content="[User]: What is next?")]
    advisor, provider = e2e_harness.advisor(["First.", "Second."])

    await advisor.handle("What is next?", dialogue=dialogue)
    await advisor.handle("What is next?", dialogue=dialogue)

    first, second = provider.calls
    # Only the trailing clock may differ; everything before it must be reusable.
    assert first[:-1] == second[:-1]
    assert str(first[-1]["content"]).startswith("[System]: Current local time:")


async def test_cache_breakpoints_mark_exactly_the_stable_prefix(e2e_harness):
    dialogue = [
        DialogueMessage(role="user", content="[Initial request]: Plan this week."),
        DialogueMessage(role="assistant", content="What matters most?"),
        DialogueMessage(role="user", content="[User]: Health."),
    ]
    advisor, provider = e2e_harness.advisor(["Noted."], cache_breakpoints=True)

    await advisor.handle("Health.", dialogue=dialogue)

    messages = provider.calls[0]
    marked = [index for index, message in enumerate(messages) if isinstance(message["content"], list)]
    assert marked == [0, 1, len(messages) - 2]
    assert messages[0]["content"] == [
        {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
    ]
    assert isinstance(messages[-1]["content"], str)


async def test_cache_breakpoints_are_absent_by_default(e2e_harness):
    advisor, provider = e2e_harness.advisor(["Noted."])

    await advisor.handle("Hi", dialogue=[DialogueMessage(role="user", content="[User]: Hi")])

    assert all(isinstance(message["content"], str) for message in provider.calls[0])


async def test_advisor_sends_layered_system_blocks_and_canonical_dialogue(e2e_harness):
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
    # Only messages[0] may be a system message; later blocks travel as owner text.
    assert [message["role"] for message in messages] == [
        "system",
        "user",
        "user",
        "assistant",
        "user",
        "user",
    ]
    assert messages[-2]["content"] == dialogue[-1].content
    # The volatile clock is the last block so the prefix before it stays cacheable.
    assert messages[-1]["content"].startswith("[System]: Current local time:")
    system = str(messages[0]["content"])
    assert system == SYSTEM_PROMPT
    assert "query_safwa" in system
    assert "ai_cards(id, title" in system
    assert "Current local time" not in system
    assert "Current local time" not in str(messages[1]["content"])
    assert "Workspace revision:" not in system
    assert "Saved Requests:" not in system
    assert "Sprint cards:" not in system
    assert "Recent cards" not in system
    assert "Lexical card candidates" not in system
    tools = provider.options[0]["tools"]
    # The Advisor reads and routes; every mutation tool belongs to the subagent that owns it.
    assert isinstance(tools, list) and [tool["function"]["name"] for tool in tools] == [
        "query_safwa",
        "route",
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
    advisor, provider = e2e_harness.advisor(
        [
            mixed_turn,
            mutation_turn(("tag", {"mode": "create", "name": "VrWalk"})),
            "The tag was saved.",
        ]
    )

    outcome = await advisor.handle("Create VrWalk and tag my recent cards")

    assert outcome.proposal_id is not None
    assert len(provider.calls) == 2
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
    assert "✅ Saved — New Tag “VrWalk”" in resumed.message
    assert "The tag was saved." in resumed.message
    assert len(provider.calls) == 3
    tool_messages = [message for message in provider.calls[1] if message["role"] == "tool"]
    assert [message["name"] for message in tool_messages] == ["tag", "query_safwa"]
    assert "mixed_read_and_mutation_tools" in str(tool_messages[0]["content"])
    assert f'"id": {card.id}' in str(tool_messages[1]["content"])
    approved_messages = [
        message
        for message in provider.calls[2]
        if message["role"] == "tool" and message["name"] == "tag"
    ]
    assert '"status": "approved"' in str(approved_messages[-1]["content"])

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

    assert final is not None and "Both decisions are resolved." in final.message
    assert final.message.count("✅ Saved") == 2
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

    assert final is not None and "I kept only the Value." in final.message
    assert "🗑 Discarded — New Tag “Skip me”" in final.message
    assert "✅ Saved — New Value “Keep me”" in final.message
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

    # The caller freezes the screen with this text, so it has to name every item.
    assert "🗑 Discarded — New Tag “VrWalk”" in cancelled
    assert "🗑 Discarded — New Value “Health”" in cancelled
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
        assert batch.metadata_json["status"] == "cancelled"
        # The screen is frozen, but the session that wrote it stays resumable: the owner's
        # next words may well be a correction to exactly these two changes.
        assert (run.kind, run.status) == ("board", "awaiting_approval")


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
        [
            first_turn,
            mutation_turn(("tag", {"mode": "create", "name": "VrWalk"})),
            link_turn,
            "VrWalk is now linked to both recent cards.",
        ]
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
    assert len(provider.calls) == 3

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
    assert len(provider.calls) == 3

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
    assert len(provider.calls) == 4

    async with e2e_harness.sessions() as session:
        tag = await session.scalar(select(Tag).where(Tag.name == "VrWalk"))
        assert tag is not None
        assert await session.get(CardTag, {"card_id": goal.id, "tag_id": tag.id}) is not None
        assert await session.get(CardTag, {"card_id": action.id, "tag_id": tag.id}) is not None


class _QueueTestBot:
    def __init__(self) -> None:
        self.typing_calls = 0
        self.edits: list[str] = []
        self.deleted: list[int] = []

    async def send_chat_action(self, _chat_id, _action) -> None:
        self.typing_calls += 1

    async def edit_message_text(
        self, text, *, chat_id, message_id, reply_markup=None, parse_mode=None
    ) -> None:
        del chat_id, message_id, reply_markup, parse_mode
        self.edits.append(text)

    async def delete_message(self, chat_id, message_id) -> None:
        del chat_id
        self.deleted.append(message_id)

    async def edit_message_reply_markup(self, *, chat_id, message_id, reply_markup=None) -> None:
        del chat_id, message_id, reply_markup


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
        raise AssertionError("approval resume must use its persisted dialogue")


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
    expected_result = "✅ Saved" if callback_action == "proposal_approve" else "🗑 Discarded"
    assert f"{expected_result} — New Tag “VrWalk”" in message.rendered[-1]
    assert final_text in message.rendered[-1]
    assert message.bot.typing_calls == 1
    assert len(provider.calls) == 2


async def test_read_queries_beside_a_proposal_still_resume_the_agent(e2e_harness):
    """A read call in the same turn stores rows, not an outcome; the receipt must survive it."""
    async with e2e_harness.sessions() as session:
        await create_manual_card(session, title="Выпустить в прод VrWalk")
        await session.commit()

    advisor, provider = e2e_harness.advisor(
        [
            mutation_turn(
                ("tag", {"mode": "create", "name": "VrWalk"}),
                ("query_safwa", {"sql": "SELECT missing_column FROM ai_cards"}),
                ("query_safwa", {"sql": "SELECT id FROM ai_cards"}),
            ),
            mutation_turn(("tag", {"mode": "create", "name": "VrWalk"})),
            "The VrWalk tag was saved; I will retry the query.",
        ]
    )
    outcome = await advisor.handle("Create a VrWalk tag and apply it to the tree")
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
            select(CallbackToken).where(CallbackToken.action == "proposal_approve")
        )
        assert token is not None

    await callback_token_handler(_QueueTestCallback(token.token, message), services)

    async with e2e_harness.sessions() as session:
        proposal = await session.get(ChangeProposal, outcome.proposal_id)
        tag = await session.scalar(select(Tag).where(Tag.name == "VrWalk"))
    assert proposal.status == "approved"
    assert tag is not None
    assert "✅ Saved — New Tag “VrWalk”" in message.rendered[-1]
    assert "The VrWalk tag was saved; I will retry the query." in message.rendered[-1]
    assert "could not generate its follow-up" not in message.rendered[-1]
    assert len(provider.calls) == 3
    resumed_query_results = [
        str(item["content"])
        for item in provider.calls[-1]
        if item.get("role") == "tool" and item.get("name") == "query_safwa"
    ]
    # The model can only repair the read if the failure came back as a tool result.
    assert "missing_column" in resumed_query_results[0]
    assert "id" in resumed_query_results[1]


async def _resolve_queued_proposal(
    e2e_harness, services, message, proposal_id: int, action: str
) -> None:
    async with e2e_harness.sessions() as session:
        token = next(
            candidate
            for candidate in await session.scalars(
                select(CallbackToken).where(
                    CallbackToken.action == action,
                    CallbackToken.consumed_at.is_(None),
                )
            )
            if candidate.payload["id"] == proposal_id
        )
    await callback_token_handler(_QueueTestCallback(token.token, message), services)


async def test_discarding_the_last_queued_proposal_still_reports_saved_siblings(e2e_harness):
    advisor, provider = e2e_harness.advisor(
        [
            mutation_turn(
                ("card", {"mode": "create", "kind": "action", "title": "First", "effort_points": 3}),
                (
                    "card",
                    {"mode": "create", "kind": "action", "title": "Second", "effort_points": 5},
                ),
            ),
            "Handled both proposals.",
        ]
    )
    first = await advisor.handle("Create two actions")
    assert first.proposal_id is not None
    message = _QueueTestMessage()
    services = SimpleNamespace(
        sessions=e2e_harness.sessions,
        advisor=advisor,
        history=_QueueTestHistory(),
        owner_id=42,
        guard=GenerationGuard(),
    )
    await render_proposal(message, services, first.proposal_id)

    await _resolve_queued_proposal(
        e2e_harness, services, message, first.proposal_id, "proposal_approve"
    )
    async with e2e_harness.sessions() as session:
        second_id = await session.scalar(
            select(ChangeProposal.id)
            .where(ChangeProposal.status == "pending")
            .order_by(ChangeProposal.id)
        )
    assert second_id is not None

    await _resolve_queued_proposal(e2e_harness, services, message, second_id, "proposal_reject")

    final_text = message.rendered[-1]
    # The model must be told what the whole request actually did, not only the last step.
    assert "✅ Saved — New Action “First”" in final_text
    assert "🗑 Discarded — New Action “Second”" in final_text
    assert "Handled both proposals." in final_text
    assert len(provider.calls) == 2


async def test_failed_call_result_states_that_its_siblings_are_still_queued(e2e_harness):
    async with e2e_harness.sessions() as session:
        card = await create_manual_card(session, title="Release VrWalk", effort_points=3)
        await session.commit()

    advisor, _provider = e2e_harness.advisor(
        [
            mutation_turn(
                ("tag", {"mode": "create", "name": "VrWalk"}),
                ("card", {"mode": "link", "id": card.id, "tag_query": "VrWalk"}),
            )
        ]
    )
    outcome = await advisor.handle("Create VrWalk and link it")
    assert outcome.proposal_id is not None

    async with e2e_harness.sessions() as session:
        batch = await session.scalar(
            select(AgentStep).where(AgentStep.kind == "approval_batch")
        )
        failed = next(
            tool for tool in batch.metadata_json["tool_calls"] if tool["target"] is None
        )
        queued = [tool for tool in batch.metadata_json["tool_calls"] if tool["target"]]

    # The prompt no longer explains sibling semantics every turn; the failing call says it.
    assert failed["result"]["status"] == "error"
    assert "were not cancelled" in failed["result"]["next"]
    assert f"{len(queued)} other call(s)" in failed["result"]["next"]


async def test_new_message_discarding_a_queue_reports_what_was_already_saved(e2e_harness):
    advisor, provider = e2e_harness.advisor(
        [
            mutation_turn(
                ("card", {"mode": "create", "kind": "action", "title": "First", "effort_points": 3}),
                (
                    "card",
                    {"mode": "create", "kind": "action", "title": "Second", "effort_points": 5},
                ),
                (
                    "card",
                    {"mode": "create", "kind": "action", "title": "Third", "effort_points": 8},
                ),
            ),
        ]
    )
    first = await advisor.handle("Create three actions")
    assert first.proposal_id is not None
    screen = _QueueTestMessage()
    services = SimpleNamespace(
        sessions=e2e_harness.sessions,
        advisor=advisor,
        history=_QueueTestHistory(),
        owner_id=42,
        guard=GenerationGuard(),
    )
    await render_proposal(screen, services, first.proposal_id)
    await _resolve_queued_proposal(
        e2e_harness, services, screen, first.proposal_id, "proposal_approve"
    )

    follow_up = _QueueTestMessage()
    follow_up.message_id = 901
    follow_up.bot = screen.bot
    await dismiss_prior_ui(follow_up, services)

    frozen = screen.bot.edits[-1]
    # The frozen screen becomes assistant history, so it must not imply the whole
    # request was discarded when an earlier proposal in the queue was already saved.
    assert "Second" in frozen
    assert "Third" in frozen
    assert "✅ Saved" in frozen
    assert frozen.count("🗑 Discarded") == 2
    assert "First" in frozen
    assert "Walk straight" not in frozen
    assert len(provider.calls) == 1
    async with e2e_harness.sessions() as session:
        titles = set(await session.scalars(select(Card.title)))
    assert titles == {"First"}


async def test_proposal_ui_queues_mutations_and_reports_dependency_failure(e2e_harness):
    async with e2e_harness.sessions() as session:
        card = await create_manual_card(session, title="Release VrWalk", effort_points=3)
        await session.commit()

    advisor, provider = e2e_harness.advisor(
        [
            mutation_turn(
                ("tag", {"mode": "create", "name": "VrWalk"}),
                ("card", {"mode": "link", "id": card.id, "tag_query": "VrWalk"}),
            ),
            "Finished processing the proposals.",
        ]
    )
    first = await advisor.handle("Create VrWalk and link it to the card")
    assert first.proposal_id is not None
    message = _QueueTestMessage()
    services = SimpleNamespace(
        sessions=e2e_harness.sessions,
        advisor=advisor,
        history=_QueueTestHistory(),
        owner_id=42,
        guard=GenerationGuard(),
    )
    await render_proposal(message, services, first.proposal_id)
    assert "Create Tag" in message.rendered[-1]
    assert "Proposal 1/2" not in message.rendered[-1]
    assert "Link Card" not in message.rendered[-1]

    async with e2e_harness.sessions() as session:
        reject_tokens = list(
            await session.scalars(
                select(CallbackToken).where(
                    CallbackToken.action == "proposal_reject",
                    CallbackToken.consumed_at.is_(None),
                )
            )
        )
        reject = next(token for token in reject_tokens if token.payload["id"] == first.proposal_id)
    await callback_token_handler(_QueueTestCallback(reject.token, message), services)

    final_text = message.rendered[-1]
    assert "🗑 Discarded — New Tag “VrWalk”" in final_text
    assert "⚠️ Failed — Link Tag “VrWalk”" not in final_text
    assert "was not found" not in final_text
    assert "Finished processing the proposals." in final_text
    assert len(provider.calls) == 2
    async with e2e_harness.sessions() as session:
        proposal = await session.get(ChangeProposal, first.proposal_id)
        tag = await session.scalar(select(Tag).where(Tag.name == "VrWalk"))
        link = await session.scalar(select(CardTag).where(CardTag.card_id == card.id))
    assert proposal.status == "rejected"
    assert tag is None
    assert link is None


async def test_tag_proposal_save_restores_an_archived_tag(e2e_harness):
    async with e2e_harness.sessions() as session:
        archived_tag = Tag(name="VrWalk", description="Old description", archived_at=utcnow())
        session.add(archived_tag)
        await session.commit()
        archived_tag_id = archived_tag.id
        revision_before = (await session.get(Workspace, 1)).revision

    advisor, provider = e2e_harness.advisor(
        [
            mutation_turn(
                (
                    "tag",
                    {"mode": "create", "name": "VrWalk", "description": "VR project"},
                )
            ),
            "The VrWalk tag was restored.",
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
            select(CallbackToken).where(CallbackToken.action == "proposal_approve")
        )
        assert token is not None

    await callback_token_handler(_QueueTestCallback(token.token, message), services)

    async with e2e_harness.sessions() as session:
        proposal = await session.get(ChangeProposal, outcome.proposal_id)
        tags = list(await session.scalars(select(Tag).where(Tag.name == "VrWalk")))
        revision_after = (await session.get(Workspace, 1)).revision
    assert proposal.status == "approved"
    assert len(tags) == 1
    assert tags[0].id == archived_tag_id
    assert tags[0].archived_at is None
    assert tags[0].description == "VR project"
    assert revision_after == revision_before + 1
    assert "✅ Saved — New Tag “VrWalk”" in message.rendered[-1]
    assert "The VrWalk tag was restored." in message.rendered[-1]
    assert len(provider.calls) == 2


async def test_single_proposal_save_error_is_reported_and_resolved(e2e_harness):
    async with e2e_harness.sessions() as session:
        session.add(Tag(name="VrWalk"))
        await session.commit()

    advisor, provider = e2e_harness.advisor(
        [mutation_turn(("tag", {"mode": "create", "name": "vrwalk"}))]
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
        old_token = await session.scalar(
            select(CallbackToken).where(CallbackToken.action == "proposal_approve")
        )
        assert old_token is not None

    await callback_token_handler(_QueueTestCallback(old_token.token, message), services)

    async with e2e_harness.sessions() as session:
        proposal = await session.get(ChangeProposal, outcome.proposal_id)
        retry_token = await session.scalar(
            select(CallbackToken).where(
                CallbackToken.action == "proposal_approve",
                CallbackToken.consumed_at.is_(None),
            )
        )
        tags = list(await session.scalars(select(Tag)))
    assert proposal.status == "failed"
    assert retry_token is None
    assert len(tags) == 1
    assert "⚠️ Failed — New Tag “vrwalk”" in message.rendered[-1]
    assert "already exists" in message.rendered[-1]
    assert "could not generate its follow-up" in message.rendered[-1]
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


async def test_a_new_card_receipt_names_every_field_that_was_chosen(e2e_harness):
    """The receipt is what the owner checks the proposal by, so a default says nothing."""
    async with e2e_harness.sessions() as session:
        await create_tag(session, name="спорт")
        await session.commit()

    advisor, _ = e2e_harness.advisor(
        [
            mutation_turn(
                (
                    "card",
                    {
                        "mode": "create",
                        "kind": "action",
                        "title": "Тренировка бега",
                        "effort_points": 5,
                        "priority": "critical",
                        "categories": ["self"],
                        "energy_types": ["physical"],
                        "hard_time": True,
                        "tag_query": "спорт",
                    },
                )
            )
        ]
    )
    outcome = await advisor.handle("Заведи тренировку")

    async with e2e_harness.sessions() as session:
        description = await advisor.describe_proposal(session, outcome.proposal_id)
    assert description.summary == (
        "New Action “Тренировка бега” "
        "(Critical · 5 EP · self · physical · Hard time · Tag “спорт”)"
    )


async def test_a_backlog_card_receipt_says_nothing_about_its_stage(e2e_harness):
    advisor, _ = e2e_harness.advisor(
        [mutation_turn(("card", {"mode": "create", "kind": "goal", "title": "Быть здоровым"}))]
    )
    outcome = await advisor.handle("Заведи цель")

    async with e2e_harness.sessions() as session:
        description = await advisor.describe_proposal(session, outcome.proposal_id)
    assert description.summary == "New Goal “Быть здоровым”"


async def test_application_owned_saved_receipt_is_rendered_once_when_model_echoes_it(
    e2e_harness,
) -> None:
    title = "Вес 65 кг к концу 2026 года"
    receipt = f"✅ Saved — New Goal “{title}”"
    advisor, provider = e2e_harness.advisor(
        [
            mutation_turn(("card", {"mode": "create", "kind": "goal", "title": title})),
            f"{receipt}\n\nГотово! Твоя вторая цель добавлена.",
        ]
    )

    first = await advisor.handle(f"Создай цель {title}")
    assert first.proposal_id is not None
    async with e2e_harness.sessions() as session:
        affected_ids = await ProposalService(session).apply(first.proposal_id)
        await session.commit()

    final = await advisor.resolve_approval(
        "proposal",
        first.proposal_id,
        decision="approved",
        result={"affected_ids": affected_ids},
    )

    assert final is not None
    assert final.message.startswith(receipt)
    assert final.message.count(receipt) == 1
    assert "Готово! Твоя вторая цель добавлена." in final.message
    assert len(provider.calls) == 2


async def test_resumed_request_replays_its_own_intermediate_steps(e2e_harness):
    """A multi-step request must keep every step it already took across each approval."""
    advisor, provider = e2e_harness.advisor(
        [
            mutation_turn(("card", {"mode": "create", "kind": "goal", "title": "Быть здоровым"})),
            ProviderTurn(
                content="",
                tool_calls=(
                    ProviderToolCall(
                        id="broken-read",
                        name="query_safwa",
                        arguments=json.dumps({"sql": "DELETE FROM cards"}),
                    ),
                ),
            ),
            mutation_turn(
                (
                    "card",
                    {
                        "mode": "create",
                        "kind": "action",
                        "title": "Подтянуться 20 раз",
                        "effort_points": 1,
                    },
                ),
                prefix="second",
            ),
            "Цель и задача готовы.",
        ]
    )

    first = await advisor.handle(
        "Сделай цель Быть здоровым и задачу подтянуться",
        dialogue=[
            DialogueMessage(
                role="user",
                content=(
                    "[Initial request]: Начнём\n"
                    "[User]: Сделай цель Быть здоровым и задачу подтянуться"
                ),
            )
        ],
    )
    assert first.proposal_id is not None
    async with e2e_harness.sessions() as session:
        goal_ids = await ProposalService(session).apply(first.proposal_id)
        await session.commit()

    # No dialogue argument: the suspended turn resumes from what it persisted itself.
    second = await advisor.resolve_approval(
        "proposal", first.proposal_id, decision="approved", result={"affected_ids": goal_ids}
    )
    assert second is not None and second.proposal_id is not None
    async with e2e_harness.sessions() as session:
        action_ids = await ProposalService(session).apply(second.proposal_id)
        await session.commit()
    final = await advisor.resolve_approval(
        "proposal", second.proposal_id, decision="approved", result={"affected_ids": action_ids}
    )

    assert final is not None and "Цель и задача готовы." in final.message
    assert len(provider.calls) == 4
    last = provider.calls[3]
    # The board session's context: its prompt, the board state, the conversation, then
    # every step it already took for this request.
    assert [message["role"] for message in last] == [
        "system",
        "user",
        "user",
        "assistant",
        "tool",
        "assistant",
        "tool",
        "assistant",
        "tool",
    ]
    # The request that started the turn is still the user message the model reads.
    assert "Сделай цель Быть здоровым и задачу подтянуться" in str(last[2]["content"])
    # Step 1: the saved Goal, described rather than reduced to an ID list.
    assert '"status": "approved"' in str(last[4]["content"])
    assert "Create Card “Быть здоровым”" in str(last[4]["content"])
    assert f'"affected_ids": {json.dumps(goal_ids)}' in str(last[4]["content"])
    assert "Do not propose it again" in str(last[4]["content"])
    # Step 2: the failed read is still visible, with a bounded instruction.
    assert last[5]["tool_calls"][0]["function"]["name"] == "query_safwa"
    assert '"code": "unsafe_query"' in str(last[6]["content"])
    assert "do not restart the request" in str(last[6]["content"])
    # Step 3: the steps speak for themselves, so no progress digest is restated on top.
    assert all("[Current request progress" not in str(message.get("content")) for message in last)
    assert "Create Card “Подтянуться 20 раз”" in str(last[8]["content"])


async def test_suspended_batch_persists_the_request_dialogue_and_transcript(e2e_harness):
    advisor, _provider = e2e_harness.advisor(
        [mutation_turn(("tag", {"mode": "create", "name": "VrWalk"}))]
    )
    outcome = await advisor.handle(
        "Create a VrWalk tag",
        dialogue=[DialogueMessage(role="user", content="[User]: Create a VrWalk tag")],
    )
    assert outcome.proposal_id is not None

    async with e2e_harness.sessions() as session:
        batch = await session.scalar(
            select(AgentStep)
            .where(AgentStep.kind == "approval_batch")
            .order_by(AgentStep.id.desc())
        )
        run = await session.get(AgentRun, batch.run_id)
        state = run.state_json
    # The batch holds the screens; the session holds what it needs to continue.
    assert batch.metadata_json["status"] == "pending"
    assert state["dialogue"] == [{"role": "user", "content": "[User]: Create a VrWalk tag"}]
    assert [message["role"] for message in state["transcript"]] == ["assistant", "tool"]
    assert state["transcript"][0]["tool_calls"][0]["function"]["name"] == "tag"


async def test_the_tool_call_budget_is_carried_across_an_approval(e2e_harness):
    advisor, _provider = e2e_harness.advisor(
        [
            mutation_turn(("tag", {"mode": "create", "name": "Budget"})),
            mutation_turn(("tag", {"mode": "create", "name": "Overrun"})),
        ]
    )
    outcome = await advisor.handle("Create a Budget tag")
    assert outcome.proposal_id is not None

    # The session has spent its whole budget; the approval must not hand it a fresh one.
    async with e2e_harness.sessions() as session:
        run = await session.scalar(select(AgentRun).order_by(AgentRun.id.desc()))
        run.state_json = {**run.state_json, "tool_count": MAX_TOOL_CALLS}
        await session.commit()

    resumed = await advisor.resolve_approval(
        "proposal",
        outcome.proposal_id,
        decision="discarded",
        result={},
    )

    assert resumed is not None
    assert "could not generate its follow-up" in resumed.message
    async with e2e_harness.sessions() as session:
        run = await session.scalar(select(AgentRun).order_by(AgentRun.id.desc()))
        assert run.status == "failed"
        # The claim is released whichever way the turn ends, or the session is stuck.
        assert run.claimed_at is None


async def test_a_session_can_only_be_claimed_once(e2e_harness):
    advisor, _provider = e2e_harness.advisor(
        [mutation_turn(("tag", {"mode": "create", "name": "Claimed"}))]
    )
    assert (await advisor.handle("Create a Claimed tag")).proposal_id is not None

    async with e2e_harness.sessions() as session:
        run = await session.scalar(select(AgentRun).order_by(AgentRun.id.desc()))
        assert await advisor._claim_session(session, run.id, held_run_id=None) is not None
        assert await advisor._claim_session(session, run.id, held_run_id=None) is None
        # The turn that already holds the session continues inside its own claim.
        assert await advisor._claim_session(session, run.id, held_run_id=run.id) is not None


async def _standalone_tag_proposal(e2e_harness, advisor, name: str) -> int:
    """A proposal with no live approval batch, which is the plain receipt path."""
    outcome = await advisor.handle(f"Create a {name} tag")
    assert outcome.proposal_id is not None
    async with e2e_harness.sessions() as session:
        await session.execute(delete(AgentStep).where(AgentStep.kind == "approval_batch"))
        await session.commit()
    return outcome.proposal_id


@pytest.mark.parametrize(
    ("action", "expected_status", "heading"),
    [
        ("proposal_approve", "approved", "✅ Saved"),
        ("proposal_reject", "rejected", "🗑 Discarded"),
    ],
)
async def test_a_resolved_proposal_leaves_one_readable_line_in_the_dialogue(
    e2e_harness, action, expected_status, heading
):
    advisor, _provider = e2e_harness.advisor(
        [mutation_turn(("tag", {"mode": "create", "name": "VrWalk"}))]
    )
    proposal_id = await _standalone_tag_proposal(e2e_harness, advisor, "VrWalk")
    message = _QueueTestMessage()
    services = SimpleNamespace(
        sessions=e2e_harness.sessions,
        advisor=advisor,
        history=_QueueTestHistory(),
        owner_id=42,
        guard=GenerationGuard(),
    )
    await render_proposal(message, services, proposal_id)
    async with e2e_harness.sessions() as session:
        token = await session.scalar(
            select(CallbackToken).where(CallbackToken.action == action)
        )

    await callback_token_handler(_QueueTestCallback(token.token, message), services)

    async with e2e_harness.sessions() as session:
        proposal = await session.get(ChangeProposal, proposal_id)
    assert proposal.status == expected_status
    receipt = message.rendered[-1]
    assert heading in receipt
    # The owner-facing line, not "Updated 1 item(s)".
    assert "New Tag “VrWalk”" in receipt
    assert "Name: VrWalk" in receipt


async def test_navigating_away_freezes_the_proposal_into_the_same_outcome_text(e2e_harness):
    advisor, _provider = e2e_harness.advisor(
        [mutation_turn(("tag", {"mode": "create", "name": "VrWalk"}))]
    )
    proposal_id = await _standalone_tag_proposal(e2e_harness, advisor, "VrWalk")
    message = _QueueTestMessage()
    services = SimpleNamespace(
        sessions=e2e_harness.sessions,
        advisor=advisor,
        history=_QueueTestHistory(),
        owner_id=42,
        guard=GenerationGuard(),
    )
    await render_proposal(message, services, proposal_id)
    # A screen *below* the proposal: the old "older than this message" selector missed it.
    dashboard = SimpleNamespace(message_id=1, chat=message.chat, bot=message.bot)

    await dismiss_prior_ui(dashboard, services)

    async with e2e_harness.sessions() as session:
        proposal = await session.get(ChangeProposal, proposal_id)
        tag = await session.scalar(select(Tag).where(Tag.name == "VrWalk"))
    assert proposal.status == "rejected"
    assert tag is None
    frozen = message.bot.edits[-1]
    assert "🗑 Discarded" in frozen
    assert "You continued the conversation without saving it." in frozen
    assert "New Tag “VrWalk”" in frozen
