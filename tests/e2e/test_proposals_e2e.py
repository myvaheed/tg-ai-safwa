from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from advisor_e2e_helpers import create_manual_card, mutation_turn
from review_e2e_helpers import (
    QueueTestCallback,
    QueueTestHistory,
    QueueTestMessage,
    resolve_queued_proposal,
    review_services,
    standalone_tag_proposal,
)
from sqlalchemy import func, select
from ui_harness import spawn_timer

from llm_gateway import CompletionTurn as ProviderTurn
from llm_gateway import ToolCall as ProviderToolCall
from safwa.bootstrap.modules import (
    FEATURE_CALLBACK_ACTIONS,
    FEATURE_TEXT_INPUTS,
    PROPOSALS,
    SCREENS,
)
from safwa.features.cards.model import Card, CardStage
from safwa.features.planning.use_cases import sprint_metrics, start_sprint
from safwa.features.tags.model import CardTag, Tag
from safwa.features.tags.use_cases import create_tag
from safwa.features.values.model import CardValue, Value
from safwa.foundation.workspace import Workspace
from telegram_llm import ChatHost
from tg_agent_shell.ai.outcome import AIOutcomeKind
from tg_agent_shell.ai.runs import AgentRun
from tg_agent_shell.foundation.kinds import MARKS
from tg_agent_shell.history import TelegramNotes
from tg_agent_shell.proposals.model import (
    BatchDecision,
    ChangeAction,
    ProposalChange,
)
from tg_agent_shell.proposals.telegram import render_proposal
from tg_agent_shell.proposals.use_cases import approve_proposal
from tg_agent_shell.telegram import callback_token_handler, dismiss_prior_ui
from tg_agent_shell.telegram.model import CallbackToken
from tg_agent_shell.turn import TurnManager

pytestmark = pytest.mark.e2e


async def test_invalid_create_returns_minimal_repair_arguments_to_the_model(e2e_harness):
    """PR-REPAIR-015 — tests/brd/tg_agent_shell/proposals.feature"""
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
    """PR-SAVE-009 — tests/brd/tg_agent_shell/proposals.feature"""
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


async def test_multiple_ai_card_creations_are_reviewed_sequentially(e2e_harness):
    """PR-QUEUE-006 — tests/brd/tg_agent_shell/proposals.feature"""
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
    """PR-FAIL-014 — tests/brd/tg_agent_shell/proposals.feature"""
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
    """PR-REPAIR-015 — tests/brd/tg_agent_shell/proposals.feature"""
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
    """PR-REPAIR-016 — tests/brd/tg_agent_shell/proposals.feature"""
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


async def test_ai_approved_tag_proposal_creates_a_reusable_tag(e2e_harness):
    """PR-SAVE-009 — tests/brd/tg_agent_shell/proposals.feature"""
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
    """PR-QUEUE-005 — tests/brd/tg_agent_shell/proposals.feature"""
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
    """PR-QUEUE-005 — tests/brd/tg_agent_shell/proposals.feature"""
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


async def test_independent_mutations_are_reviewed_in_order_before_one_resume(e2e_harness):
    """PR-QUEUE-006 — tests/brd/tg_agent_shell/proposals.feature"""
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
    """PR-SAVE-010 — tests/brd/tg_agent_shell/proposals.feature"""
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
    """PR-INTERRUPT-017 — tests/brd/tg_agent_shell/proposals.feature"""
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


