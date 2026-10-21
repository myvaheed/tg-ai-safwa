from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy import select, text

from safwa.bootstrap.modules import AI_VIEWS, ALLOWED_VIEWS, PROPOSALS
from safwa.constants import INBOX_TAG_NAME
from safwa.features.cards.model import Card
from safwa.features.cards.use_cases import create_card, toggle_card_tag
from safwa.features.checks.model import Check
from safwa.features.checks.use_cases import create_check, delete_check
from safwa.features.saved_requests.api import RequestQueryError, request_items
from safwa.features.saved_requests.model import SavedRequest
from safwa.features.saved_requests.use_cases import (
    create_saved_request,
    delete_saved_request,
    seed_inbox_request,
    update_saved_request,
)
from safwa.features.tags.model import CardTag, Tag
from safwa.features.tags.use_cases import create_tag, seed_inbox_tag
from safwa.features.workspace_mutator.remove import RemoveToolInput
from safwa.foundation.marks import title_marks
from tg_agent_shell.ai.sql import create_ai_views
from tg_agent_shell.foundation.errors import DomainError
from tg_agent_shell.proposals.model import ChangeAction, ProposalChange


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
            "SELECT 'card' AS item_type, id FROM ai_cards WHERE kind = 'action' "
            "AND direct_tags LIKE '%Family%' AND stage IN ('today', 'backlog') ORDER BY title",
            "Family tasks that are live.",
            views=ALLOWED_VIEWS,
        )
        await session.commit()

    async with sessions() as session:
        stored = await session.get(SavedRequest, request.id)
        assert stored is not None
        cards = await request_items(session, stored.query_sql, ALLOWED_VIEWS)
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
            "SELECT 'card' AS item_type, id FROM ai_cards WHERE kind = 'action' "
            "UNION ALL SELECT 'card' AS item_type, id FROM ai_cards WHERE stage = 'today'",
            views=ALLOWED_VIEWS,
        )
        await session.commit()

    async with sessions() as session:
        stored = await session.get(SavedRequest, request.id)
        assert stored is not None
        assert [item.id for item in await request_items(session, stored.query_sql, ALLOWED_VIEWS)] == [
            card.id
        ]


@pytest.mark.parametrize(
    "query_sql",
    [
        "SELECT id FROM ai_cards",
        "SELECT id FROM ai_checks",
        "DELETE FROM ai_cards",
        "SELECT id FROM cards",
        "SELECT id FROM ai_tags",
        "SELECT title FROM ai_cards",
        # A Request runs on the ordinary session, under no authorizer, so the shared
        # validator is the whole of what stops it naming a table nothing published.
        "SELECT id FROM cards WHERE 'WITH cards AS (' <> ''",
        # Both rules are asked of the statement, not of its text: the id here belongs to a
        # CTE nobody selects it from, and the Cards here are named inside a string.
        "WITH c AS (SELECT 'card' AS item_type, id FROM ai_cards) SELECT 1 AS amount FROM c",
        "SELECT id FROM ai_values WHERE name <> 'ai_cards'",
        "SELECT id, missing FROM ai_cards",
    ],
)
async def test_saved_request_rejects_non_read_or_non_card_queries(sessions, query_sql):
    """SR-SQL-004 — tests/brd/saved_requests.feature"""
    async with sessions() as session:
        with pytest.raises(DomainError):
            await create_saved_request(session, "Unsafe request", query_sql, views=ALLOWED_VIEWS)
        assert list(await session.scalars(select(SavedRequest))) == []


async def test_a_query_is_checked_by_its_columns_and_not_by_what_it_matches(sessions):
    """SR-SQL-004 — tests/brd/saved_requests.feature"""
    # Nothing is stored for the refused query, and a query over an empty workspace is not
    # refused for coming back with no rows: an empty result has columns like any other.
    async with sessions() as session:
        with pytest.raises(DomainError, match="column named id"):
            await create_saved_request(
                session,
                "Amounts",
                "WITH c AS (SELECT 'card' AS item_type, id FROM ai_cards) SELECT 1 AS amount FROM c",
                views=ALLOWED_VIEWS,
            )
        assert list(await session.scalars(select(SavedRequest))) == []

        request = await create_saved_request(
            session,
            "Nothing yet",
            "SELECT 'card' AS item_type, c.id FROM ai_cards c WHERE c.kind = 'goal'",
            views=ALLOWED_VIEWS,
        )
        await session.commit()
        assert await request_items(session, request.query_sql, ALLOWED_VIEWS) == []


