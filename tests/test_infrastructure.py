from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine

from safwa.ai.sql import ReadOnlyQueryRunner, create_ai_views
from safwa.db import upgrade_database
from safwa.models import Base, Card, CardTag, Tag


def test_alembic_bootstraps_new_database(tmp_path, monkeypatch):
    monkeypatch.chdir(Path(__file__).parents[1])
    path = tmp_path / "safwa.db"
    upgrade_database(f"sqlite:///{path.as_posix()}")
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    assert set(Base.metadata.tables).issubset(set(engine.dialect.get_table_names(engine.connect())))
    engine.dispose()


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
            "hard_time,repeatable,version,created_at,updated_at) "
            "VALUES (2,'action','Read','Book','backlog','backlog','medium',0,0,1,"
            "CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        )
        connection.exec_driver_sql("INSERT INTO card_tags(card_id,tag_id) VALUES (2,1)")
        connection.exec_driver_sql(
            "INSERT INTO saved_requests(id,name,description,filter_spec,version,created_at,updated_at) "
            'VALUES (3,\'Family actions\',\'\', \'{"all":[{"field":"tag_id","op":"any_of","value":[1]}]}\',1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)'
        )
    runner = ReadOnlyQueryRunner(path)
    assert await runner.run("SELECT title FROM ai_cards") == [{"title": "Read"}]
    assert await runner.run("SELECT name FROM ai_tags") == [{"name": "Family"}]
    assert await runner.run("SELECT name FROM ai_requests") == [{"name": "Family actions"}]
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
