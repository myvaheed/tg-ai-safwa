from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy import select, text

from safwa.bootstrap.modules import AI_VIEWS, ALLOWED_VIEWS, AUTOAPPROVALS
from safwa.features.cards.model import Card
from safwa.features.saved_requests.api import request_cards
from safwa.features.saved_requests.model import SavedRequest
from safwa.features.saved_requests.use_cases import (
    create_saved_request,
    delete_saved_request,
    update_saved_request,
)
from safwa.features.tags.model import CardTag, Tag
from safwa.features.workspace_mutator.remove import RemoveToolInput
from tg_agent_shell.ai.autoapproval import AutoApprovalCandidate, AutoApprovalReviewer
from tg_agent_shell.ai.sql import RequestQueryError, create_ai_views
from tg_agent_shell.foundation.errors import DomainError


async def test_saved_request_runs_a_safe_card_query(sessions):
    """SR-RUN-006 — tests/brd/saved_requests.feature"""
    async with sessions() as session:
        await (await session.connection()).run_sync(
            lambda connection: create_ai_views(connection, AI_VIEWS)
        )
        family = Tag(name="Family")
        work = Tag(name="Work")
        family_today = Card(
            kind="action",
            title="Call family",
            manual_stage="today",
            effective_stage="today",
            effort_points=1,
        )
        family_backlog = Card(
            kind="action",
            title="Plan family trip",
            manual_stage="backlog",
            effective_stage="backlog",
            effort_points=3,
        )
        work_today = Card(
            kind="action",
            title="Prepare report",
            manual_stage="today",
            effective_stage="today",
            effort_points=3,
        )
        session.add_all([family, work, family_today, family_backlog, work_today])
        await session.flush()
        session.add_all(
            [
                CardTag(card_id=family_today.id, tag_id=family.id),
                CardTag(card_id=family_backlog.id, tag_id=family.id),
                CardTag(card_id=work_today.id, tag_id=work.id),
            ]
        )
        request = await create_saved_request(
            session,
            "Family actions to work on",
            "SELECT id FROM ai_cards WHERE kind = 'action' "
            "AND direct_tags LIKE '%Family%' AND stage IN ('today', 'backlog') ORDER BY title",
            "Family tasks that are live.",
            views=ALLOWED_VIEWS,
        )
        await session.commit()

    async with sessions() as session:
        stored = await session.get(SavedRequest, request.id)
        assert stored is not None
        cards = await request_cards(session, stored.query_sql, ALLOWED_VIEWS)
        assert [card.title for card in cards] == ["Call family", "Plan family trip"]


async def test_a_request_returns_each_card_once(sessions):
    """SR-RUN-006 — tests/brd/saved_requests.feature"""
    async with sessions() as session:
        await (await session.connection()).run_sync(
            lambda connection: create_ai_views(connection, AI_VIEWS)
        )
        card = Card(
            kind="action",
            title="Stretch",
            manual_stage="today",
            effective_stage="today",
            effort_points=1,
        )
        session.add(card)
        await session.flush()
        # A UNION ALL over the same Card is the shape a model reaches for when it wants two
        # conditions; the Card is one Card, and the screen must not list it twice.
        request = await create_saved_request(
            session,
            "Either way",
            "SELECT id FROM ai_cards WHERE kind = 'action' "
            "UNION ALL SELECT id FROM ai_cards WHERE stage = 'today'",
            views=ALLOWED_VIEWS,
        )
        await session.commit()

    async with sessions() as session:
        stored = await session.get(SavedRequest, request.id)
        assert stored is not None
        assert [item.id for item in await request_cards(session, stored.query_sql, ALLOWED_VIEWS)] == [
            card.id
        ]


@pytest.mark.parametrize(
    "query_sql",
    [
        "DELETE FROM ai_cards",
        "SELECT id FROM cards",
        "SELECT id FROM ai_tags",
        "SELECT title FROM ai_cards",
        # A Request runs on the ordinary session, under no authorizer, so the shared
        # validator is the whole of what stops it naming a table nothing published.
        "SELECT id FROM cards WHERE 'WITH cards AS (' <> ''",
    ],
)
async def test_saved_request_rejects_non_read_or_non_card_queries(sessions, query_sql):
    """SR-SQL-004 — tests/brd/saved_requests.feature"""
    async with sessions() as session:
        with pytest.raises(DomainError):
            await create_saved_request(session, "Unsafe request", query_sql, views=ALLOWED_VIEWS)
        assert list(await session.scalars(select(SavedRequest))) == []


