from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from aiogram.types import InlineKeyboardMarkup
from sqlalchemy import func, select
from ui_harness import spawn_timer

from llm_gateway import CompletionTurn as ProviderTurn
from llm_gateway import ToolCall as ProviderToolCall
from safwa.bootstrap.modules import (
    ALLOWED_VIEWS,
    FEATURE_CALLBACK_ACTIONS,
    FEATURE_TEXT_INPUTS,
    PROPOSALS,
    SCREENS,
    SYSTEM_PROMPT,
)
from safwa.bootstrap.recovery import recover_startup
from safwa.features.cards.model import Card, CardCategory, CardEnergyType, CardStage
from safwa.features.cards.use_cases import create_card, finish_action, move_card
from safwa.features.planning.use_cases import finish_sprint, sprint_metrics, start_sprint
from safwa.features.saved_requests.api import request_cards
from safwa.features.saved_requests.model import SavedRequest
from safwa.features.saved_requests.use_cases import create_saved_request
from safwa.features.tags.model import CardTag, Tag
from safwa.features.tags.use_cases import create_tag
from safwa.features.values.model import CardValue, Value
from safwa.foundation.marks import title_marks
from safwa.foundation.workspace import Workspace
from telegram_llm import ChatHost, DialogueMessage
from tg_agent_shell.ai.outcome import AIOutcome, AIOutcomeKind
from tg_agent_shell.ai.runs import AgentRun, AgentStep
from tg_agent_shell.foundation.errors import StaleStateError
from tg_agent_shell.foundation.kinds import MARKS
from tg_agent_shell.history import TelegramNotes
from tg_agent_shell.proposals.model import (
    BatchDecision,
    ChangeAction,
    ProposalChange,
)
from tg_agent_shell.proposals.telegram import render_ai_outcome, render_proposal
from tg_agent_shell.proposals.use_cases import approve_proposal
from tg_agent_shell.session import MAX_TOOL_CALLS
from tg_agent_shell.telegram import callback_token_handler, dismiss_prior_ui
from tg_agent_shell.telegram.model import CallbackToken
from tg_agent_shell.turn import TurnManager

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
                id=f"{prefix}-{index}", name=name, arguments_json=json.dumps(arguments)
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

    assert outcome.kind is AIOutcomeKind.PROPOSAL
    change = e2e_harness.reviews.proposal(outcome.proposal_id).changes[0]
    assert change.values["title"] == "Подтянуться 20 раз"
    assert "parent_id" not in change.values
    assert "parent_query" not in change.values


async def test_invalid_create_returns_minimal_repair_arguments_to_the_model(e2e_harness):
    """PR-REPAIR-015 — tests/brd/proposals.feature"""
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

    assert outcome.kind is AIOutcomeKind.PROPOSAL
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
        change = e2e_harness.reviews.proposal(outcome.proposal_id).changes[0]
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
        assert len(advisor.reviews.open_proposals) == 0
    tool_messages = [
        message
        for call in _provider.calls
        for message in call
        if message.get("role") == "tool"
    ]
    assert any("root-level" in str(message["content"]) for message in tool_messages)


async def test_ai_stage_update_to_done_keeps_completion_accounting(e2e_harness):
    """PR-SAVE-009 — tests/brd/proposals.feature"""
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
        change = e2e_harness.reviews.proposal(outcome.proposal_id).changes[0]
        # An approved stage change routes terminal stages through finish_action, so the
        # completion timestamp and Sprint result are never skipped.
        change.values = {**change.values, "stage": CardStage.DONE.value}
        await session.commit()

    async with e2e_harness.sessions() as session:
        await approve_proposal(session, advisor.reviews, PROPOSALS, outcome.proposal_id)
        await session.commit()

    async with e2e_harness.sessions() as session:
        stored = await session.get(Card, action_id)
        assert stored.effective_stage == CardStage.DONE.value
        assert stored.completed_at is not None
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

    assert outcome.kind is AIOutcomeKind.ANSWER
    assert len(provider.calls) == 2
    tool_result = json.loads(str(provider.calls[1][-1]["content"]))
    assert tool_result["code"] == "unsafe_query"
    assert "read-only SELECT over ai_cards" in tool_result["hint"]


