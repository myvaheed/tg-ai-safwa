from __future__ import annotations

import pytest
from sqlalchemy import select, text

from safwa.bootstrap.modules import AI_VIEWS, ALLOWED_VIEWS, PROPOSALS
from safwa.features.cards.model import Card
from safwa.features.cards.use_cases import (
    archive_subtree,
    create_card,
    finish_action,
    finish_card,
    toggle_card_value,
)
from safwa.features.checks.model import Check
from safwa.features.checks.use_cases import create_check, delete_check, toggle_check_value
from safwa.features.saved_requests.api import RequestQueryError, request_items
from safwa.features.saved_requests.model import SavedRequest
from safwa.features.saved_requests.use_cases import (
    create_saved_request,
    delete_saved_request,
    seed_default_requests,
    update_saved_request,
)
from safwa.features.tags.model import CardTag, Tag
from safwa.features.values.model import Value
from safwa.features.values.use_cases import create_value, delete_value
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

        before = (other.name, other.version)
        with pytest.raises(DomainError, match="already exists"):
            await update_saved_request(session, other.id, name="ALL GOALS", views=ALLOWED_VIEWS)
        assert (other.name, other.version) == before
        with pytest.raises(DomainError, match="cannot be empty"):
            await update_saved_request(session, other.id, name="   ", views=ALLOWED_VIEWS)
        assert (other.name, other.version) == before
        with pytest.raises(DomainError, match="cannot be empty"):
            await create_saved_request(
                session, "  ", "SELECT 'card' AS item_type, id FROM ai_cards", views=ALLOWED_VIEWS
            )
        assert list(await session.scalars(select(SavedRequest.name).order_by(SavedRequest.id))) == [
            "All goals", "Open actions",
        ]

        await update_saved_request(session, other.id, name="Today actions", views=ALLOWED_VIEWS)
        assert (other.name, other.version) == ("Today actions", before[1] + 1)


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
    """SR-DELETE-015 — tests/brd/saved_requests.feature"""
    async with sessions() as session:
        request = await create_saved_request(
            session,
            "All goals",
            "SELECT 'card' AS item_type, id FROM ai_cards WHERE kind = 'goal'",
            "Original description",
            views=ALLOWED_VIEWS,
        )
        await session.commit()

        remove = RemoveToolInput(mode="delete", entity="request", id=request.id)
        assert remove.entity == "request"
        await delete_saved_request(session, remove.id)
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


async def test_default_requests_select_missing_direct_relationships(read_views):
    """SR-UI-013 — tests/brd/saved_requests.feature"""
    sessions, runner = read_views
    async with sessions() as session:
        requests = await seed_default_requests(session, views=ALLOWED_VIEWS)
        # Similar names and commas must not turn a partial name match into a relationship.
        linked = await create_value(session, "Health, fitness", "Stay healthy.")
        unused = await create_value(session, "Health", "Stay well.")
        action_only = await create_value(session, "Work", "Do useful work.")
        check_only = await create_value(session, "Rest", "Recover.")
        leaf = await create_card(session, kind="goal", title="No links")
        parent = await create_card(session, kind="goal", title="Parent")
        await toggle_card_value(session, parent.id, linked.id)
        child = await create_card(session, kind="goal", title="Subgoal", parent_ids={parent.id})
        action = await create_card(session, kind="action", title="Step", parent_ids={parent.id, child.id})
        await toggle_card_value(session, action.id, action_only.id)
        check = await create_check(session, title="Rested?")
        await toggle_check_value(session, check.id, check_only.id)
        # A finished or archived Action is still a child.
        await finish_action(session, action.id)
        await archive_subtree(session, action.id)
        await create_card(session, kind="thought", title="Not a Goal")
        await session.commit()
        results = {
            request.name: await request_items(session, request.query_sql, ALLOWED_VIEWS)
            for request in requests
        }
        assert results == {
            "Goals without children": [leaf],
            "Goals without Values": [leaf, child],
            "Values without Goals": [unused, action_only, check_only],
        }
        for request in requests:
            rows = (await runner.run(request.query_sql)).rows
            assert [row["id"] for row in rows] == [item.id for item in results[request.name]]
        rows = (await runner.run("SELECT * FROM ai_values ORDER BY id")).rows
        assert [row["card_ids"] for row in rows] == [str(parent.id), None, str(action.id), None]


