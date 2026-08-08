from __future__ import annotations

import pytest
from sqlalchemy import select

from safwa.ai.sql import create_ai_views
from safwa.domain import DomainError, create_saved_request
from safwa.models import Card, CardTag, SavedRequest, Tag
from safwa.saved_requests import request_cards


async def test_saved_request_runs_a_safe_card_query(sessions):
    async with sessions() as session:
        await (await session.connection()).run_sync(create_ai_views)
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
        )
        await session.commit()

    async with sessions() as session:
        stored = await session.get(SavedRequest, request.id)
        assert stored is not None
        cards = await request_cards(session, stored.query_sql)
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
            await create_saved_request(session, "Unsafe request", query_sql)
        assert list(await session.scalars(select(SavedRequest))) == []
