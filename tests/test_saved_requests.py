from __future__ import annotations

import pytest
from sqlalchemy import select

from safwa.ai.sql import create_ai_views
from safwa.bootstrap.modules import AI_VIEWS, ALLOWED_VIEWS
from safwa.domain import DomainError, archive_saved_request, create_saved_request
from safwa.models import Card, CardTag, SavedRequest, Tag
from safwa.saved_requests import request_cards


async def test_saved_request_runs_a_safe_card_query(sessions):
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


@pytest.mark.parametrize(
    "query_sql",
    [
        "DELETE FROM ai_cards",
        "SELECT id FROM cards",
        "SELECT id FROM ai_tags",
        "SELECT title FROM ai_cards",
    ],
)
async def test_saved_request_rejects_non_read_or_non_card_queries(sessions, query_sql):
    async with sessions() as session:
        with pytest.raises(DomainError):
            await create_saved_request(session, "Unsafe request", query_sql, views=ALLOWED_VIEWS)
        assert list(await session.scalars(select(SavedRequest))) == []


async def test_create_request_restores_an_archived_name(sessions):
    async with sessions() as session:
        request = await create_saved_request(
            session,
            "All goals",
            "SELECT id FROM ai_cards WHERE kind = 'goal'",
            "Original description",
            views=ALLOWED_VIEWS,
        )
        request_id = request.id
        await archive_saved_request(session, request.id)
        await session.commit()

        restored = await create_saved_request(
            session,
            "all GOALS",
            "SELECT id FROM ai_cards WHERE kind = 'goal' AND stage = 'backlog'",
            views=ALLOWED_VIEWS,
        )
        await session.commit()

        assert restored.id == request_id
        assert restored.archived_at is None
        assert restored.description == "Original description"
        assert "stage = 'backlog'" in restored.query_sql
        assert len(list(await session.scalars(select(SavedRequest)))) == 1
        with pytest.raises(DomainError, match="already exists"):
            await create_saved_request(
                session,
                "ALL GOALS",
                "SELECT id FROM ai_cards WHERE kind = 'goal'",
                views=ALLOWED_VIEWS,
            )
