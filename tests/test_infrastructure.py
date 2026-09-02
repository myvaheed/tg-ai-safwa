from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, select

from safwa.ai.runs import AgentRun
from safwa.ai.sql import ReadOnlyQueryRunner, create_ai_views
from safwa.bootstrap.modules import AI_VIEWS, ALLOWED_VIEWS
from safwa.features.tags.model import Tag
from safwa.foundation.database import Database, upgrade_database
from safwa.foundation.models import Base
from safwa.recovery import recover_startup


def test_startup_bootstraps_a_new_database_from_the_models(tmp_path, monkeypatch):
    monkeypatch.chdir(Path(__file__).parents[1])
    path = tmp_path / "safwa.db"
    upgrade_database(f"sqlite:///{path.as_posix()}")
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    assert set(Base.metadata.tables).issubset(set(engine.dialect.get_table_names(engine.connect())))
    indexes = {index["name"] for index in inspect(engine).get_indexes("agent_steps")}
    assert "ix_agent_steps_kind" in indexes
    engine.dispose()


async def test_startup_ends_the_session_a_crash_left_running(sessions):
    """AG-SESSION-009 — tests/brd/agents.feature"""
    async with sessions() as session:
        run = AgentRun(
            provider="test",
            model="test",
            status="running",
            claimed_at=datetime.now(UTC),
        )
        session.add(run)
        await session.commit()

        await recover_startup(session)
        await session.commit()

        restored = await session.get(AgentRun, run.id)
    # Nothing can pick it up: only the turn that routed to a session adopts it, and that
    # turn died with the process.  The claim goes with it so no row is left held forever.
    assert restored.status == "abandoned"
    assert restored.claimed_at is None


async def test_startup_closes_every_session_waiting_on_a_process_local_screen(sessions):
    """AG-SESSION-009 — tests/brd/agents.feature"""
    async with sessions() as session:
        waiting = AgentRun(provider="test", model="test", status="awaiting_approval")
        session.add(waiting)
        await session.commit()

        await recover_startup(session)
        await session.commit()

        # The screens that session was waiting on went with the process that opened them.
        assert (await session.get(AgentRun, waiting.id)).status == "abandoned"


async def test_read_only_query_runner_reads_only_ai_views(tmp_path):
    path = tmp_path / "query.db"
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        create_ai_views(connection, AI_VIEWS)
        connection.exec_driver_sql(
            "INSERT INTO tags(id,name,description,version,created_at,updated_at) "
            "VALUES (1,'Family','',1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        )
        connection.exec_driver_sql(
            "INSERT INTO cards(id,kind,title,note,manual_stage,effective_stage,priority,"
            "hard_time,repeatable,blocked,blocked_description,version,created_at,updated_at) "
            "VALUES (2,'action','Read','Book','backlog','backlog','medium',0,0,0,'',1,"
            "CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        )
        connection.exec_driver_sql("INSERT INTO card_tags(card_id,tag_id) VALUES (2,1)")
        connection.exec_driver_sql(
            "INSERT INTO saved_requests(id,name,description,query_sql,version,created_at,updated_at) "
            "VALUES (3,'Family actions','','SELECT id FROM ai_cards',1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        )
    runner = ReadOnlyQueryRunner(path, ALLOWED_VIEWS)
    cards = await runner.run("SELECT title FROM ai_cards")
    assert cards.rows == [{"title": "Read"}]
    assert cards.notice is None
    assert cards.as_tool_result() == [{"title": "Read"}]
    assert (await runner.run("SELECT name FROM ai_tags")).rows == [{"name": "Family"}]
    assert (await runner.run("SELECT name FROM ai_requests")).rows == [{"name": "Family actions"}]
    engine.dispose()


async def test_transaction_commits_the_whole_block(tmp_path):
    url = f"sqlite+aiosqlite:///{(tmp_path / 'tx.db').as_posix()}"
    upgrade_database(url.replace("sqlite+aiosqlite:", "sqlite:"))
    database = Database(url)
    try:
        async with database.transaction() as session:
            session.add(Tag(name="Family"))

        async with database.sessions() as session:
            assert await session.scalar(select(Tag.name)) == "Family"
    finally:
        await database.dispose()


async def test_transaction_discards_the_whole_block_when_the_body_raises(tmp_path):
    url = f"sqlite+aiosqlite:///{(tmp_path / 'tx.db').as_posix()}"
    upgrade_database(url.replace("sqlite+aiosqlite:", "sqlite:"))
    database = Database(url)
    try:
        with pytest.raises(RuntimeError):
            async with database.transaction() as session:
                session.add(Tag(name="Family"))
                await session.flush()
                raise RuntimeError("half a use case is not a use case")

        async with database.sessions() as session:
            assert await session.scalar(select(Tag.name)) is None
    finally:
        await database.dispose()


async def test_a_second_transaction_inside_one_is_refused_rather_than_joined(tmp_path):
    """Joining would commit the inner work with the outer block and leave a caught inner
    failure in a dirty session. An operation the caller composes takes the session."""
    url = f"sqlite+aiosqlite:///{(tmp_path / 'tx.db').as_posix()}"
    upgrade_database(url.replace("sqlite+aiosqlite:", "sqlite:"))
    database = Database(url)
    try:
        with pytest.raises(RuntimeError, match="already open"):
            async with database.transaction():
                async with database.transaction():
                    pass

        # The refusal releases the boundary rather than wedging it shut.
        async with database.transaction() as session:
            session.add(Tag(name="Family"))
        async with database.sessions() as session:
            assert await session.scalar(select(Tag.name)) == "Family"
    finally:
        await database.dispose()
