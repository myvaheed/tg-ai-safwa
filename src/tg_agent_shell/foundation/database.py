"""The SQLite engine an application runs on, and how its schema is brought up.

The ORM model modules are the only schema source: `create_all` adds missing tables and
indexes and never alters an existing one, so a changed column needs a rebuilt file rather
than a migration. Whether an application may rebuild its database instead of migrating it
is the application's own decision, written where that application's rules are.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from pathlib import Path

from sqlalchemy import Connection, MetaData, create_engine, event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .models import Base

_in_transaction: ContextVar[bool] = ContextVar("shell_in_transaction", default=False)


def create_schema(connection: Connection, *metadata: MetaData) -> None:
    """The shell's own tables, then the application's, on one connection.

    An application declares its tables on a `Base` of its own and hands its metadata here,
    so two applications in one process each create their own and neither reaches the
    other's. Nothing is altered: `create_all` adds what is missing and leaves the rest.
    """
    for declared in (Base.metadata, *metadata):
        declared.create_all(connection)


def upgrade_database(database_url: str, *metadata: MetaData) -> None:
    """Bring the database up to the ORM model modules, which are the only schema source."""
    if database_url.startswith("sqlite:///"):
        Path(database_url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            create_schema(connection, *metadata)
    finally:
        engine.dispose()


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

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncSession]:
        """One business transaction: the block commits on success and discards on failure.

        `sessions()` stays for reads and for callers that commit in steps; this is the
        boundary a use case opens when its whole body has to land or not land at all.

        Opening one inside another is refused rather than joined.  Joining would commit
        the inner work with the outer block and leave a caught inner failure sitting in a
        dirty session, because a joined block has no savepoint to roll back to.  An
        operation a use case has to call takes the session instead of opening its own.
        """
        if _in_transaction.get():
            raise RuntimeError(
                "A transaction is already open here. Call the operation with this "
                "session instead of opening a second transaction."
            )
        token = _in_transaction.set(True)
        try:
            async with self.sessions() as session:
                yield session
                await session.commit()
        finally:
            _in_transaction.reset(token)
