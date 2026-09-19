from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from safwa.backup import BackupError, create_backup, inspect_backup, restore_backup


def _database_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def _write_database(path: Path, value: str) -> None:
    database = sqlite3.connect(path)
    try:
        database.execute("CREATE TABLE IF NOT EXISTS state (value TEXT NOT NULL)")
        database.execute("DELETE FROM state")
        database.execute("INSERT INTO state (value) VALUES (?)", (value,))
        database.commit()
    finally:
        database.close()


def _database_value(path: Path) -> str:
    database = sqlite3.connect(path)
    try:
        return str(database.execute("SELECT value FROM state").fetchone()[0])
    finally:
        database.close()


def test_backup_and_restore_replaces_the_database_atomically(tmp_path: Path) -> None:
    database = tmp_path / "safwa.db"
    _write_database(database, "original")
    backup = create_backup(
        _database_url(database),
        tmp_path / "exports",
        now=datetime(2026, 8, 8, 12, 0, tzinfo=UTC),
    )
    assert inspect_backup(backup.path) == backup

    _write_database(database, "changed")
    with pytest.raises(BackupError, match="--yes"):
        restore_backup(backup.path, _database_url(database))

    restored = restore_backup(backup.path, _database_url(database), confirmed=True)
    assert _database_value(database) == "original"
    assert restored.safety_backup.is_file()
    assert inspect_backup(restored.safety_backup).path == restored.safety_backup
