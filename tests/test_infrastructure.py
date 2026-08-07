from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine

from safwa.ai.sql import ReadOnlyQueryRunner, create_ai_views
from safwa.db import upgrade_database
from safwa.models import Base


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
            "INSERT INTO boards(id,name,description,version,created_at,updated_at) "
            "VALUES ('b','Inbox','',1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        )
        connection.exec_driver_sql(
            "INSERT INTO cards(id,board_id,kind,title,note,manual_stage,effective_stage,priority,"
            "hard_time,repeatable,version,created_at,updated_at) "
            "VALUES ('c','b','action','Read','Book','backlog','backlog','medium',0,0,1,"
            "CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        )
    runner = ReadOnlyQueryRunner(path)
    assert await runner.run("SELECT title FROM ai_cards") == [{"title": "Read"}]
    engine.dispose()
