from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine, inspect, select

from safwa.ai.sql import ReadOnlyQueryRunner, create_ai_views
from safwa.db import upgrade_database
from safwa.models import AgentRun, AgentStep, Base, Card, CardTag, DiaryStamp, Tag
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


async def test_startup_releases_an_interrupted_agent_continuation(sessions):
    async with sessions() as session:
        run = AgentRun(provider="test", model="test", status="running")
        session.add(run)
        await session.flush()
        session.add_all(
            [
                AgentStep(
                    run_id=run.id,
                    position=1,
                    kind="approval_batch",
                    metadata_json={"status": "resuming", "queue": []},
                ),
                AgentStep(
                    run_id=run.id,
                    position=2,
                    kind="approval_batch",
                    metadata_json={"status": "pending", "queue": []},
                ),
            ]
        )
        await session.commit()

        await recover_startup(session)
        await session.commit()

        steps = list(
            await session.scalars(select(AgentStep).order_by(AgentStep.position))
        )
    # A resuming batch already had its whole queue resolved, so it is closed rather
    # than left claiming its proposal forever.  A pending batch still owns live UI.
    assert steps[0].metadata_json["status"] == "completed"
    assert steps[0].metadata_json["continuation_error"] == "InterruptedAtStartup"
    assert steps[1].metadata_json["status"] == "pending"


async def test_startup_sweeps_diary_stamps_whose_day_is_over(sessions):
    today = date(2026, 8, 15)
    async with sessions() as session:
        session.add_all(
            [
                DiaryStamp(
                    stamp="yesterday",
                    entry_date=today - timedelta(days=1),
                    body="Old.",
                    expires_at=datetime.now(UTC) - timedelta(hours=1),
                ),
                DiaryStamp(
                    stamp="today",
                    entry_date=today,
                    body="Current.",
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                ),
            ]
        )
        await session.commit()

        await recover_startup(session)
        await session.commit()

        remaining = list(await session.scalars(select(DiaryStamp.stamp)))
    assert remaining == ["today"]


async def test_read_only_query_runner_reads_only_ai_views(tmp_path):
    path = tmp_path / "query.db"
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        create_ai_views(connection)
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
    runner = ReadOnlyQueryRunner(path)
    cards = await runner.run("SELECT title FROM ai_cards")
    assert cards.rows == [{"title": "Read"}]
    assert cards.notice is None
    assert cards.as_tool_result() == [{"title": "Read"}]
    assert (await runner.run("SELECT name FROM ai_tags")).rows == [{"name": "Family"}]
    assert (await runner.run("SELECT name FROM ai_requests")).rows == [{"name": "Family actions"}]
    engine.dispose()


async def test_durable_entities_use_incrementing_integer_ids(sessions):
    async with sessions() as session:
        first = Card(kind="action", title="First", effort_points=1)
        second = Card(kind="action", title="Second", effort_points=1)
        first_tag = Tag(name="First tag")
        second_tag = Tag(name="Second tag")
        session.add_all([first, second, first_tag, second_tag])
        await session.flush()
        session.add(CardTag(card_id=first.id, tag_id=first_tag.id))
        await session.commit()

    assert isinstance(first.id, int)
    assert second.id == first.id + 1
    assert isinstance(first_tag.id, int)
    assert second_tag.id == first_tag.id + 1