async def test_ai_card_proposal_reaches_the_sprint_it_was_planned_into(e2e_harness):
    """PL-SCOPE-007 — tests/brd/planning.feature"""
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

    assert outcome.kind is AIOutcomeKind.PROPOSAL
    assert outcome.proposal_id is not None
    assert len(provider.calls) == 1
    assert not provider.responses

    async with e2e_harness.sessions() as session:
        assert await session.scalar(select(func.count(Card.id))) == 1
        affected = await approve_proposal(session, advisor.reviews, PROPOSALS, outcome.proposal_id)
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
        sprint = await start_sprint(session, success_criteria="Ship the release")
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

        assert await sprint_metrics(session, sprint.id) == {
            "committed": 2,
            "added": 2,
            "removed": 0,
            "completed": 2,
            "cancelled": 0,
        }

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
        await start_sprint(session, success_criteria="Ship the release")
        await finish_action(session, action.id, CardStage.DONE)
        await session.commit()

    query_response = ProviderTurn(
        content="",
        tool_calls=(
            ProviderToolCall(
                id="read-1",
                name="query_data",
                arguments_json=json.dumps(
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

    assert outcome.kind is AIOutcomeKind.ANSWER
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
    """AG-ANSWER-013 — tests/brd/agents.feature"""
    query_response = ProviderTurn(
        content="",
        tool_calls=(
            ProviderToolCall(
                id="read-1",
                name="query_data",
                arguments_json=json.dumps({"sql": "SELECT id FROM ai_cards"}),
            ),
        ),
    )
    advisor, provider = e2e_harness.advisor(
        [query_response, "", "Isha is the night prayer."]
    )
    outcome = await advisor.handle("What is isha?")

    assert outcome.kind is AIOutcomeKind.ANSWER
    assert outcome.message == "Isha is the night prayer."
    assert len(provider.calls) == 3
    nudge = str(provider.calls[2][-1]["content"])
    assert nudge.startswith("[System]: You stopped without answering.")
    assert provider.calls[2][-2]["role"] == "tool"


async def test_a_turn_that_never_finds_words_still_reaches_the_owner(e2e_harness):
    """AG-ANSWER-013 — tests/brd/agents.feature"""
    advisor, provider = e2e_harness.advisor([""] * 6)
    outcome = await advisor.handle("What is isha?")

    assert outcome.kind is AIOutcomeKind.ANSWER
    assert outcome.message == "⚠️ Safwa had nothing to say about that. You can ask again."
    assert len(provider.calls) == 6


async def test_read_and_mutation_in_one_turn_rejects_only_the_mutation(e2e_harness):
    """PR-WRITE-002 — tests/brd/proposals.feature"""
    mixed = ProviderTurn(
        content="",
        tool_calls=(
            ProviderToolCall(
                id="mixed-read",
                name="query_data",
                arguments_json=json.dumps({"sql": "SELECT id FROM ai_cards LIMIT 1"}),
            ),
            ProviderToolCall(
                id="mixed-write",
                name="card",
                arguments_json=json.dumps(
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
    assert isinstance(tool_results["query_data"], list)
    assert tool_results["card"]["code"] == "mixed_read_and_mutation_tools"
    assert tool_results["card"]["retryable"] is True


async def test_multiple_ai_card_creations_are_reviewed_sequentially(e2e_harness):
    """PR-QUEUE-006 — tests/brd/proposals.feature"""
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

    batch = next(iter(advisor.reviews.open_batches), None)
    assert batch is not None
    tool_results = batch.tool_calls
    assert tool_results[0]["result"] is None
    assert [item["result"]["code"] for item in tool_results[1:]] == [
        "reference_not_found",
        "reference_not_found",
    ]

    for _index in range(3):
        assert current.proposal_id is not None
        proposal_ids.append(current.proposal_id)
        async with e2e_harness.sessions() as session:
            affected = await approve_proposal(session, advisor.reviews, PROPOSALS, current.proposal_id)
            await session.commit()
        current = await advisor.resolve_approval(
            proposal_ids[-1],
            decision=BatchDecision.APPROVED,
            result={"affected_ids": affected},
        )
        assert current is not None
        if len(proposal_ids) == 1:
            assert "⚠️ Failed" not in current.message

    assert len(set(proposal_ids)) == 3
    assert current.kind is AIOutcomeKind.ANSWER
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
        affected = await approve_proposal(session, advisor.reviews, PROPOSALS, proposal.proposal_id)
        await session.commit()

    outcome = await advisor.resolve_approval(
        proposal.proposal_id,
        decision=BatchDecision.APPROVED,
        result={"affected_ids": affected},
    )

    assert outcome is not None and outcome.kind is AIOutcomeKind.ANSWER
    saved = json.loads(
        str(next(message for message in provider.calls[1] if message["role"] == "tool")["content"])
    )
    assert saved["status"] == "approved"
    assert saved["affected_ids"] == [card.id]
    assert saved["summary"] == f"Update Card #{card.id}"
    assert "Note: Before dinner → After dinner" in saved["fields"]
    assert "Categories: self → rest" in saved["fields"]


async def test_child_proposal_fails_cleanly_when_earlier_parent_is_discarded(e2e_harness):
    """PR-FAIL-014 — tests/brd/proposals.feature"""
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
        advisor.reviews.end_proposal(first.proposal_id)
        await session.commit()
    second = await advisor.resolve_approval(
        first.proposal_id,
        decision=BatchDecision.DISCARDED,
        result={},
    )

    assert second is not None and second.kind is AIOutcomeKind.ANSWER
    assert "did not retry" in second.message
    assert len(provider.calls) == 2
    continuation_results = [
        json.loads(str(message["content"]))
        for message in provider.calls[1]
        if message["role"] == "tool"
    ]
    assert continuation_results[0]["status"] == "discarded"
    assert continuation_results[1]["code"] == "reference_not_found"
    async with e2e_harness.sessions() as session:
        assert await session.scalar(select(func.count(Card.id))) == 0
        assert len(advisor.reviews.open_proposals) == 0


async def test_new_tag_and_dependent_card_link_use_one_repair_round(e2e_harness):
    """PR-REPAIR-015 — tests/brd/proposals.feature"""
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
        tag_ids = await approve_proposal(session, advisor.reviews, PROPOSALS, tag_proposal.proposal_id)
        await session.commit()

    card_proposal = await advisor.resolve_approval(
        tag_proposal.proposal_id,
        decision=BatchDecision.APPROVED,
        result={"affected_ids": tag_ids},
    )
    assert card_proposal is not None and card_proposal.proposal_id is not None
    async with e2e_harness.sessions() as session:
        affected = await approve_proposal(session, advisor.reviews, PROPOSALS, card_proposal.proposal_id)
        await session.commit()

    outcome = await advisor.resolve_approval(
        card_proposal.proposal_id,
        decision=BatchDecision.APPROVED,
        result={"affected_ids": affected},
    )

    assert outcome is not None and outcome.kind is AIOutcomeKind.ANSWER
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
    """PR-REPAIR-016 — tests/brd/proposals.feature"""
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

    assert outcome.kind is AIOutcomeKind.ANSWER
    assert "five repair attempts" in outcome.message
    assert len(provider.calls) == 6
    async with e2e_harness.sessions() as session:
        assert len(advisor.reviews.open_proposals) == 0
        assert await session.scalar(select(func.count(Card.id))) == 0


async def test_ai_creates_an_approved_saved_tag_request(e2e_harness):
    """SR-AI-007 — tests/brd/saved_requests.feature"""
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
        affected = await approve_proposal(session, advisor.reviews, PROPOSALS, outcome.proposal_id)
        await session.commit()
        request = await session.get(SavedRequest, affected[0])
        assert request is not None
        matches = await request_cards(session, request.query_sql, ALLOWED_VIEWS)
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
    """PR-SAVE-009 — tests/brd/proposals.feature"""
    response = mutation_turn(
        ("tag", {"mode": "create", "name": "Learning", "description": "Study and practice."})
    )
    advisor, _provider = e2e_harness.advisor([response])
    outcome = await advisor.handle("Create a Learning tag")

    async with e2e_harness.sessions() as session:
        affected = await approve_proposal(session, advisor.reviews, PROPOSALS, outcome.proposal_id or 0)
        await session.commit()
        tag = await session.get(Tag, affected[0])
        assert tag is not None
        assert (tag.name, tag.description) == ("Learning", "Study and practice.")


async def test_ai_create_tag_and_links_are_reviewed_as_separate_proposals(e2e_harness):
    """PR-QUEUE-005 — tests/brd/proposals.feature"""
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
                name="query_data",
                arguments_json=json.dumps(
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
            changes = list(e2e_harness.reviews.proposal(current.proposal_id).changes)
            assert len(changes) == 1
            affected = await approve_proposal(session, advisor.reviews, PROPOSALS, current.proposal_id)
            await session.commit()
        current = await advisor.resolve_approval(
            proposal_ids[-1],
            decision=BatchDecision.APPROVED,
            result={"affected_ids": affected},
        )
        assert current is not None

    assert len(set(proposal_ids)) == 3
    assert current.kind is AIOutcomeKind.ANSWER
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
    """PR-QUEUE-005 — tests/brd/proposals.feature"""
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
        first_ids = await approve_proposal(session, advisor.reviews, PROPOSALS, first.proposal_id)
        await session.commit()
    second = await advisor.resolve_approval(
        first.proposal_id,
        decision=BatchDecision.APPROVED,
        result={"affected_ids": first_ids},
    )
    assert second is not None and second.proposal_id is not None
    assert second.proposal_id != first.proposal_id
    assert len(provider.calls) == 2

    async with e2e_harness.sessions() as session:
        second_ids = await approve_proposal(session, advisor.reviews, PROPOSALS, second.proposal_id)
        await session.commit()
    final = await advisor.resolve_approval(
        second.proposal_id,
        decision=BatchDecision.APPROVED,
        result={"affected_ids": second_ids},
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
    """PR-STALE-012 — tests/brd/proposals.feature"""
    async with e2e_harness.sessions() as session:
        request = await create_saved_request(
            session,
            "All goals",
            "SELECT id FROM ai_cards WHERE kind = 'goal'",
            views=ALLOWED_VIEWS,
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
            await approve_proposal(session, advisor.reviews, PROPOSALS, outcome.proposal_id or "")
        except StaleStateError:
            pass
        else:
            raise AssertionError("Request proposal must reject a stale version")


async def test_ai_request_with_unsafe_sql_never_becomes_a_proposal(e2e_harness):
    """SR-AI-007 — tests/brd/saved_requests.feature"""
    response = mutation_turn(
        (
            "request",
            {
                "mode": "create",
                "name": "Everything",
                "sql": "SELECT id FROM cards",
            },
        )
    )
    advisor, provider = e2e_harness.advisor(
        [response, "That query is not allowed, so I proposed nothing."]
    )

    outcome = await advisor.handle("Save a Request over every card")

    assert outcome.kind is AIOutcomeKind.ANSWER
    tool_result = json.loads(str(provider.calls[1][-1]["content"]))
    assert tool_result["code"] == "unsafe_query"
    assert "read-only SELECT over ai_cards" in tool_result["hint"]
    async with e2e_harness.sessions() as session:
        assert list(await session.scalars(select(SavedRequest))) == []
        assert list(advisor.reviews.open_proposals) == []


async def test_ai_can_query_saved_requests_through_the_safe_view(e2e_harness):
    """SR-READ-011 — tests/brd/saved_requests.feature"""
    async with e2e_harness.sessions() as session:
        await create_saved_request(
            session,
            "All goals",
            "SELECT id FROM ai_cards WHERE kind = 'goal'",
            views=ALLOWED_VIEWS,
        )
        await session.commit()

    responses = [
        ProviderTurn(
            content="",
            tool_calls=(
                ProviderToolCall(
                    id="read-requests",
                    name="query_data",
                    arguments_json=json.dumps({"sql": "SELECT name FROM ai_requests"}),
                ),
            ),
        ),
        "You have a saved Request named All goals.",
    ]
    advisor, provider = e2e_harness.advisor(responses)
    outcome = await advisor.handle("What saved Requests do I have?")

    assert outcome.kind is AIOutcomeKind.ANSWER
    assert len(provider.calls) == 2
    follow_up_context = str(provider.calls[1][-1]["content"])
    assert '"name": "All goals"' in follow_up_context


async def test_ai_request_query_values_and_marks_archived_cards(e2e_harness):
    """SR-RUN-006 — tests/brd/saved_requests.feature"""
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
        affected = await approve_proposal(session, advisor.reviews, PROPOSALS, outcome.proposal_id or "")
        await session.commit()
        request = await session.get(SavedRequest, affected[0])
        assert request is not None
        matches = await request_cards(session, request.query_sql, ALLOWED_VIEWS)
        # An archived Card is in the answer, and the marker on its title is what says so.
        assert [card.id for card in matches] == [live.id, archived.id]
        assert await title_marks(session, matches[1]) == " [📦]"


async def test_ai_request_query_supports_complex_boolean_logic(e2e_harness):
    """SR-RUN-006 — tests/brd/saved_requests.feature"""
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
        affected = await approve_proposal(session, advisor.reviews, PROPOSALS, outcome.proposal_id or "")
        await session.commit()
        request = await session.get(SavedRequest, affected[0])
        assert request is not None
        matches = await request_cards(session, request.query_sql, ALLOWED_VIEWS)
        assert {card.id for card in matches} == {today.id, critical.id}
        assert ordinary.id not in {card.id for card in matches}


async def test_cacheable_prefix_is_byte_stable_across_turns(e2e_harness):
    dialogue = [DialogueMessage(role="user", content="[User]: What is next?")]
    advisor, provider = e2e_harness.advisor(["First.", "Second."])

    await advisor.handle("What is next?", dialogue=dialogue)
    await advisor.handle("What is next?", dialogue=dialogue)

    first, second = provider.calls
    # Only the trailing clock inside the final owner turn may differ.
    assert first[:-1] == second[:-1]
    assert "[System]: Current local time:" in str(first[-1]["content"])


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
    assert marked == [0, len(messages) - 2]
    assert messages[0]["content"] == [
        {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
    ]
    assert isinstance(messages[-1]["content"], str)


async def test_cache_breakpoints_are_absent_by_default(e2e_harness):
    advisor, provider = e2e_harness.advisor(["Noted."])

    await advisor.handle("Hi", dialogue=[DialogueMessage(role="user", content="[User]: Hi")])

    assert all(isinstance(message["content"], str) for message in provider.calls[0])


async def test_advisor_combines_context_when_history_is_absent(e2e_harness):
    advisor, provider = e2e_harness.advisor(["Noted."])

    await advisor.handle("Hi")

    messages = provider.calls[0]
    assert [message["role"] for message in messages] == ["system", "user"]
    assert "Persistent memory:" in messages[-1]["content"]
    assert "Hi" in messages[-1]["content"]
    assert "[System]: Current local time:" in messages[-1]["content"]


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

    assert outcome.kind is AIOutcomeKind.ANSWER
    messages = provider.calls[0]
    # Only messages[0] may be a system message; later blocks travel as owner text.
    assert [message["role"] for message in messages] == [
        "system",
        "user",
        "assistant",
        "user",
    ]
    assert messages[-1]["content"].startswith(dialogue[-1].content)
    assert "[System]: Current local time:" in messages[-1]["content"]
    timestamp = messages[-1]["content"].split("Current local time: ", 1)[1]
    assert "T" not in timestamp and timestamp.count(":") == 1
    system = str(messages[0]["content"])
    assert system == SYSTEM_PROMPT
    assert "query_data" in system
    assert "ai_cards(id, title" in system
    assert "Current local time" not in system
    assert "Current local time" not in str(messages[1]["content"])
    assert "Workspace revision:" not in system
    assert "Saved Requests:" not in system
    assert "Sprint cards:" not in system
    assert "Recent cards" not in system
    assert "Lexical card candidates" not in system
    tools = provider.options[0]["tools"]
    # The Advisor reads, shows and routes; every mutation tool belongs to the subagent that owns it.
    assert isinstance(tools, list) and [tool["function"]["name"] for tool in tools] == [
        "query_data",
        "open",
        "route",
    ]


async def test_mixed_query_and_mutation_resumes_only_after_approval(e2e_harness):
    """PR-QUEUE-007 — tests/brd/proposals.feature"""
    async with e2e_harness.sessions() as session:
        card = await create_manual_card(session, title="Release VrWalk", effort_points=3)
        await session.commit()

    mixed_turn = ProviderTurn(
        content="",
        tool_calls=(
            ProviderToolCall(
                id="create-tag",
                name="tag",
                arguments_json=json.dumps({"mode": "create", "name": "VrWalk"}),
            ),
            ProviderToolCall(
                id="recent-cards",
                name="query_data",
                arguments_json=json.dumps(
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
        affected = await approve_proposal(session, advisor.reviews, PROPOSALS, outcome.proposal_id)
        await session.commit()

    resumed = await advisor.resolve_approval(
        outcome.proposal_id,
        decision=BatchDecision.APPROVED,
        result={"affected_ids": affected},
    )

    assert resumed is not None and resumed.kind is AIOutcomeKind.ANSWER
    assert "✅ Saved — New Tag “VrWalk”" in resumed.message
    assert "The tag was saved." in resumed.message
    assert len(provider.calls) == 3
    tool_messages = [message for message in provider.calls[1] if message["role"] == "tool"]
    assert [message["name"] for message in tool_messages] == ["tag", "query_data"]
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
        batch = e2e_harness.reviews.batch_for_run(run.id)
        assert run.status == "completed"
        assert batch is None


async def test_independent_mutations_are_reviewed_in_order_before_one_resume(e2e_harness):
    """PR-QUEUE-006 — tests/brd/proposals.feature"""
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
        first_ids = await approve_proposal(session, advisor.reviews, PROPOSALS, first.proposal_id)
        await session.commit()
    second = await advisor.resolve_approval(
        first.proposal_id,
        decision=BatchDecision.APPROVED,
        result={"affected_ids": first_ids},
    )

    assert second is not None and second.proposal_id is not None
    assert second.proposal_id != first.proposal_id
    assert len(provider.calls) == 1
    async with e2e_harness.sessions() as session:
        second_ids = await approve_proposal(session, advisor.reviews, PROPOSALS, second.proposal_id)
        await session.commit()
    final = await advisor.resolve_approval(
        second.proposal_id,
        decision=BatchDecision.APPROVED,
        result={"affected_ids": second_ids},
    )

    assert final is not None and "Both decisions are resolved." in final.message
    assert final.message.count("✅ Saved") == 2
    assert len(provider.calls) == 2
    tool_messages = [message for message in provider.calls[1] if message["role"] == "tool"]
    assert len(tool_messages) == 2
    assert all('"status": "approved"' in str(message["content"]) for message in tool_messages)


async def test_discarded_proposal_result_is_returned_with_later_approval(e2e_harness):
    """PR-SAVE-010 — tests/brd/proposals.feature"""
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
        advisor.reviews.end_proposal(first.proposal_id)
        await session.commit()
    second = await advisor.resolve_approval(
        first.proposal_id,
        decision=BatchDecision.DISCARDED,
        result={"message": "The user discarded this proposed change."},
    )
    assert second is not None and second.proposal_id is not None

    async with e2e_harness.sessions() as session:
        affected = await approve_proposal(session, advisor.reviews, PROPOSALS, second.proposal_id)
        await session.commit()
    final = await advisor.resolve_approval(
        second.proposal_id,
        decision=BatchDecision.APPROVED,
        result={"affected_ids": affected},
    )

    assert final is not None and "I kept only the Value." in final.message
    assert "🗑 Discarded — New Tag “Skip me”" in final.message
    assert "✅ Saved — New Value “Keep me”" in final.message
    tool_messages = [message for message in provider.calls[1] if message["role"] == "tool"]
    assert '"status": "discarded"' in str(tool_messages[0]["content"])
    assert '"status": "approved"' in str(tool_messages[1]["content"])


async def test_new_dialogue_cancels_every_unresolved_item_in_suspended_batch(e2e_harness):
    """PR-INTERRUPT-017 — tests/brd/proposals.feature"""
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

    cancelled = await advisor.cancel_approval_for_proposal(first.proposal_id)

    # The caller freezes the screen with this text, so it has to name every item.
    assert "🗑 Discarded — New Tag “VrWalk”" in cancelled
    assert "🗑 Discarded — New Value “Health”" in cancelled
    assert len(provider.calls) == 1
    async with e2e_harness.sessions() as session:
        proposals = list(advisor.reviews.open_proposals)
        run = await session.scalar(select(AgentRun).order_by(AgentRun.id.desc()))
        batch = e2e_harness.reviews.batch_for_run(run.id)
        assert proposals == []
        assert batch is None
        # The screen is frozen, but the session that wrote it stays resumable: the owner's
        # next words may well be a correction to exactly these two changes.  Unfinished
        # rather than waiting, because no screen is open on it any more.
        assert (run.kind, run.status) == ("workspace_mutator", "interrupted")


async def test_a_review_whose_screen_could_not_be_sent_does_not_stay_open(
    e2e_harness, monkeypatch
):
    """SC-FAIL-005 — tests/brd/screens.feature"""
    advisor, _provider = e2e_harness.advisor(
        [mutation_turn(("tag", {"mode": "create", "name": "VrWalk"}))]
    )
    outcome = await advisor.handle("Create a VrWalk tag")
    assert outcome.proposal_id is not None
    assert advisor.reviews.busy is True

    async def refuse(*_args, **_kwargs):
        raise RuntimeError("Telegram refused the screen")

    monkeypatch.setattr("tg_agent_shell.proposals.telegram.answer.render_proposal", refuse)
    message = _QueueTestMessage()
    services = SimpleNamespace(
        sessions=e2e_harness.sessions,
        root=advisor,
        history=_QueueTestHistory(),
        owner_id=42,
        turn=TurnManager(),
        screens=SCREENS,
        chat=ChatHost(TelegramNotes(e2e_harness.sessions), MARKS, spawn=spawn_timer),
        callback_actions=FEATURE_CALLBACK_ACTIONS,
        text_inputs=FEATURE_TEXT_INPUTS,
    )

    with pytest.raises(RuntimeError):
        await render_ai_outcome(message, services, outcome)

    # Nothing on screen and nothing waiting: this is exactly what `CueRuntime.can_speak`
    # reads, so a Reminder that comes due next is spoken instead of waiting for a restart.
    assert message.rendered == []
    assert advisor.reviews.busy is False
    async with e2e_harness.sessions() as session:
        claimed = await session.scalar(
            select(func.count(AgentRun.id)).where(AgentRun.claimed_at.is_not(None))
        )
    assert claimed == 0


async def test_query_then_link_continuation_can_suspend_for_a_second_queue(e2e_harness):
    """PR-QUEUE-007 — tests/brd/proposals.feature"""
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
                arguments_json=json.dumps({"mode": "create", "name": "VrWalk"}),
            ),
            ProviderToolCall(
                id="find-recent",
                name="query_data",
                arguments_json=json.dumps(
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
        created_tag_ids = await approve_proposal(session, advisor.reviews, PROPOSALS, first.proposal_id)
        await session.commit()
    first_link = await advisor.resolve_approval(
        first.proposal_id,
        decision=BatchDecision.APPROVED,
        result={"affected_ids": created_tag_ids},
    )
    assert first_link is not None and first_link.proposal_id is not None
    assert len(provider.calls) == 3

    async with e2e_harness.sessions() as session:
        first_link_ids = await approve_proposal(session, advisor.reviews, PROPOSALS, first_link.proposal_id)
        await session.commit()
    second_link = await advisor.resolve_approval(
        first_link.proposal_id,
        decision=BatchDecision.APPROVED,
        result={"affected_ids": first_link_ids},
    )
    assert second_link is not None and second_link.proposal_id is not None
    assert len(provider.calls) == 3

    async with e2e_harness.sessions() as session:
        second_link_ids = await approve_proposal(session, advisor.reviews, PROPOSALS, second_link.proposal_id)
        await session.commit()
    final = await advisor.resolve_approval(
        second_link.proposal_id,
        decision=BatchDecision.APPROVED,
        result={"affected_ids": second_link_ids},
    )
    assert final is not None and final.kind is AIOutcomeKind.ANSWER
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
        self.markups: list[InlineKeyboardMarkup | None] = []

    async def edit_text(self, text, *, reply_markup=None, parse_mode=None):
        del parse_mode
        self.rendered.append(text)
        self.markups.append(reply_markup)
        return self

    async def answer(self, text, *, reply_markup=None, parse_mode=None):
        del parse_mode
        self.rendered.append(text)
        self.markups.append(reply_markup)
        return self

    def buttons(self) -> list[str]:
        markup = self.markups[-1]
        return [button.text for row in markup.inline_keyboard for button in row]


class _QueueTestCallback:
    def __init__(self, token: str, message: _QueueTestMessage) -> None:
        self.data = f"cb:{token}"
        self.message = message
        self.answers: list[tuple[str | None, bool]] = []

    async def answer(self, text=None, *, show_alert=False) -> None:
        self.answers.append((text, show_alert))


class _QueueTestHistory:
    async def dialogue(self, _chat_id):
        raise AssertionError("approval resume must use its persisted dialogue")


@pytest.mark.parametrize(
    ("callback_action", "final_text"),
    [
        ("proposal_approve", "The VrWalk tag was saved."),
        ("proposal_reject", "The VrWalk tag was discarded."),
    ],
)
async def test_single_tag_proposal_save_and_discard_callbacks_resume_agent(
    e2e_harness,
    callback_action,
    final_text,
):
    """PR-SCREEN-003 — tests/brd/proposals.feature"""
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
        root=advisor,
        history=_QueueTestHistory(),
        owner_id=42,
        turn=TurnManager(),
        screens=SCREENS,
        chat=ChatHost(TelegramNotes(e2e_harness.sessions), MARKS, spawn=spawn_timer),
        callback_actions=FEATURE_CALLBACK_ACTIONS,
        text_inputs=FEATURE_TEXT_INPUTS,
    )
    await render_proposal(message, services, outcome.proposal_id)
    async with e2e_harness.sessions() as session:
        token = await session.scalar(
            select(CallbackToken).where(CallbackToken.action == callback_action)
        )
        assert token is not None

    await callback_token_handler(_QueueTestCallback(token.token, message), services)

    async with e2e_harness.sessions() as session:
        proposal = advisor.reviews.proposal(outcome.proposal_id)
        tag = await session.scalar(select(Tag).where(Tag.name == "VrWalk"))
    assert proposal is None
    assert (tag is not None) is (callback_action == "proposal_approve")
    expected_result = "✅ Saved" if callback_action == "proposal_approve" else "🗑 Discarded"
    assert f"{expected_result} — New Tag “VrWalk”" in message.rendered[-1]
    assert final_text in message.rendered[-1]
    assert message.bot.typing_calls == 1
    assert len(provider.calls) == 2


async def test_restart_invalidates_an_unanswered_proposal_button(e2e_harness):
    """SC-BUTTON-003 — tests/brd/screens.feature"""
    advisor, provider = e2e_harness.advisor(
        [mutation_turn(("tag", {"mode": "create", "name": "VrWalk"}))]
    )
    outcome = await advisor.handle("Create a VrWalk tag")
    assert outcome.proposal_id is not None
    message = _QueueTestMessage()
    services = SimpleNamespace(
        sessions=e2e_harness.sessions,
        root=advisor,
        history=_QueueTestHistory(),
        owner_id=42,
        turn=TurnManager(),
        screens=SCREENS,
        chat=ChatHost(TelegramNotes(e2e_harness.sessions), MARKS, spawn=spawn_timer),
        callback_actions=FEATURE_CALLBACK_ACTIONS,
        text_inputs=FEATURE_TEXT_INPUTS,
    )
    await render_proposal(message, services, outcome.proposal_id)
    async with e2e_harness.sessions() as session:
        token = await session.scalar(
            select(CallbackToken).where(CallbackToken.action == "proposal_approve")
        )
        assert token is not None
        token_value = token.token
        await recover_startup(session)
        await session.commit()
    # Restarting is what ends a review: nothing carries one across a process.
    e2e_harness.reviews.end_proposal(outcome.proposal_id)

    callback = _QueueTestCallback(token_value, message)
    await callback_token_handler(callback, services)

    async with e2e_harness.sessions() as session:
        tag = await session.scalar(select(Tag).where(Tag.name == "VrWalk"))
        token = await session.get(CallbackToken, token_value)
    assert advisor.reviews.proposal(outcome.proposal_id) is None
    assert tag is None
    # The button went with the run of Safwa that drew it, and the screen it was on is
    # replaced rather than left standing with controls that answer nothing.
    assert token is None
    assert "out of date" in message.rendered[-1]
    assert message.markups[-1] is None
    assert not [alert for _text, alert in callback.answers if alert]
    assert len(provider.calls) == 1


async def test_a_button_works_once(e2e_harness):
    """SC-BUTTON-003 — tests/brd/screens.feature"""
    advisor, _provider = e2e_harness.advisor(
        [
            mutation_turn(("tag", {"mode": "create", "name": "VrWalk"})),
            "The VrWalk tag was saved.",
        ]
    )
    outcome = await advisor.handle("Create a VrWalk tag")
    assert outcome.proposal_id is not None
    message = _QueueTestMessage()
    services = SimpleNamespace(
        sessions=e2e_harness.sessions,
        root=advisor,
        history=_QueueTestHistory(),
        owner_id=42,
        turn=TurnManager(),
        screens=SCREENS,
        chat=ChatHost(TelegramNotes(e2e_harness.sessions), MARKS, spawn=spawn_timer),
        callback_actions=FEATURE_CALLBACK_ACTIONS,
        text_inputs=FEATURE_TEXT_INPUTS,
    )
    await render_proposal(message, services, outcome.proposal_id)
    async with e2e_harness.sessions() as session:
        token_value = await session.scalar(
            select(CallbackToken.token).where(CallbackToken.action == "proposal_approve")
        )

    await callback_token_handler(_QueueTestCallback(token_value, message), services)
    second = _QueueTestCallback(token_value, message)
    await callback_token_handler(second, services)

    async with e2e_harness.sessions() as session:
        tags = list(await session.scalars(select(Tag).where(Tag.name == "VrWalk")))
    # The work happened once, and the second press is told the action is spent rather
    # than the screen being replaced: the button is still there, it is just used up.
    assert len(tags) == 1
    assert [(text, alert) for text, alert in second.answers if alert] == [
        ("This action expired. Reopen the screen.", True)
    ]
    assert "out of date" not in message.rendered[-1]


async def test_read_queries_beside_a_proposal_still_resume_the_agent(e2e_harness):
    """PR-QUEUE-007 — tests/brd/proposals.feature

    A read call in the same turn stores rows, not an outcome; the receipt must survive it.
    """
    async with e2e_harness.sessions() as session:
        await create_manual_card(session, title="Выпустить в прод VrWalk")
        await session.commit()

    advisor, provider = e2e_harness.advisor(
        [
            mutation_turn(
                ("tag", {"mode": "create", "name": "VrWalk"}),
                ("query_data", {"sql": "SELECT missing_column FROM ai_cards"}),
                ("query_data", {"sql": "SELECT id FROM ai_cards"}),
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
        root=advisor,
        history=_QueueTestHistory(),
        owner_id=42,
        turn=TurnManager(),
        screens=SCREENS,
        chat=ChatHost(TelegramNotes(e2e_harness.sessions), MARKS, spawn=spawn_timer),
        callback_actions=FEATURE_CALLBACK_ACTIONS,
        text_inputs=FEATURE_TEXT_INPUTS,
    )
    await render_proposal(message, services, outcome.proposal_id)
    async with e2e_harness.sessions() as session:
        token = await session.scalar(
            select(CallbackToken).where(CallbackToken.action == "proposal_approve")
        )
        assert token is not None

    await callback_token_handler(_QueueTestCallback(token.token, message), services)

    async with e2e_harness.sessions() as session:
        proposal = advisor.reviews.proposal(outcome.proposal_id)
        tag = await session.scalar(select(Tag).where(Tag.name == "VrWalk"))
    assert proposal is None
    assert tag is not None
    assert "✅ Saved — New Tag “VrWalk”" in message.rendered[-1]
    assert "The VrWalk tag was saved; I will retry the query." in message.rendered[-1]
    assert "could not generate its follow-up" not in message.rendered[-1]
    assert len(provider.calls) == 3
    resumed_query_results = [
        str(item["content"])
        for item in provider.calls[-1]
        if item.get("role") == "tool" and item.get("name") == "query_data"
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
    """PR-SAVE-010 — tests/brd/proposals.feature"""
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
        root=advisor,
        history=_QueueTestHistory(),
        owner_id=42,
        turn=TurnManager(),
        screens=SCREENS,
        chat=ChatHost(TelegramNotes(e2e_harness.sessions), MARKS, spawn=spawn_timer),
        callback_actions=FEATURE_CALLBACK_ACTIONS,
        text_inputs=FEATURE_TEXT_INPUTS,
    )
    await render_proposal(message, services, first.proposal_id)

    await _resolve_queued_proposal(
        e2e_harness, services, message, first.proposal_id, "proposal_approve"
    )
    second_id = next((review.id for review in e2e_harness.reviews.open_proposals), None)
    assert second_id is not None

    await _resolve_queued_proposal(e2e_harness, services, message, second_id, "proposal_reject")

    final_text = message.rendered[-1]
    # The model must be told what the whole request actually did, not only the last step.
    assert "✅ Saved — New Action “First”" in final_text
    assert "🗑 Discarded — New Action “Second”" in final_text
    assert "Handled both proposals." in final_text
    assert len(provider.calls) == 2


async def test_failed_call_result_states_that_its_siblings_are_still_queued(e2e_harness):
    """PR-REPAIR-015 — tests/brd/proposals.feature"""
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

    batch = next(iter(advisor.reviews.open_batches), None)
    failed = next(tool for tool in batch.tool_calls if tool["proposal_id"] is None)
    queued = [tool for tool in batch.tool_calls if tool["proposal_id"]]

    # The prompt no longer explains sibling semantics every turn; the failing call says it.
    assert failed["result"]["status"] == "error"
    assert "were not cancelled" in failed["result"]["next"]
    assert f"{len(queued)} other call(s)" in failed["result"]["next"]


async def test_new_message_discarding_a_queue_reports_what_was_already_saved(e2e_harness):
    """PR-INTERRUPT-018 — tests/brd/proposals.feature"""
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
        root=advisor,
        history=_QueueTestHistory(),
        owner_id=42,
        turn=TurnManager(),
        screens=SCREENS,
        chat=ChatHost(TelegramNotes(e2e_harness.sessions), MARKS, spawn=spawn_timer),
        callback_actions=FEATURE_CALLBACK_ACTIONS,
        text_inputs=FEATURE_TEXT_INPUTS,
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
    """PR-FAIL-014 — tests/brd/proposals.feature"""
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
        root=advisor,
        history=_QueueTestHistory(),
        owner_id=42,
        turn=TurnManager(),
        screens=SCREENS,
        chat=ChatHost(TelegramNotes(e2e_harness.sessions), MARKS, spawn=spawn_timer),
        callback_actions=FEATURE_CALLBACK_ACTIONS,
        text_inputs=FEATURE_TEXT_INPUTS,
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
        proposal = advisor.reviews.proposal(first.proposal_id)
        tag = await session.scalar(select(Tag).where(Tag.name == "VrWalk"))
        link = await session.scalar(select(CardTag).where(CardTag.card_id == card.id))
    assert proposal is None
    assert tag is None
    assert link is None


async def test_single_proposal_save_error_is_reported_and_resolved(e2e_harness):
    """PR-FAIL-014 — tests/brd/proposals.feature"""
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
        root=advisor,
        history=_QueueTestHistory(),
        owner_id=42,
        turn=TurnManager(),
        screens=SCREENS,
        chat=ChatHost(TelegramNotes(e2e_harness.sessions), MARKS, spawn=spawn_timer),
        callback_actions=FEATURE_CALLBACK_ACTIONS,
        text_inputs=FEATURE_TEXT_INPUTS,
    )
    await render_proposal(message, services, outcome.proposal_id)
    async with e2e_harness.sessions() as session:
        old_token = await session.scalar(
            select(CallbackToken).where(CallbackToken.action == "proposal_approve")
        )
        assert old_token is not None

    await callback_token_handler(_QueueTestCallback(old_token.token, message), services)

    async with e2e_harness.sessions() as session:
        proposal = advisor.reviews.proposal(outcome.proposal_id)
        retry_token = await session.scalar(
            select(CallbackToken).where(
                CallbackToken.action == "proposal_approve",
                CallbackToken.consumed_at.is_(None),
            )
        )
        tags = list(await session.scalars(select(Tag)))
    assert proposal is None
    assert retry_token is None
    assert len(tags) == 1
    assert "⚠️ Failed — New Tag “vrwalk”" in message.rendered[-1]
    assert "already exists" in message.rendered[-1]
    assert "could not generate its follow-up" in message.rendered[-1]
    assert len(provider.calls) == 2


@pytest.mark.parametrize(
    ("callback_action", "resolved_text"),
    [
        ("proposal_approve", "Saved"),
        ("proposal_reject", "Discarded"),
    ],
)
async def test_single_tag_callback_never_leaves_dead_buttons_when_follow_up_fails(
    e2e_harness,
    callback_action,
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
        root=advisor,
        history=_QueueTestHistory(),
        owner_id=42,
        turn=TurnManager(),
        screens=SCREENS,
        chat=ChatHost(TelegramNotes(e2e_harness.sessions), MARKS, spawn=spawn_timer),
        callback_actions=FEATURE_CALLBACK_ACTIONS,
        text_inputs=FEATURE_TEXT_INPUTS,
    )
    await render_proposal(message, services, outcome.proposal_id)
    async with e2e_harness.sessions() as session:
        token = await session.scalar(
            select(CallbackToken).where(CallbackToken.action == callback_action)
        )
        assert token is not None

    await callback_token_handler(_QueueTestCallback(token.token, message), services)

    async with e2e_harness.sessions() as session:
        proposal = advisor.reviews.proposal(outcome.proposal_id)
    assert proposal is None
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
        affected_ids = await approve_proposal(session, advisor.reviews, PROPOSALS, first.proposal_id)
        await session.commit()

    final = await advisor.resolve_approval(
        first.proposal_id,
        decision=BatchDecision.APPROVED,
        result={"affected_ids": affected_ids},
    )

    assert final is not None
    assert final.message.startswith(receipt)
    assert final.message.count(receipt) == 1
    assert "Готово! Твоя вторая цель добавлена." in final.message
    assert len(provider.calls) == 2


async def test_resumed_request_replays_its_own_intermediate_steps(e2e_harness):
    """PR-RESULT-011 — tests/brd/proposals.feature

    A multi-step request must keep every step it already took across each approval.
    """
    advisor, provider = e2e_harness.advisor(
        [
            mutation_turn(("card", {"mode": "create", "kind": "goal", "title": "Быть здоровым"})),
            ProviderTurn(
                content="",
                tool_calls=(
                    ProviderToolCall(
                        id="broken-read",
                        name="query_data",
                        arguments_json=json.dumps({"sql": "DELETE FROM cards"}),
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
        goal_ids = await approve_proposal(session, advisor.reviews, PROPOSALS, first.proposal_id)
        await session.commit()

    # No dialogue argument: the suspended turn resumes from what it persisted itself.
    second = await advisor.resolve_approval(
        first.proposal_id, decision=BatchDecision.APPROVED, result={"affected_ids": goal_ids}
    )
    assert second is not None and second.proposal_id is not None
    async with e2e_harness.sessions() as session:
        action_ids = await approve_proposal(session, advisor.reviews, PROPOSALS, second.proposal_id)
        await session.commit()
    final = await advisor.resolve_approval(
        second.proposal_id, decision=BatchDecision.APPROVED, result={"affected_ids": action_ids}
    )

    assert final is not None and "Цель и задача готовы." in final.message
    assert len(provider.calls) == 4
    last = provider.calls[3]
    # The workspace session's context: its prompt, the workspace state, the conversation, then
    # every step it already took for this request.
    assert [message["role"] for message in last] == [
        "system",
        "user",
        "assistant",
        "tool",
        "assistant",
        "tool",
        "assistant",
        "tool",
    ]
    # The request that started the turn is still the user message the model reads.
    assert "Сделай цель Быть здоровым и задачу подтянуться" in str(last[1]["content"])
    # Step 1: the saved Goal, described rather than reduced to an ID list.
    assert '"status": "approved"' in str(last[3]["content"])
    assert "Create Card “Быть здоровым”" in str(last[3]["content"])
    assert f'"affected_ids": {json.dumps(goal_ids)}' in str(last[3]["content"])
    assert "Do not propose it again" in str(last[3]["content"])
    # Step 2: the failed read is still visible, with a bounded instruction.
    assert last[4]["tool_calls"][0]["function"]["name"] == "query_data"
    assert '"code": "unsafe_query"' in str(last[5]["content"])
    assert "do not restart the request" in str(last[5]["content"])
    # Step 3: the steps speak for themselves, so no progress digest is restated on top.
    assert all("[Current request progress" not in str(message.get("content")) for message in last)
    assert "Create Card “Подтянуться 20 раз”" in str(last[7]["content"])


async def test_suspended_batch_persists_the_request_dialogue_and_transcript(e2e_harness):
    """AG-SESSION-008 — tests/brd/agents.feature"""
    advisor, _provider = e2e_harness.advisor(
        [mutation_turn(("tag", {"mode": "create", "name": "VrWalk"}))]
    )
    outcome = await advisor.handle(
        "Create a VrWalk tag",
        dialogue=[DialogueMessage(role="user", content="[User]: Create a VrWalk tag")],
    )
    assert outcome.proposal_id is not None

    async with e2e_harness.sessions() as session:
        batch = next(iter(e2e_harness.reviews.open_batches), None)
        run = await session.get(AgentRun, batch.run_id)
        state = run.state_json
    # The batch holds the screens; the session holds what it needs to continue.
    assert batch is not None
    assert state["dialogue"] == [{"role": "user", "content": "[User]: Create a VrWalk tag"}]
    assert [message["role"] for message in state["transcript"]] == ["assistant", "tool"]
    assert state["transcript"][0]["tool_calls"][0]["function"]["name"] == "tag"


async def test_the_tool_call_budget_is_carried_across_an_approval(e2e_harness):
    """AG-BUDGET-011 — tests/brd/agents.feature"""
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
        outcome.proposal_id,
        decision=BatchDecision.DISCARDED,
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
        assert await advisor.store.claim_within(session, run.id) is not None
        assert await advisor.store.claim_within(session, run.id) is None


async def test_ending_a_session_ends_every_unfinished_one_below_it(e2e_harness):
    """AG-WORDS-020 — tests/brd/agents.feature

    A subagent holds no `route`, so nothing routes three deep today. The store closes the
    branch whole anyway: the depth of a chain is its business, not its caller's.
    """
    advisor, _provider = e2e_harness.advisor(["Готово."])
    root = await advisor.store.create(kind="advisor")
    child = await advisor.store.create(kind="workspace_mutator", parent_run_id=root.id)
    grandchild = await advisor.store.create(kind="diary", parent_run_id=child.id)
    for run in (child, grandchild):
        await advisor.store.leave_interrupted(run.id, {}, "left unfinished")

    assert await advisor.store.close_unfinished_children(root.id) == 2

    async with e2e_harness.sessions() as session:
        status = {
            run.id: (run.status, run.claimed_at)
            for run in await session.scalars(select(AgentRun))
        }
    assert status[child.id] == ("abandoned", None)
    assert status[grandchild.id] == ("abandoned", None)


async def _standalone_tag_proposal(e2e_harness, advisor, name: str) -> int:
    """A proposal with no live approval batch, which is the plain receipt path."""
    outcome = await advisor.handle(f"Create a {name} tag")
    assert outcome.proposal_id is not None
    async with e2e_harness.sessions() as session:
        [e2e_harness.reviews.close_batch(batch) for batch in e2e_harness.reviews.open_batches]
        await session.commit()
    return outcome.proposal_id


@pytest.mark.parametrize(
    ("action", "heading"),
    [
        ("proposal_approve", "✅ Saved"),
        ("proposal_reject", "🗑 Discarded"),
    ],
)
async def test_a_resolved_proposal_leaves_one_readable_line_in_the_dialogue(
    e2e_harness, action, heading
):
    """PR-RESULT-011 — tests/brd/proposals.feature"""
    advisor, _provider = e2e_harness.advisor(
        [mutation_turn(("tag", {"mode": "create", "name": "VrWalk"}))]
    )
    proposal_id = await _standalone_tag_proposal(e2e_harness, advisor, "VrWalk")
    message = _QueueTestMessage()
    services = SimpleNamespace(
        sessions=e2e_harness.sessions,
        root=advisor,
        history=_QueueTestHistory(),
        owner_id=42,
        turn=TurnManager(),
        screens=SCREENS,
        chat=ChatHost(TelegramNotes(e2e_harness.sessions), MARKS, spawn=spawn_timer),
        callback_actions=FEATURE_CALLBACK_ACTIONS,
        text_inputs=FEATURE_TEXT_INPUTS,
    )
    await render_proposal(message, services, proposal_id)
    async with e2e_harness.sessions() as session:
        token = await session.scalar(
            select(CallbackToken).where(CallbackToken.action == action)
        )

    await callback_token_handler(_QueueTestCallback(token.token, message), services)

    async with e2e_harness.sessions() as session:
        proposal = advisor.reviews.proposal(proposal_id)
    assert proposal is None
    receipt = message.rendered[-1]
    assert heading in receipt
    # The owner-facing line, not "Updated 1 item(s)".
    assert "New Tag “VrWalk”" in receipt
    assert "Name: VrWalk" in receipt


async def test_navigating_away_freezes_the_proposal_into_the_same_outcome_text(e2e_harness):
    """SC-LIVE-001 — tests/brd/screens.feature"""
    advisor, _provider = e2e_harness.advisor(
        [mutation_turn(("tag", {"mode": "create", "name": "VrWalk"}))]
    )
    proposal_id = await _standalone_tag_proposal(e2e_harness, advisor, "VrWalk")
    message = _QueueTestMessage()
    services = SimpleNamespace(
        sessions=e2e_harness.sessions,
        root=advisor,
        history=_QueueTestHistory(),
        owner_id=42,
        turn=TurnManager(),
        screens=SCREENS,
        chat=ChatHost(TelegramNotes(e2e_harness.sessions), MARKS, spawn=spawn_timer),
        callback_actions=FEATURE_CALLBACK_ACTIONS,
        text_inputs=FEATURE_TEXT_INPUTS,
    )
    await render_proposal(message, services, proposal_id)
    # A screen *below* the proposal: the old "older than this message" selector missed it.
    dashboard = SimpleNamespace(message_id=1, chat=message.chat, bot=message.bot)

    await dismiss_prior_ui(dashboard, services)

    async with e2e_harness.sessions() as session:
        proposal = advisor.reviews.proposal(proposal_id)
        tag = await session.scalar(select(Tag).where(Tag.name == "VrWalk"))
    assert proposal is None
    assert tag is None
    frozen = message.bot.edits[-1]
    assert "🗑 Discarded" in frozen
    assert "You continued the conversation without saving it." in frozen
    assert "New Tag “VrWalk”" in frozen


def _screen_services(e2e_harness, advisor) -> SimpleNamespace:
    return SimpleNamespace(
        sessions=e2e_harness.sessions,
        root=advisor,
        history=_QueueTestHistory(),
        owner_id=42,
        turn=TurnManager(),
        screens=SCREENS,
        chat=ChatHost(TelegramNotes(e2e_harness.sessions), MARKS, spawn=spawn_timer),
        callback_actions=FEATURE_CALLBACK_ACTIONS,
        text_inputs=FEATURE_TEXT_INPUTS,
    )


async def test_a_proposal_screen_lists_its_fields_behind_save_and_discard(e2e_harness):
    """PR-SCREEN-003 — tests/brd/proposals.feature"""
    async with e2e_harness.sessions() as session:
        card = await create_manual_card(session, title="Release VrWalk", effort_points=3)
        await session.commit()
        card_id = card.id

    advisor, _provider = e2e_harness.advisor(
        [mutation_turn(("card", {"mode": "update", "id": card_id, "title": "Ship VrWalk"}))]
    )
    outcome = await advisor.handle("Rename the release card")
    assert outcome.proposal_id is not None
    message = _QueueTestMessage()

    await render_proposal(message, _screen_services(e2e_harness, advisor), outcome.proposal_id)

    screen = message.rendered[-1]
    assert "Release VrWalk" in screen
    assert "Ship VrWalk" in screen
    assert message.buttons() == ["✅ Save", "🗑 Discard"]


async def test_a_proposal_holding_two_changes_lists_both_on_one_screen(e2e_harness):
    """PR-SCREEN-003 — tests/brd/proposals.feature"""
    async with e2e_harness.sessions() as session:
        workspace = await session.get(Workspace, 1)
    proposal_id = e2e_harness.reviews.open_proposal(
        message="Two tags in one proposal",
        workspace_revision=workspace.revision,
        changes=[
            ProposalChange(entity="tag", action=ChangeAction.CREATE, values={"name": "VrWalk"}),
            ProposalChange(entity="tag", action=ChangeAction.CREATE, values={"name": "Release"}),
        ],
    ).id

    advisor, _provider = e2e_harness.advisor([])
    message = _QueueTestMessage()

    await render_proposal(message, _screen_services(e2e_harness, advisor), proposal_id)

    screen = message.rendered[-1]
    assert "VrWalk" in screen
    assert "Release" in screen
    assert message.buttons() == ["✅ Save", "🗑 Discard"]


async def test_one_call_setting_several_fields_is_one_proposal(e2e_harness):
    """PR-QUEUE-005 — tests/brd/proposals.feature"""
    advisor, _provider = e2e_harness.advisor(
        [
            mutation_turn(
                (
                    "card",
                    {
                        "mode": "create",
                        "kind": "action",
                        "title": "Ship VrWalk",
                        "note": "cut the release branch",
                        "stage": "today",
                        "effort_points": 5,
                    },
                )
            )
        ]
    )

    outcome = await advisor.handle("Add the release action")

    assert outcome.kind is AIOutcomeKind.PROPOSAL
    proposals = list(advisor.reviews.open_proposals)
    changes = [change for review in proposals for change in review.changes]
    assert len(proposals) == 1
    assert len(changes) == 1
    assert changes[0].values["title"] == "Ship VrWalk"
    assert changes[0].values["note"] == "cut the release branch"
    assert changes[0].values["stage"] == "today"
    assert changes[0].values["effort_points"] == 5
    # A proposal alone in its queue is not numbered.
    assert "Proposal 1/" not in proposals[0].message


async def test_saving_one_proposal_leaves_the_queued_ones_saveable(e2e_harness):
    """PR-QUEUE-008 — tests/brd/proposals.feature"""
    async with e2e_harness.sessions() as session:
        card = await create_manual_card(session, title="Release", effort_points=3)
        await session.commit()
        card_id = card.id

    advisor, provider = e2e_harness.advisor(
        [
            mutation_turn(
                ("card", {"mode": "update", "id": card_id, "title": "Ship VrWalk"}),
                ("card", {"mode": "update", "id": card_id, "note": "cut the branch"}),
                ("card", {"mode": "update", "id": card_id, "effort_points": 5}),
            ),
            "All three edits are saved.",
        ]
    )
    outcome = await advisor.handle("Three edits to the release card")
    assert outcome.proposal_id is not None
    message = _QueueTestMessage()
    services = _screen_services(e2e_harness, advisor)
    await render_proposal(message, services, outcome.proposal_id)

    for _ in range(3):
        pending = next((review.id for review in e2e_harness.reviews.open_proposals), None)
        assert pending is not None
        await _resolve_queued_proposal(
            e2e_harness, services, message, pending, "proposal_approve"
        )

    async with e2e_harness.sessions() as session:
        card = await session.get(Card, card_id)
        remaining = [review.id for review in e2e_harness.reviews.open_proposals]
    assert remaining == []
    assert card.title == "Ship VrWalk"
    assert card.note == "cut the branch"
    assert card.effort_points == 5
    assert len(provider.calls) == 2


async def test_deleting_a_card_asks_once_more_before_it_goes(e2e_harness):
    """PR-SCREEN-004 — tests/brd/proposals.feature"""
    async with e2e_harness.sessions() as session:
        card = await create_manual_card(session, title="Release VrWalk", effort_points=3)
        tag = await create_tag(session, "VrWalk")
        await session.commit()
        card_id, tag_id = card.id, tag.id

    advisor, _provider = e2e_harness.advisor(
        [
            mutation_turn(
                ("remove", {"mode": "delete", "entity": "card", "id": card_id}),
                ("remove", {"mode": "delete", "entity": "tag", "id": tag_id}),
            ),
            "Both are gone.",
        ]
    )
    outcome = await advisor.handle("Delete the release card and the VrWalk tag")
    assert outcome.proposal_id is not None
    message = _QueueTestMessage()
    services = _screen_services(e2e_harness, advisor)
    await render_proposal(message, services, outcome.proposal_id)

    await _resolve_queued_proposal(
        e2e_harness, services, message, outcome.proposal_id, "proposal_approve"
    )

    async with e2e_harness.sessions() as session:
        assert await session.get(Card, card_id) is not None
    assert "Final destructive confirmation" in message.rendered[-1]
    assert "historical contribution" in message.rendered[-1]

    await _resolve_queued_proposal(
        e2e_harness, services, message, outcome.proposal_id, "proposal_delete_confirm"
    )

    async with e2e_harness.sessions() as session:
        assert await session.get(Card, card_id) is None
        tag_proposal = next((review.id for review in e2e_harness.reviews.open_proposals), None)
    assert tag_proposal is not None

    await _resolve_queued_proposal(
        e2e_harness, services, message, tag_proposal, "proposal_approve"
    )

    async with e2e_harness.sessions() as session:
        assert await session.get(Tag, tag_id) is None
    # The Tag went on Save alone: no second screen stood between it and the deletion.
    assert "Final destructive confirmation" not in message.rendered[-1]


async def test_a_failed_save_with_no_waiting_request_brings_the_screen_back(e2e_harness):
    """PR-FAIL-014 — tests/brd/proposals.feature"""
    async with e2e_harness.sessions() as session:
        card = await create_manual_card(session, title="Release VrWalk", effort_points=3)
        workspace = await session.get(Workspace, 1)
        await session.commit()
        proposal_id = e2e_harness.reviews.open_proposal(
            message="Link something to the release card",
            workspace_revision=workspace.revision,
            changes=[
                ProposalChange(
                    entity="card",
                    action=ChangeAction.LINK,
                    entity_id=card.id,
                    expected_version=card.version,
                    values={},
                )
            ],
        ).id

    advisor, _provider = e2e_harness.advisor([])
    message = _QueueTestMessage()
    services = _screen_services(e2e_harness, advisor)
    await render_proposal(message, services, proposal_id)

    await _resolve_queued_proposal(
        e2e_harness, services, message, proposal_id, "proposal_approve"
    )

    async with e2e_harness.sessions() as session:
        assert advisor.reviews.proposal(proposal_id) is not None
    assert "needs one relationship type" in message.rendered[-1]
    assert message.buttons() == ["✅ Save", "🗑 Discard"]
