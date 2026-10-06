"""The encrypted SQLite file an application runs on, the one place it is opened, and its schema.

Every connection — the async engine behind `Database`, the sync engine `upgrade_database`
uses, the read-only runner behind `query_data`, a backup — is opened by `DatabaseFile.connect`,
so there is one place that sets the key and refuses a file the key does not open. The driver
is SQLCipher's, imported here alone: a module that needs its names (`Row`, `Error`, the
authorizer codes) takes them as `driver` from here, never from the standard `sqlite3`, whose
error classes the driver does not raise.

The ORM model modules are the only schema source: `create_all` adds missing tables and
indexes and never alters an existing one, so a changed column needs a rebuilt file rather
than a migration. Whether an application may rebuild its database instead of migrating it
is the application's own decision, written where that application's rules are.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from threading import Thread

import aiosqlite
import sqlcipher3.dbapi2 as driver
from sqlalchemy import Connection, Engine, MetaData, create_engine
from sqlalchemy.dialects.sqlite.aiosqlite import AsyncAdapt_aiosqlite_dbapi
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from .models import Base

KEY_BYTES = 32
BUSY_TIMEOUT_SECONDS = 5
# aiosqlite's own default for how many rows a cursor hands over per trip to its thread.
ITER_CHUNK_SIZE = 64


def _unicode_nocase(left: str, right: str) -> int:
    left, right = left.casefold(), right.casefold()
    return (left > right) - (left < right)


def _no_relevance(item_type: str, item_id: int) -> None:
    return None


class DatabaseRefused(Exception):
    """The file was not opened, because nothing proves it is encrypted with this key."""


class DatabaseFile:
    """One encrypted database file and the key that opens it."""

    def __init__(self, path: Path, key: bytes) -> None:
        if len(key) != KEY_BYTES:
            raise ValueError(f"A database key is {KEY_BYTES} bytes")
        self.path = path.resolve()
        self.key = key

    def __repr__(self) -> str:
        return f"DatabaseFile({str(self.path)!r})"

    def connect(self, *, read_only: bool = False) -> driver.Connection:
        """A connection with the key set and the file proven to open with it.

        A driver built without the cipher ignores `PRAGMA key` without a word and would write
        a plain file, so an empty cipher version refuses. A wrong key fails on the first read,
        before anything is written.
        """
        if read_only:
            connection = driver.connect(f"file:{self.path.as_posix()}?mode=ro", uri=True)
        else:
            connection = driver.connect(str(self.path))
        try:
            # One comparison for Unicode name lookups and case-insensitive UNIQUE indexes.
            connection.create_collation("UNICODE_NOCASE", _unicode_nocase)
            # A searchable view selects `relevance`, so every connection can read it: NULL,
            # unless a read that searches replaces it on its own connection (AG-SEARCH-058).
            connection.create_function("relevance", 2, _no_relevance, deterministic=True)
            connection.execute(f"PRAGMA key = \"x'{self.key.hex()}'\"")
            # Before the first read, so a file another connection holds locked is waited for
            # rather than taken for one the key does not open.
            connection.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_SECONDS * 1000}")
            if connection.execute("PRAGMA cipher_version").fetchone() is None:
                raise DatabaseRefused("The SQLite driver cannot encrypt")
            try:
                connection.execute("SELECT count(*) FROM sqlite_master").fetchone()
            except driver.DatabaseError as error:
                if error.sqlite_errorcode != driver.SQLITE_NOTADB:
                    raise
                raise DatabaseRefused(f"The key does not open {self.path.name}") from error
            # A sort or a temporary index never spills to a file outside the encryption.
            connection.execute("PRAGMA temp_store=MEMORY")
            if not read_only:
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("PRAGMA journal_mode=WAL")
        except BaseException:
            connection.close()
            raise
        return connection

    def copy_to(self, target: DatabaseFile) -> None:
        """Copy this database into `target`, encrypted under `target`'s key.

        SQLite's backup API reads through a connection, so the copy is consistent while other
        connections write, and it takes what still sits in the WAL.
        """
        source = self.connect()
        try:
            copy = target.connect()
            try:
                source.backup(copy)
            finally:
                copy.close()
        finally:
            source.close()


def create_schema(connection: Connection, *metadata: MetaData) -> None:
    """The shell's own tables, then the application's, on one connection.

    An application declares its tables on a `Base` of its own and hands its metadata here,
    so two applications in one process each create their own and neither reaches the
    other's. Nothing is altered: `create_all` adds what is missing and leaves the rest.
    """
    for declared in (Base.metadata, *metadata):
        declared.create_all(connection)


def sync_engine(file: DatabaseFile) -> Engine:
    """A synchronous engine on the file, for work done before the event loop starts."""
    return create_engine(
        f"sqlite:///{file.path.as_posix()}", creator=file.connect, module=driver, poolclass=NullPool
    )


def upgrade_database(file: DatabaseFile, *metadata: MetaData) -> None:
    """Bring the database up to the ORM model modules, which are the only schema source."""
    file.path.parent.mkdir(parents=True, exist_ok=True)
    engine = sync_engine(file)
    try:
        with engine.begin() as connection:
            create_schema(connection, *metadata)
    finally:
        engine.dispose()


class _AsyncDriver(AsyncAdapt_aiosqlite_dbapi):
    """SQLAlchemy's aiosqlite adapter, knowing the errors SQLCipher raises.

    The stock adapter takes its error classes from aiosqlite, which re-exports the standard
    `sqlite3` ones; SQLCipher raises its own, which would then pass through unwrapped instead
    of as SQLAlchemy's.
    """

    def _init_dbapi_attributes(self) -> None:
        super()._init_dbapi_attributes()
        for name in (
            "DatabaseError",
            "Error",
            "IntegrityError",
            "NotSupportedError",
            "OperationalError",
            "ProgrammingError",
            "sqlite_version",
            "sqlite_version_info",
        ):
            setattr(self, name, getattr(self.sqlite, name))


class Database:
    def __init__(self, file: DatabaseFile) -> None:
        self.file = file
        self._threads: list[Thread] = []
        self.engine: AsyncEngine = create_async_engine(
            f"sqlite+aiosqlite:///{file.path.as_posix()}",
            async_creator=self._open,
            module=_AsyncDriver(aiosqlite, driver),
            pool_pre_ping=True,
        )
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    def _open(self) -> aiosqlite.Connection:
        connection = aiosqlite.Connection(self.file.connect, ITER_CHUNK_SIZE)
        # SQLAlchemy marks the thread a daemon only for a connection it opens itself.
        connection._thread.daemon = True
        # A thread not started yet belongs to a connection another task is still opening.
        self._threads = [t for t in self._threads if t.is_alive() or t.ident is None]
        self._threads.append(connection._thread)
        return connection

    async def dispose(self) -> None:
        await self.engine.dispose()
        # A task cancelled while its connection was opening leaves that connection's thread to
        # finish on its own; unwaited, it would finish after the event loop had closed.
        for thread in self._threads:
            await asyncio.to_thread(thread.join, BUSY_TIMEOUT_SECONDS)
        self._threads.clear()
