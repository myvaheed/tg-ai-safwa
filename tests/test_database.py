"""The database is encrypted on disk, opened in one place, and only with the key that locks it."""

from __future__ import annotations

import asyncio
import hashlib
import sqlite3
import threading
from pathlib import Path

import pytest
from database_key import TEST_KEY, keyed
from sqlalchemy import exc, text

from tg_agent_shell.ai.sql import ReadOnlyQueryRunner, UnsafeQueryError
from tg_agent_shell.foundation import database as database_module
from tg_agent_shell.foundation.database import Database, DatabaseFile, DatabaseRefused, driver
from tg_agent_shell.foundation.key_file import (
    KeyFileError,
    WrongPassphrase,
    create_key,
    key_path,
    lock_key,
    new_key,
    unlock_database,
    unlock_key,
)

MARKER = "MARKER-7f3a9c-plain-text-canary"
PASSPHRASE = "correct horse battery staple"
OTHER_KEY = bytes(reversed(range(32)))


def _digests(folder: Path) -> dict[str, str]:
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in folder.iterdir()
        if path.is_file()
    }


def _with_marker(path: Path) -> DatabaseFile:
    file = keyed(path)
    connection = file.connect()
    try:
        connection.execute("CREATE TABLE notes (words TEXT NOT NULL)")
        connection.executemany("INSERT INTO notes VALUES (?)", [(f"{MARKER} {n}",) for n in range(50)])
        connection.commit()
    finally:
        connection.close()
    return file


def test_nothing_written_to_the_database_is_readable_on_disk(tmp_path):
    file = keyed(tmp_path / "app.db")
    connection = file.connect()
    try:
        connection.execute("CREATE TABLE notes (words TEXT NOT NULL)")
        connection.execute("INSERT INTO notes VALUES (?)", (MARKER,))
        connection.commit()
        # While the connection is open the row sits in the WAL, which is encrypted as well.
        wal = Path(f"{file.path}-wal").read_bytes()
        assert wal and MARKER.encode() not in wal
    finally:
        connection.close()

    raw = file.path.read_bytes()
    assert MARKER.encode() not in raw
    assert not raw.startswith(b"SQLite format 3\x00")
    plain = sqlite3.connect(file.path)
    try:
        with pytest.raises(sqlite3.DatabaseError):
            plain.execute("SELECT count(*) FROM sqlite_master").fetchone()
    finally:
        plain.close()


@pytest.mark.parametrize("read_only", [False, True])
def test_a_wrong_key_is_refused_and_changes_nothing(tmp_path, read_only):
    file = _with_marker(tmp_path / "app.db")
    before = _digests(tmp_path)

    with pytest.raises(DatabaseRefused, match="does not open"):
        DatabaseFile(file.path, OTHER_KEY).connect(read_only=read_only)

    assert _digests(tmp_path) == before


def test_a_driver_that_cannot_encrypt_is_refused(tmp_path, monkeypatch):
    # The standard driver ignores `PRAGMA key` without a word and would write a plain file.
    monkeypatch.setattr(database_module, "driver", sqlite3)

    with pytest.raises(DatabaseRefused, match="cannot encrypt"):
        keyed(tmp_path / "app.db").connect()


async def test_a_driver_error_through_the_engine_is_sqlalchemys(tmp_path):
    database = Database(keyed(tmp_path / "app.db"))
    try:
        async with database.engine.begin() as connection:
            await connection.execute(text("CREATE TABLE tags (name TEXT UNIQUE)"))
            await connection.execute(text("INSERT INTO tags VALUES ('Family')"))
        with pytest.raises(exc.IntegrityError):
            async with database.engine.begin() as connection:
                await connection.execute(text("INSERT INTO tags VALUES ('Family')"))
    finally:
        await database.dispose()


def _connection_threads() -> list[threading.Thread]:
    return [
        thread
        for thread in threading.enumerate()
        if thread.name.endswith("(_connection_worker_thread)")
    ]


