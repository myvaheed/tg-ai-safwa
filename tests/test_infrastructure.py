from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect

from safwa.bootstrap.modules import AI_VIEWS, ALLOWED_VIEWS
from safwa.foundation.models import Base
from tg_agent_shell.ai.runs import AgentRun
from tg_agent_shell.ai.sql import ReadOnlyQueryRunner, create_ai_views
from tg_agent_shell.foundation.database import upgrade_database
from tg_agent_shell.foundation.poll import run_poll
from tg_agent_shell.recovery import recover_startup


async def test_ag_poll_030_a_poll_outlives_a_failing_round_and_ends_on_shutdown():
    """AG-POLL-030 — tests/brd/tg_agent_shell/agents.feature"""
    rounds = {"count": 0}

    async def exploding() -> None:
        rounds["count"] += 1
        raise RuntimeError("boom")

    task = asyncio.create_task(run_poll(exploding, poll_seconds=0.01, name="The test poll"))
    for _ in range(200):
        await asyncio.sleep(0.01)
        if rounds["count"] >= 2:
            break

    assert rounds["count"] >= 2
    # Shutdown still ends it: `CancelledError` is not an `Exception`.
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_startup_bootstraps_a_new_database_from_the_models(tmp_path, monkeypatch):
    monkeypatch.chdir(Path(__file__).parents[1])
    path = tmp_path / "safwa.db"
    upgrade_database(f"sqlite:///{path.as_posix()}", Base.metadata)
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    assert set(Base.metadata.tables).issubset(set(engine.dialect.get_table_names(engine.connect())))
    indexes = {index["name"] for index in inspect(engine).get_indexes("agent_steps")}
    assert "ix_agent_steps_kind" in indexes
    engine.dispose()


async def test_startup_ends_the_session_a_crash_left_running(sessions):
    """AG-SESSION-009 — tests/brd/tg_agent_shell/agents.feature"""
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
    """AG-SESSION-009 — tests/brd/tg_agent_shell/agents.feature"""
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