async def test_a_stored_statement_is_checked_again_before_it_runs(sessions):
    """SR-SQL-005 — tests/brd/saved_requests.feature"""
    async with sessions() as session:
        await (await session.connection()).run_sync(
            lambda connection: create_ai_views(connection, AI_VIEWS)
        )
        request = await create_saved_request(
            session, "All goals", "SELECT id FROM ai_cards WHERE kind = 'goal'", views=ALLOWED_VIEWS
        )
        # Straight to the column: the write paths refuse this, and that is the point — what
        # runs is checked when it runs, not only when it was stored.
        await session.execute(
            text("UPDATE saved_requests SET query_sql = :sql WHERE id = :id"),
            {"sql": "SELECT id FROM cards", "id": request.id},
        )
        await session.commit()

    async with sessions() as session:
        stored = await session.get(SavedRequest, request.id)
        assert stored is not None
        with pytest.raises(RequestQueryError):
            await request_cards(session, stored.query_sql, ALLOWED_VIEWS)


async def test_a_request_name_is_taken_whatever_its_case(sessions):
    """SR-WRITE-002 — tests/brd/saved_requests.feature"""
    async with sessions() as session:
        await create_saved_request(
            session, "All goals", "SELECT id FROM ai_cards WHERE kind = 'goal'", views=ALLOWED_VIEWS
        )
        other = await create_saved_request(
            session, "Open actions", "SELECT id FROM ai_cards WHERE kind = 'action'",
            views=ALLOWED_VIEWS,
        )
        await session.commit()

        with pytest.raises(DomainError, match="already exists"):
            await update_saved_request(session, other.id, name="ALL GOALS", views=ALLOWED_VIEWS)
        with pytest.raises(DomainError, match="cannot be empty"):
            await update_saved_request(session, other.id, name="   ", views=ALLOWED_VIEWS)
        with pytest.raises(DomainError, match="cannot be empty"):
            await create_saved_request(
                session, "  ", "SELECT id FROM ai_cards", views=ALLOWED_VIEWS
            )


def test_a_request_query_is_never_allowlisted_for_autoapproval():
    """SR-AI-010 — tests/brd/saved_requests.feature"""
    reviewer = AutoApprovalReviewer(provider=None, rules=AUTOAPPROVALS)

    def candidate(action: str, values: dict[str, object]) -> AutoApprovalCandidate:
        return AutoApprovalCandidate(
            user_request="Rename that Request",
            entity="request",
            action=action,
            entity_id=7,
            values=values,
            summary="Request “All goals”",
            fields=[],
        )

    assert reviewer.rule_for(candidate("update", {"name": "Every goal"})) is not None
    # `prepare` stores the normalized statement as `query_sql`, so re-aiming a Request never
    # matches the allowlisted field set and never reaches the reviewer at all.
    assert reviewer.rule_for(candidate("update", {"query_sql": "SELECT id FROM ai_cards"})) is None
    assert reviewer.rule_for(candidate("update", {"name": "X", "query_sql": "SELECT id"})) is None
    assert reviewer.rule_for(candidate("create", {"name": "X"})) is None


async def test_a_request_is_deleted_not_archived(sessions):
    """SR-DELETE-013 — tests/brd/saved_requests.feature"""
    async with sessions() as session:
        request = await create_saved_request(
            session,
            "All goals",
            "SELECT id FROM ai_cards WHERE kind = 'goal'",
            "Original description",
            views=ALLOWED_VIEWS,
        )
        await session.commit()

        await delete_saved_request(session, request.id)
        await session.commit()

        assert await session.get(SavedRequest, request.id) is None
        with pytest.raises(DomainError, match="Request does not exist"):
            await delete_saved_request(session, request.id)

        # The name is free from that moment, and what takes it is a new Request.
        again = await create_saved_request(
            session, "all GOALS", "SELECT id FROM ai_cards", views=ALLOWED_VIEWS
        )
        await session.commit()
        assert again.description == ""
        assert len(list(await session.scalars(select(SavedRequest)))) == 1


def test_the_remove_tool_refuses_to_archive_a_request():
    """SR-DELETE-013 — tests/brd/saved_requests.feature"""
    assert RemoveToolInput(mode="delete", entity="request", id=1).entity == "request"
    with pytest.raises(ValidationError, match="a request is deleted, never archived"):
        RemoveToolInput(mode="archive", entity="request", id=1)