async def test_a_connection_cancelled_while_opening_leaves_no_thread_after_dispose(tmp_path):
    file = _with_marker(tmp_path / "app.db")
    already_running = set(_connection_threads())
    # A writer holding the file locked in rollback-journal mode keeps the next open waiting.
    holder = file.connect()
    holder.execute("PRAGMA journal_mode=DELETE")
    holder.execute("BEGIN EXCLUSIVE")
    database = Database(file)

    async def read() -> None:
        async with database.engine.connect() as connection:
            await connection.execute(text("SELECT count(*) FROM notes"))

    task = asyncio.create_task(read())
    await asyncio.sleep(0.2)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    holder.rollback()
    holder.close()
    await database.dispose()

    assert set(_connection_threads()) - already_running == set()


@pytest.mark.parametrize(
    "sql, refused",
    [
        ("ATTACH DATABASE '{leak}' AS leak KEY ''", UnsafeQueryError),
        ("SELECT sqlcipher_export('main')", driver.Error),
    ],
)
async def test_query_data_cannot_export_a_plain_copy(tmp_path, sql, refused):
    file = _with_marker(tmp_path / "app.db")
    leak = tmp_path / "leak.db"
    runner = ReadOnlyQueryRunner(file, ())

    with pytest.raises(refused):
        await runner.run(sql.format(leak=leak.as_posix()))

    assert not leak.exists()


@pytest.mark.parametrize(
    "sql, parameters, expected",
    [
        ("SELECT ? LIKE ?", ("Стоматолог", "%стоматолог%"), 1),
        ("SELECT ? LIKE ?", ("Dentist", "%dentist%"), 1),
        ("SELECT ? LIKE ?", ("Ёлка", "ё_ка"), 1),
        ("SELECT ? LIKE ?", ("Ёлка", "ё_"), 0),
        ("SELECT ? LIKE ?", ("line\nbreak", "line%"), 1),
        ("SELECT ? LIKE ?", (42, "4_"), 1),
        ("SELECT ? LIKE ?", (None, "%"), None),
        ("SELECT ? LIKE ? ESCAPE ?", ("100%", "100!%", "!"), 1),
        ("SELECT ? LIKE ? ESCAPE ?", ("1000", "100!%", "!"), 0),
        ("SELECT ? LIKE ? ESCAPE ?", ("Я_Ты", "я~_ты", "~"), 1),
        ("SELECT ? LIKE ? ESCAPE ?", ("ЯxТы", "я~_ты", "~"), 0),
        ("SELECT ? LIKE ? ESCAPE ?", ("a", "a!", "!"), 0),
        ("SELECT ? LIKE ? ESCAPE ?", ("a", "a", None), None),
    ],
)
def test_like_folds_case_beyond_ascii(tmp_path, sql, parameters, expected):
    connection = keyed(tmp_path / "app.db").connect()
    try:
        assert connection.execute(sql, parameters).fetchone()[0] == expected
    finally:
        connection.close()


def test_a_key_round_trips_through_its_file_and_only_with_its_passphrase():
    key = new_key()
    content = lock_key(key, PASSPHRASE)

    assert unlock_key(content, PASSPHRASE) == key
    assert key.hex() not in content.decode()
    with pytest.raises(WrongPassphrase):
        unlock_key(content, "correct horse battery stapler")


def test_a_passphrase_under_sixteen_characters_is_refused():
    with pytest.raises(KeyFileError, match="at least 16"):
        lock_key(new_key(), "fifteen chars!!")


def test_a_key_file_edited_to_weaken_it_no_longer_unlocks():
    content = lock_key(new_key(), PASSPHRASE).decode().replace('"n": 131072', '"n": 1024')

    with pytest.raises(WrongPassphrase):
        unlock_key(content.encode(), PASSPHRASE)


def test_a_key_file_that_belongs_to_another_database_is_refused(tmp_path):
    database = tmp_path / "app.db"
    _with_marker(database)
    key_path(database).write_bytes(lock_key(OTHER_KEY, PASSPHRASE))

    with pytest.raises(DatabaseRefused, match="does not open"):
        unlock_database(database, PASSPHRASE)


def test_a_new_key_is_only_for_a_new_database(tmp_path):
    database = tmp_path / "app.db"
    create_key(database, PASSPHRASE)
    first = key_path(database).read_bytes()

    with pytest.raises(KeyFileError, match="already exists"):
        create_key(database, PASSPHRASE)
    assert key_path(database).read_bytes() == first
    assert unlock_database(database, PASSPHRASE).key != TEST_KEY
