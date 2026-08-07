from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from alembic import command


def upgrade_database(database_url: str) -> None:
    if database_url.startswith("sqlite:///"):
        Path(database_url.removeprefix("sqlite:/// ".strip())).parent.mkdir(
            parents=True, exist_ok=True
        )
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")


class Database:
    def __init__(self, url: str) -> None:
        self.engine: AsyncEngine = create_async_engine(url, pool_pre_ping=True)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

        @event.listens_for(self.engine.sync_engine, "connect")
        def configure_sqlite(connection, _record) -> None:  # type: ignore[no-untyped-def]
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

    async def dispose(self) -> None:
        await self.engine.dispose()
