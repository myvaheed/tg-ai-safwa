"""The one-off Request conversion on real encrypted workspace databases."""

from __future__ import annotations

import sys

import pytest
from database_key import held_elsewhere, keyed_copy
from sqlalchemy import select

from safwa.bootstrap.modules import ALLOWED_VIEWS
from safwa.features.cards.use_cases import create_card
from safwa.features.checks.use_cases import create_check
from safwa.features.saved_requests.api import explicit_order, request_items
from safwa.features.saved_requests.model import SavedRequest
from safwa.features.saved_requests.use_cases import create_saved_request
from scripts import upgrade_saved_requests as upgrade
from tg_agent_shell.foundation.errors import DomainError


@pytest.mark.parametrize("sql, expected", [
    ("SELECT id FROM ai_cards", [1, 2, 3]),
    ("SELECT id FROM ai_cards ORDER BY title LIMIT 2 OFFSET 1", [3, 1]),
    ("WITH chosen AS (SELECT id, title FROM ai_cards) SELECT id FROM chosen ORDER BY title", [2, 3, 1]),
    ("SELECT id FROM ai_cards UNION ALL SELECT id FROM ai_cards ORDER BY id DESC", [3, 2, 1]),
    ("SELECT CAST(id AS TEXT) AS id FROM ai_cards ORDER BY id DESC", [3, 2, 1]),
    ("SELECT id, title AS position FROM ai_cards ORDER BY 2", [2, 3, 1]),
    ("SELECT id, 'ORDER BY title' AS note FROM ai_cards", [1, 2, 3]),
    ("SELECT id FROM ai_cards WHERE id < 0", []),
])
async def test_upgrade_preserves_selection_order_and_request_identity(sessions, sql, expected):
    """SR-RUN-006 — tests/brd/saved_requests.feature"""
    async with sessions() as session:
        for title in ("Z", "A", "M"):
            await create_card(session, kind="action", title=title)
        legacy = SavedRequest(name="Legacy", description="Keep this.", query_sql=sql)
        session.add(legacy)
        await session.commit()
        request_id = legacy.id

    preview = await upgrade.upgrade_requests(sessions)
    assert [(item_id, name) for item_id, name, _sql in preview] == [(request_id, "Legacy")]
    async with sessions() as session:
        unchanged = await session.get(SavedRequest, request_id)
        assert unchanged.query_sql == sql and unchanged.version == 1

    assert await upgrade.upgrade_requests(sessions, apply=True) == preview
    async with sessions() as session:
        converted = await session.get(SavedRequest, request_id)
        assert converted.name == "Legacy" and converted.description == "Keep this."
        assert converted.version == 2
        assert explicit_order(converted.query_sql) == explicit_order(sql)
        assert [item.id for item in await request_items(session, converted.query_sql, ALLOWED_VIEWS)] == expected
        if explicit_order(sql):
            new_card = await create_card(session, kind="action", title="Aa")
            await session.commit()
            if sql.endswith("LIMIT 2 OFFSET 1"):
                fresh_expected = [new_card.id, 3]
            elif "DESC" in sql:
                fresh_expected = [new_card.id, 3, 2, 1]
            else:
                fresh_expected = [2, new_card.id, 3, 1]
            assert [
                item.id for item in await request_items(session, converted.query_sql, ALLOWED_VIEWS)
            ] == fresh_expected
    assert await upgrade.upgrade_requests(sessions, apply=True) == []


async def test_upgrade_leaves_typed_requests_unchanged(sessions):
    """SR-RUN-006 — tests/brd/saved_requests.feature"""
    async with sessions() as session:
        await create_card(session, kind="action", title="Walk")
        await create_check(session, title="Enough sleep?")
        request = await create_saved_request(
            session, "Mixed",
            "SELECT 'card' AS item_type, id FROM ai_cards UNION ALL SELECT 'check', id FROM ai_checks",
            views=ALLOWED_VIEWS,
        )
        await session.commit()
        request_id, sql = request.id, request.query_sql
    assert await upgrade.upgrade_requests(sessions, apply=True) == []
    async with sessions() as session:
        request = await session.get(SavedRequest, request_id)
        assert request.query_sql == sql and request.version == 1
        assert len(await request_items(session, request.query_sql, ALLOWED_VIEWS)) == 2


async def test_upgrade_commits_nothing_if_one_request_is_invalid(sessions):
    """SR-SQL-005 — tests/brd/saved_requests.feature"""
    async with sessions() as session:
        await create_card(session, kind="action", title="Walk")
        session.add_all([
            SavedRequest(name="Valid", query_sql="SELECT id FROM ai_cards"),
            SavedRequest(name="Invalid", query_sql="SELECT id FROM cards"),
        ])
        await session.commit()
    with pytest.raises(DomainError, match="Invalid"):
        await upgrade.upgrade_requests(sessions, apply=True)
    async with sessions() as session:
        rows = list(await session.scalars(select(SavedRequest).order_by(SavedRequest.id)))
        assert [row.query_sql for row in rows] == ["SELECT id FROM ai_cards", "SELECT id FROM cards"]
        assert [row.version for row in rows] == [1, 1]


async def test_upgrade_rolls_back_earlier_writes_when_a_later_write_fails(sessions, monkeypatch):
    """SR-SQL-005 — tests/brd/saved_requests.feature"""
    async with sessions() as session:
        session.add_all([
            SavedRequest(name="First", query_sql="SELECT id FROM ai_cards"),
            SavedRequest(name="Second", query_sql="SELECT id FROM ai_cards"),
        ])
        await session.commit()
    update = upgrade.update_saved_request

    async def fail_second(session, request_id, **kwargs):
        request = await session.get(SavedRequest, request_id)
        if request.name == "Second":
            raise DomainError("Second write failed")
        return await update(session, request_id, **kwargs)

    monkeypatch.setattr(upgrade, "update_saved_request", fail_second)
    with pytest.raises(DomainError, match="Second write failed"):
        await upgrade.upgrade_requests(sessions, apply=True)
    async with sessions() as session:
        rows = list(await session.scalars(select(SavedRequest)))
        assert all(row.query_sql == "SELECT id FROM ai_cards" and row.version == 1 for row in rows)


def test_upgrade_refuses_a_running_bot_before_asking_for_the_passphrase(
    tmp_path, workspace_file, monkeypatch
):
    file = keyed_copy(workspace_file, tmp_path / "safwa.db")
    monkeypatch.setattr(sys, "argv", ["upgrade_saved_requests.py", "--database", str(file.path), "--apply"])

    def no_prompt(_path):
        pytest.fail("A locked database must be refused before unlocking")

    monkeypatch.setattr(upgrade, "unlock", no_prompt)
    with held_elsewhere(file.path), pytest.raises(SystemExit, match="Safwa is running"):
        upgrade.main()


def test_upgrade_refuses_a_missing_database_without_creating_it(tmp_path, monkeypatch):
    missing = tmp_path / "missing.db"
    monkeypatch.setattr(sys, "argv", ["upgrade_saved_requests.py", "--database", str(missing)])
    with pytest.raises(SystemExit):
        upgrade.main()
    assert not missing.exists()