@pytest.mark.parametrize("archived", [False, True])
async def test_default_requests_ignore_done_and_archived_goals(read_views, archived):
    """SR-UI-013 — tests/brd/saved_requests.feature"""
    sessions, runner = read_views
    async with sessions() as session:
        requests = await seed_default_requests(session, views=ALLOWED_VIEWS)
        closed_value = await create_value(session, "Closed work", "Only closed Goals serve it.")
        live_value = await create_value(session, "Live work", "An open Goal serves it.")
        parent = await create_card(session, kind="goal", title="Only a closed child Goal")
        child = await create_card(session, kind="goal", title="Closed child", parent_ids={parent.id})
        await toggle_card_value(session, child.id, closed_value.id)
        action = await create_card(session, kind="action", title="Finished step", parent_ids={child.id})
        await finish_action(session, action.id)
        await finish_card(session, child.id)
        if archived:
            await archive_subtree(session, child.id)
            assert child.archived_at is not None
        leaf = await create_card(session, kind="goal", title="Closed leaf without Values")
        await finish_card(session, leaf.id)
        live = await create_card(session, kind="goal", title="Open leaf with a Value")
        await toggle_card_value(session, live.id, live_value.id)
        await session.commit()
        expected = {
            "Goals without children": [parent, live],
            "Goals without Values": [parent],
            "Values without Goals": [closed_value],
        }
        for request in requests:
            assert await request_items(session, request.query_sql, ALLOWED_VIEWS) == expected[request.name]
            assert [row["id"] for row in (await runner.run(request.query_sql)).rows] == [
                item.id for item in expected[request.name]
            ]


async def test_a_mixed_request_keeps_types_order_and_unique_references(sessions):
    """SR-RUN-006 — tests/brd/saved_requests.feature"""
    async with sessions() as session:
        card = await create_card(session, kind="action", title="Walk")
        check = await create_check(session, title="Slept enough?")
        value = await create_value(session, "Health", "Stay healthy.")
        assert card.id == check.id == value.id
        check.archived_at = check.created_at
        request = await create_saved_request(
            session, "Mixed",
            "SELECT 'check' AS item_type, id, 0 AS position FROM ai_checks "
            "UNION ALL SELECT 'card', id, 1 FROM ai_cards "
            "UNION ALL SELECT 'check', id, 2 FROM ai_checks "
            "UNION ALL SELECT 'check', id + 1000, 3 FROM ai_checks "
            "UNION ALL SELECT 'value', id, 4 FROM ai_values "
            "UNION ALL SELECT 'value', id, 5 FROM ai_values "
            "UNION ALL SELECT 'value', id + 1000, 6 FROM ai_values "
            "UNION ALL SELECT 'card', id, 7 FROM ai_cards "
            "UNION ALL SELECT 'card', id + 1000, 8 FROM ai_cards "
            "ORDER BY position",
            views=ALLOWED_VIEWS,
        )
        await session.commit()

    async with sessions() as session:
        stored = await session.get(SavedRequest, request.id)
        assert stored is not None
        matches = await request_items(session, stored.query_sql, ALLOWED_VIEWS)
        assert [(type(item), item.id) for item in matches] == [
            (Check, check.id), (Card, card.id), (Value, value.id)
        ]
        assert await title_marks(session, matches[0]) == " [📦]"
        await delete_check(session, check.id)
        await delete_value(session, value.id)
        await session.commit()
        assert [
            (type(item), item.id)
            for item in await request_items(session, stored.query_sql, ALLOWED_VIEWS)
        ] == [(Card, card.id)]


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
    "'tag' AS item_type, id",
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
    "'tag' AS item_type, id",
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
