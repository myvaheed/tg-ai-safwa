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


def test_backup_and_restore_replaces_database_and_memory_atomically(tmp_path: Path) -> None:
    database = tmp_path / "safwa.db"
    memory = tmp_path / "memory.md"
    _write_database(database, "original")
    memory.write_text("prefers morning walks\n", encoding="utf-8")
    backup = create_backup(
        _database_url(database),
        memory,
        tmp_path / "exports",
        now=datetime(2026, 8, 8, 12, 0, tzinfo=UTC),
    )
    assert inspect_backup(backup.path) == backup

    _write_database(database, "changed")
    memory.write_text("changed locally\n", encoding="utf-8")
    with pytest.raises(BackupError, match="--yes"):
        restore_backup(backup.path, _database_url(database), memory)

    restored = restore_backup(backup.path, _database_url(database), memory, confirmed=True)
    assert _database_value(database) == "original"
    assert memory.read_text(encoding="utf-8") == "prefers morning walks\n"
    assert restored.safety_backup.is_file()
    assert inspect_backup(restored.safety_backup).memory_present is True


def test_restore_missing_memory_file_intentionally_clears_memory(tmp_path: Path) -> None:
    database = tmp_path / "safwa.db"
    memory = tmp_path / "memory.md"
    _write_database(database, "original")
    backup = create_backup(_database_url(database), memory, tmp_path / "exports")
    assert backup.memory_present is False

    memory.write_text("will be cleared\n", encoding="utf-8")
    restore_backup(backup.path, _database_url(database), memory, confirmed=True)
    assert not memory.exists()