async def test_a_stored_statement_is_checked_again_before_it_runs(sessions):
    """SR-SQL-005 — tests/brd/saved_requests.feature"""
    async with sessions() as session:
        await (await session.connection()).run_sync(
            lambda connection: create_ai_views(connection, AI_VIEWS)
        )
        request = await create_saved_request(
            session, "All goals", "SELECT 'card' AS item_type, id FROM ai_cards WHERE kind = 'goal'", views=ALLOWED_VIEWS
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
            await request_items(session, stored.query_sql, ALLOWED_VIEWS)


async def test_a_request_name_is_taken_whatever_its_case(sessions):
    """SR-WRITE-002 — tests/brd/saved_requests.feature"""
    async with sessions() as session:
        await create_saved_request(
            session, "All goals", "SELECT 'card' AS item_type, id FROM ai_cards WHERE kind = 'goal'", views=ALLOWED_VIEWS
        )
        other = await create_saved_request(
            session, "Open actions", "SELECT 'card' AS item_type, id FROM ai_cards WHERE kind = 'action'",
            views=ALLOWED_VIEWS,
        )
        await session.commit()

        with pytest.raises(DomainError, match="already exists"):
            await update_saved_request(session, other.id, name="ALL GOALS", views=ALLOWED_VIEWS)
        with pytest.raises(DomainError, match="cannot be empty"):
            await update_saved_request(session, other.id, name="   ", views=ALLOWED_VIEWS)
        with pytest.raises(DomainError, match="cannot be empty"):
            await create_saved_request(
                session, "  ", "SELECT 'card' AS item_type, id FROM ai_cards", views=ALLOWED_VIEWS
            )


def test_a_request_query_is_never_allowlisted_for_autoapproval():
    """SR-AI-010 — tests/brd/saved_requests.feature"""

    def change(action: str, values: dict[str, object]) -> ProposalChange:
        return ProposalChange(
            entity="request", action=ChangeAction(action), entity_id=7, values=values
        )

    assert PROPOSALS.criterion(change("update", {"name": "Every goal"})) is not None
    # `prepare` stores the normalized statement as `query_sql`, so re-aiming a Request never
    # matches the allowlisted field set and never reaches the reviewer at all.
    assert PROPOSALS.criterion(change("update", {"query_sql": "SELECT 'card' AS item_type, id FROM ai_cards"})) is None
    assert PROPOSALS.criterion(change("update", {"name": "X", "query_sql": "SELECT id"})) is None
    assert PROPOSALS.criterion(change("create", {"name": "X"})) is None


async def test_a_request_is_deleted_not_archived(sessions):
    """SR-DELETE-013 — tests/brd/saved_requests.feature"""
    async with sessions() as session:
        request = await create_saved_request(
            session,
            "All goals",
            "SELECT 'card' AS item_type, id FROM ai_cards WHERE kind = 'goal'",
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
            session, "all GOALS", "SELECT 'card' AS item_type, id FROM ai_cards", views=ALLOWED_VIEWS
        )
        await session.commit()
        assert again.description == ""
        assert len(list(await session.scalars(select(SavedRequest)))) == 1


def test_the_remove_tool_refuses_to_archive_a_request():
    """SR-DELETE-013 — tests/brd/saved_requests.feature"""
    assert RemoveToolInput(mode="delete", entity="request", id=1).entity == "request"
    with pytest.raises(ValidationError, match="a request is deleted, never archived"):
        RemoveToolInput(mode="archive", entity="request", id=1)


async def test_the_inbox_selection_matches_the_exact_tag_in_creation_order(sessions):
    """SR-INBOX-014 — tests/brd/saved_requests.feature"""
    async with sessions() as session:
        await seed_inbox_tag(session)
        await seed_inbox_request(session, views=ALLOWED_VIEWS)
        await seed_inbox_request(session, views=ALLOWED_VIEWS)
        inbox = await session.scalar(select(Tag).where(Tag.name == INBOX_TAG_NAME))
        similar = await create_tag(session, "Inbox другое")
        other = await create_tag(session, "Другой")
        cards = []
        for title in ("Z first", "A second", "Similar Tag", "No Tag"):
            cards.append(await create_card(session, kind="action", title=title))
        for card, tag in ((cards[0], inbox), (cards[1], other), (cards[1], inbox), (cards[2], similar)):
            await toggle_card_tag(session, card.id, tag.id)
        await session.commit()

        request = await session.scalar(select(SavedRequest))
        assert len(list(await session.scalars(select(SavedRequest)))) == 1
        assert [card.id for card in await request_items(session, request.query_sql, ALLOWED_VIEWS)] == [
            cards[0].id, cards[1].id
        ]
        await toggle_card_tag(session, cards[0].id, inbox.id)
        await session.commit()
        assert [card.id for card in await request_items(session, request.query_sql, ALLOWED_VIEWS)] == [
            cards[1].id
        ]


async def test_a_mixed_request_keeps_types_order_and_unique_references(sessions):
    """SR-RUN-006 — tests/brd/saved_requests.feature"""
    async with sessions() as session:
        card = await create_card(session, kind="action", title="Walk")
        check = await create_check(session, title="Slept enough?")
        assert card.id == check.id
        check.archived_at = check.created_at
        request = await create_saved_request(
            session, "Mixed",
            "SELECT 'check' AS item_type, id, 0 AS position FROM ai_checks "
            "UNION ALL SELECT 'card', id, 1 FROM ai_cards "
            "UNION ALL SELECT 'check', id, 2 FROM ai_checks "
            "UNION ALL SELECT 'check', id + 1000, 3 FROM ai_checks "
            "ORDER BY position",
            views=ALLOWED_VIEWS,
        )
        await session.commit()
        matches = await request_items(session, request.query_sql, ALLOWED_VIEWS)
        assert [(type(item), item.id) for item in matches] == [
            (Check, check.id), (Card, card.id)
        ]
        assert await title_marks(session, matches[0]) == " [📦]"
        await delete_check(session, check.id)
        await session.commit()
        assert await request_items(session, request.query_sql, ALLOWED_VIEWS) == [card]


async def test_a_check_only_request_is_valid_on_an_empty_workspace(sessions):
    """SR-SQL-004 — tests/brd/saved_requests.feature"""
    async with sessions() as session:
        request = await create_saved_request(
            session, "Pending",
            "SELECT 'check' AS item_type, id FROM ai_checks WHERE status = 'pending'",
            views=ALLOWED_VIEWS,
        )
        assert await request_items(session, request.query_sql, ALLOWED_VIEWS) == []
        check = await create_check(session, title="Enough water?")
        assert await request_items(session, request.query_sql, ALLOWED_VIEWS) == [check]


@pytest.mark.parametrize("projection", [
    "'value' AS item_type, id",
    "NULL AS item_type, id",
    "'check' AS item_type, NULL AS id",
    "'check' AS item_type, 1.5 AS id",
    "'check' AS item_type, '1' AS id",
])
async def test_invalid_stored_references_cannot_open_another_item(sessions, projection):
    """SR-SQL-005 — tests/brd/saved_requests.feature"""
    async with sessions() as session:
        await create_check(session, title="Observed?")
        request = SavedRequest(
            name="Invalid", query_sql=f"SELECT {projection} FROM ai_checks", description=""
        )
        session.add(request)
        await session.flush()
        with pytest.raises(RequestQueryError):
            await request_items(session, request.query_sql, ALLOWED_VIEWS)


@pytest.mark.parametrize("projection", [
    "'value' AS item_type, id",
    "'check' AS item_type, 'wrong' AS id",
])
async def test_invalid_references_are_refused_before_a_request_is_saved(sessions, projection):
    """SR-SQL-004 — tests/brd/saved_requests.feature"""
    async with sessions() as session:
        await create_check(session, title="Observed?")
        with pytest.raises(RequestQueryError):
            await create_saved_request(
                session, "Invalid", f"SELECT {projection} FROM ai_checks", views=ALLOWED_VIEWS
            )
        assert list(await session.scalars(select(SavedRequest))) == []