async def test_query_then_link_continuation_can_suspend_for_a_second_queue(e2e_harness):
    """PR-QUEUE-007 — tests/brd/tg_agent_shell/proposals.feature"""
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
    """PR-SCREEN-003 — tests/brd/tg_agent_shell/proposals.feature"""
    advisor, provider = e2e_harness.advisor(
        [
            mutation_turn(("tag", {"mode": "create", "name": "VrWalk"})),
            final_text,
        ]
    )
    outcome = await advisor.handle("Create a VrWalk tag")
    assert outcome.proposal_id is not None
    message = QueueTestMessage()
    services = SimpleNamespace(
        sessions=e2e_harness.sessions,
        root=advisor,
        history=QueueTestHistory(),
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

    await callback_token_handler(QueueTestCallback(token.token, message), services)

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


async def test_read_queries_beside_a_proposal_still_resume_the_agent(e2e_harness):
    """PR-QUEUE-007 — tests/brd/tg_agent_shell/proposals.feature

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
    message = QueueTestMessage()
    services = SimpleNamespace(
        sessions=e2e_harness.sessions,
        root=advisor,
        history=QueueTestHistory(),
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

    await callback_token_handler(QueueTestCallback(token.token, message), services)

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


async def test_discarding_the_last_queued_proposal_still_reports_saved_siblings(e2e_harness):
    """PR-SAVE-010 — tests/brd/tg_agent_shell/proposals.feature"""
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
    message = QueueTestMessage()
    services = SimpleNamespace(
        sessions=e2e_harness.sessions,
        root=advisor,
        history=QueueTestHistory(),
        owner_id=42,
        turn=TurnManager(),
        screens=SCREENS,
        chat=ChatHost(TelegramNotes(e2e_harness.sessions), MARKS, spawn=spawn_timer),
        callback_actions=FEATURE_CALLBACK_ACTIONS,
        text_inputs=FEATURE_TEXT_INPUTS,
    )
    await render_proposal(message, services, first.proposal_id)

    await resolve_queued_proposal(
        e2e_harness, services, message, first.proposal_id, "proposal_approve"
    )
    second_id = next((review.id for review in e2e_harness.reviews.open_proposals), None)
    assert second_id is not None

    await resolve_queued_proposal(e2e_harness, services, message, second_id, "proposal_reject")

    final_text = message.rendered[-1]
    # The model must be told what the whole request actually did, not only the last step.
    assert "✅ Saved — New Action “First”" in final_text
    assert "🗑 Discarded — New Action “Second”" in final_text
    assert "Handled both proposals." in final_text
    assert len(provider.calls) == 2


async def test_failed_call_result_states_that_its_siblings_are_still_queued(e2e_harness):
    """PR-REPAIR-015 — tests/brd/tg_agent_shell/proposals.feature"""
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
    """PR-INTERRUPT-018 — tests/brd/tg_agent_shell/proposals.feature"""
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
    screen = QueueTestMessage()
    services = SimpleNamespace(
        sessions=e2e_harness.sessions,
        root=advisor,
        history=QueueTestHistory(),
        owner_id=42,
        turn=TurnManager(),
        screens=SCREENS,
        chat=ChatHost(TelegramNotes(e2e_harness.sessions), MARKS, spawn=spawn_timer),
        callback_actions=FEATURE_CALLBACK_ACTIONS,
        text_inputs=FEATURE_TEXT_INPUTS,
    )
    await render_proposal(screen, services, first.proposal_id)
    await resolve_queued_proposal(
        e2e_harness, services, screen, first.proposal_id, "proposal_approve"
    )

    follow_up = QueueTestMessage()
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
    """PR-FAIL-014 — tests/brd/tg_agent_shell/proposals.feature"""
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
    message = QueueTestMessage()
    services = SimpleNamespace(
        sessions=e2e_harness.sessions,
        root=advisor,
        history=QueueTestHistory(),
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
    await callback_token_handler(QueueTestCallback(reject.token, message), services)

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
    """PR-FAIL-014 — tests/brd/tg_agent_shell/proposals.feature"""
    async with e2e_harness.sessions() as session:
        session.add(Tag(name="VrWalk"))
        await session.commit()

    advisor, provider = e2e_harness.advisor(
        [mutation_turn(("tag", {"mode": "create", "name": "vrwalk"}))]
    )
    outcome = await advisor.handle("Create a VrWalk tag")
    assert outcome.proposal_id is not None
    message = QueueTestMessage()
    services = SimpleNamespace(
        sessions=e2e_harness.sessions,
        root=advisor,
        history=QueueTestHistory(),
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

    await callback_token_handler(QueueTestCallback(old_token.token, message), services)

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
    message = QueueTestMessage()
    services = SimpleNamespace(
        sessions=e2e_harness.sessions,
        root=advisor,
        history=QueueTestHistory(),
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

    await callback_token_handler(QueueTestCallback(token.token, message), services)

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
    """PR-RESULT-011 — tests/brd/tg_agent_shell/proposals.feature"""
    advisor, _provider = e2e_harness.advisor(
        [mutation_turn(("tag", {"mode": "create", "name": "VrWalk"}))]
    )
    proposal_id = await standalone_tag_proposal(e2e_harness, advisor, "VrWalk")
    message = QueueTestMessage()
    services = SimpleNamespace(
        sessions=e2e_harness.sessions,
        root=advisor,
        history=QueueTestHistory(),
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

    await callback_token_handler(QueueTestCallback(token.token, message), services)

    async with e2e_harness.sessions() as session:
        proposal = advisor.reviews.proposal(proposal_id)
    assert proposal is None
    receipt = message.rendered[-1]
    assert heading in receipt
    # The owner-facing line, not "Updated 1 item(s)".
    assert "New Tag “VrWalk”" in receipt
    assert "Name: VrWalk" in receipt


async def test_one_call_setting_several_fields_is_one_proposal(e2e_harness):
    """PR-QUEUE-005 — tests/brd/tg_agent_shell/proposals.feature"""
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
    """PR-QUEUE-008 — tests/brd/tg_agent_shell/proposals.feature"""
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
    message = QueueTestMessage()
    services = review_services(e2e_harness, advisor)
    await render_proposal(message, services, outcome.proposal_id)

    for _ in range(3):
        pending = next((review.id for review in e2e_harness.reviews.open_proposals), None)
        assert pending is not None
        await resolve_queued_proposal(
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
    """PR-SCREEN-004 — tests/brd/tg_agent_shell/proposals.feature"""
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
    message = QueueTestMessage()
    services = review_services(e2e_harness, advisor)
    await render_proposal(message, services, outcome.proposal_id)

    await resolve_queued_proposal(
        e2e_harness, services, message, outcome.proposal_id, "proposal_approve"
    )

    async with e2e_harness.sessions() as session:
        assert await session.get(Card, card_id) is not None
    assert "Final destructive confirmation" in message.rendered[-1]
    assert "historical contribution" in message.rendered[-1]

    await resolve_queued_proposal(
        e2e_harness, services, message, outcome.proposal_id, "proposal_delete_confirm"
    )

    async with e2e_harness.sessions() as session:
        assert await session.get(Card, card_id) is None
        tag_proposal = next((review.id for review in e2e_harness.reviews.open_proposals), None)
    assert tag_proposal is not None

    await resolve_queued_proposal(
        e2e_harness, services, message, tag_proposal, "proposal_approve"
    )

    async with e2e_harness.sessions() as session:
        assert await session.get(Tag, tag_id) is None
    # The Tag went on Save alone: no second screen stood between it and the deletion.
    assert "Final destructive confirmation" not in message.rendered[-1]


async def test_a_failed_save_with_no_waiting_request_brings_the_screen_back(e2e_harness):
    """PR-FAIL-014 — tests/brd/tg_agent_shell/proposals.feature"""
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
    message = QueueTestMessage()
    services = review_services(e2e_harness, advisor)
    await render_proposal(message, services, proposal_id)

    await resolve_queued_proposal(
        e2e_harness, services, message, proposal_id, "proposal_approve"
    )

    async with e2e_harness.sessions() as session:
        assert advisor.reviews.proposal(proposal_id) is not None
    assert "needs one relationship type" in message.rendered[-1]
    assert message.buttons() == ["✅ Save", "🗑 Discard"]
