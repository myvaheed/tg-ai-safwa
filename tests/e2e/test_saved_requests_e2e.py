from __future__ import annotations

import json

import pytest
from advisor_e2e_helpers import create_manual_card, mutation_turn, route_turn
from sqlalchemy import select

from llm_gateway import CompletionTurn as ProviderTurn
from llm_gateway import ToolCall as ProviderToolCall
from safwa.bootstrap.modules import (
    ALLOWED_VIEWS,
    PROPOSALS,
)
from safwa.features.cards.model import CardStage
from safwa.features.saved_requests.api import request_cards
from safwa.features.saved_requests.model import SavedRequest
from safwa.features.saved_requests.use_cases import create_saved_request
from safwa.features.tags.model import CardTag, Tag
from safwa.features.values.model import CardValue, Value
from safwa.foundation.marks import title_marks
from tg_agent_shell.ai.outcome import AIOutcomeKind
from tg_agent_shell.foundation.errors import StaleStateError
from tg_agent_shell.proposals.use_cases import approve_proposal

pytestmark = pytest.mark.e2e


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
    advisor, _provider = e2e_harness.advisor([route_turn("workspace_mutator"), response])
    outcome = await advisor.handle("Create a Request for my Family actions")

    assert outcome.proposal_id is not None
    async with e2e_harness.sessions() as session:
        affected = await approve_proposal(session, advisor.reviews, PROPOSALS, outcome.proposal_id)
        await session.commit()
        request = await session.get(SavedRequest, affected[0])
        assert request is not None
        matches = await request_cards(session, request.query_sql, ALLOWED_VIEWS)
        assert [card.id for card in matches] == [action.id]


async def test_ai_request_update_is_rejected_when_the_request_becomes_stale(e2e_harness):
    """PR-STALE-012 — tests/brd/tg_agent_shell/proposals.feature"""
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
    advisor, _provider = e2e_harness.advisor([route_turn("workspace_mutator"), response])
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


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT id FROM cards",
        # The id is real and belongs to a CTE nobody selects it from, so the Request would
        # answer with amounts; the Cards here are named inside a string and read nowhere.
        "WITH c AS (SELECT id FROM ai_cards) SELECT 1 AS amount FROM c",
        "SELECT id FROM ai_values WHERE name <> 'ai_cards'",
    ],
)
async def test_ai_request_with_unsafe_sql_never_becomes_a_proposal(e2e_harness, sql):
    """SR-AI-007 — tests/brd/saved_requests.feature"""
    response = mutation_turn(
        (
            "request",
            {
                "mode": "create",
                "name": "Everything",
                "sql": sql,
            },
        )
    )
    advisor, provider = e2e_harness.advisor(
        [
            route_turn("workspace_mutator"),
            response,
            "That query is not allowed, so I proposed nothing.",
            "That query is not allowed, so I proposed nothing.",
        ]
    )

    outcome = await advisor.handle("Save a Request over every card")

    assert outcome.kind is AIOutcomeKind.ANSWER
    tool_result = json.loads(str(provider.calls[2][-1]["content"]))
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
    advisor, _provider = e2e_harness.advisor([route_turn("workspace_mutator"), response])
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
    advisor, _provider = e2e_harness.advisor([route_turn("workspace_mutator"), response])
    outcome = await advisor.handle("Create an urgent actions Request")

    async with e2e_harness.sessions() as session:
        affected = await approve_proposal(session, advisor.reviews, PROPOSALS, outcome.proposal_id or "")
        await session.commit()
        request = await session.get(SavedRequest, affected[0])
        assert request is not None
        matches = await request_cards(session, request.query_sql, ALLOWED_VIEWS)
        assert {card.id for card in matches} == {today.id, critical.id}
        assert ordinary.id not in {card.id for card in matches}
