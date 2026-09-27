"""A backup is the encrypted database and its locked key file; a restore and a passphrase change
replace that pair as one, a passphrase change backs the new pair up first, and a stop at any
step leaves a pair that opens."""

from __future__ import annotations

import hashlib
import os
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
from database_key import held_elsewhere

from tg_agent_shell.backup import (
    BackupError,
    change_passphrase,
    create_backup,
    inspect_backup,
    restore_backup,
)
from tg_agent_shell.foundation import key_file
from tg_agent_shell.foundation.database import DatabaseFile, DatabaseRefused
from tg_agent_shell.foundation.key_file import (
    DatabaseBusy,
    WrongPassphrase,
    create_key,
    key_path,
    unlock_database,
    unlock_key,
)

MARKER = "MARKER-2c81e0-plain-text-canary"
PASSPHRASE = "the passphrase the backup was made with"
NEW_PASSPHRASE = "a different passphrase, set later"
NOW = datetime(2026, 9, 27, 10, 15, tzinfo=UTC)


@pytest.fixture(autouse=True)
def cheap_passphrase_tries(monkeypatch):
    # Each try costs 128 MB by design; these tests make dozens and test the pair, not scrypt.
    monkeypatch.setattr(key_file, "SCRYPT_N", 2**10)


def _write(file: DatabaseFile, value: str) -> None:
    connection = file.connect()
    try:
        connection.execute("CREATE TABLE IF NOT EXISTS state (value TEXT NOT NULL)")
        connection.execute("DELETE FROM state")
        connection.execute("INSERT INTO state VALUES (?)", (value,))
        connection.commit()
    finally:
        connection.close()


def _read(file: DatabaseFile) -> str:
    connection = file.connect()
    try:
        return str(connection.execute("SELECT value FROM state").fetchone()[0])
    finally:
        connection.close()


def _live(tmp_path: Path, value: str) -> DatabaseFile:
    database = tmp_path / "data" / "app.db"
    create_key(database, PASSPHRASE)
    file = unlock_database(database, PASSPHRASE)
    _write(file, value)
    return file


def _digests(folder: Path) -> dict[str, str]:
    return {
        path.relative_to(folder).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in folder.rglob("*")
        if path.is_file()
    }


def test_a_backup_holds_the_encrypted_database_and_its_locked_key_and_nothing_readable(tmp_path):
    live = _live(tmp_path, MARKER)

    backup = create_backup(live, tmp_path / "backups", now=NOW)

    assert backup.path.name == "app-20260927T101500Z.zip"
    assert MARKER.encode() not in backup.path.read_bytes()
    with zipfile.ZipFile(backup.path) as archive:
        assert set(archive.namelist()) == {"app.db", "app.key", "manifest.json"}
        assert archive.read("app.key") == key_path(live.path).read_bytes()
    assert inspect_backup(backup.path, live.path, PASSPHRASE) == backup
    with pytest.raises(BackupError, match="passphrase"):
        inspect_backup(backup.path, live.path, NEW_PASSPHRASE)


def test_a_backup_taken_while_the_database_is_written_holds_what_is_committed(tmp_path):
    live = _live(tmp_path, "before")
    writer = live.connect()
    try:
        writer.execute("UPDATE state SET value = 'committed, still in the WAL'")
        writer.commit()
        backup = create_backup(live, tmp_path / "backups", now=NOW)
    finally:
        writer.close()

    elsewhere = tmp_path / "elsewhere" / "app.db"
    restore_backup(backup.path, elsewhere, PASSPHRASE, tmp_path / "set-aside")
    assert _read(unlock_database(elsewhere, PASSPHRASE)) == "committed, still in the WAL"


def test_a_restore_takes_the_backups_passphrase_and_sets_aside_what_was_there(tmp_path):
    live = _live(tmp_path, "original")
    backup = create_backup(live, tmp_path / "backups", now=NOW)
    change_passphrase(live.path, PASSPHRASE, NEW_PASSPHRASE, tmp_path / "backups")
    _write(unlock_database(live.path, NEW_PASSPHRASE), "changed")

    result = restore_backup(backup.path, live.path, PASSPHRASE, tmp_path / "backups", now=NOW)

    assert _read(unlock_database(live.path, PASSPHRASE)) == "original"
    with pytest.raises(WrongPassphrase):
        unlock_database(live.path, NEW_PASSPHRASE)
    assert result.set_aside is not None
    kept_key = unlock_key((result.set_aside / "app.key").read_bytes(), NEW_PASSPHRASE)
    assert _read(DatabaseFile(result.set_aside / "app.db", kept_key)) == "changed"


def test_a_wrong_passphrase_refuses_a_restore_before_any_live_file_is_touched(tmp_path):
    live = _live(tmp_path, "original")
    backup = create_backup(live, tmp_path / "backups", now=NOW)
    before = _digests(tmp_path)

    with pytest.raises(BackupError, match="passphrase"):
        restore_backup(backup.path, live.path, NEW_PASSPHRASE, tmp_path / "backups")

    assert _digests(tmp_path) == before


def test_a_passphrase_change_replaces_the_key_and_backs_up_under_the_new_passphrase(tmp_path):
    live = _live(tmp_path, MARKER)
    old_key = live.key
    older = create_backup(live, tmp_path / "backups", now=NOW)

    newer = change_passphrase(live.path, PASSPHRASE, NEW_PASSPHRASE, tmp_path / "backups")

    changed = unlock_database(live.path, NEW_PASSPHRASE)
    assert changed.key != old_key
    assert _read(changed) == MARKER
    with pytest.raises(DatabaseRefused):
        DatabaseFile(live.path, old_key).connect()
    assert inspect_backup(newer.path, live.path, NEW_PASSPHRASE) == newer
    with pytest.raises(BackupError, match="passphrase"):
        inspect_backup(newer.path, live.path, PASSPHRASE)
    with zipfile.ZipFile(newer.path) as archive:
        assert archive.read("app.key") == key_path(live.path).read_bytes()
        assert MARKER.encode() not in archive.read("app.db")
        (tmp_path / "newer.db").write_bytes(archive.read("app.db"))
    assert _read(DatabaseFile(tmp_path / "newer.db", changed.key)) == MARKER
    assert inspect_backup(older.path, live.path, PASSPHRASE) == older
    with pytest.raises(BackupError):
        inspect_backup(older.path, live.path, NEW_PASSPHRASE)


def test_a_restore_and_a_passphrase_change_refuse_while_another_process_holds_the_database(
    tmp_path,
):
    live = _live(tmp_path, MARKER)
    backup = create_backup(live, tmp_path / "backups", now=NOW)
    before = _digests(tmp_path)

    with held_elsewhere(live.path):
        with pytest.raises(DatabaseBusy):
            change_passphrase(live.path, PASSPHRASE, NEW_PASSPHRASE, tmp_path / "backups")
        with pytest.raises(DatabaseBusy):
            restore_backup(backup.path, live.path, PASSPHRASE, tmp_path / "backups")
        assert _digests(tmp_path) == before
        # A backup only reads, so it does not wait for the application to stop.
        create_backup(unlock_database(live.path, PASSPHRASE), tmp_path / "backups")

    # The holder was killed, and the hold ended with it.
    change_passphrase(live.path, PASSPHRASE, NEW_PASSPHRASE, tmp_path / "backups")
    assert _read(unlock_database(live.path, NEW_PASSPHRASE)) == MARKER


class _StopAt:
    """`os.replace` that fails once, on its `step`-th call: a process stopped there."""

    def __init__(self, step: int) -> None:
        self.step = step
        self.calls = 0
        self.replace = os.replace

    def __call__(self, source, target) -> None:  # noqa: ANN001
        self.calls += 1
        if self.calls == self.step:
            raise OSError("stopped here")
        self.replace(source, target)


# A passphrase change publishes its backup whole, then replaces the pair: it moves the new
# database beside the live one, publishes the new key file whole, then moves each into place.
# That is five moves, and a stop may come before any of them.
@pytest.mark.parametrize("step", [1, 2, 3, 4, 5])
def test_a_passphrase_change_stopped_at_any_step_leaves_a_pair_that_opens(
    tmp_path, monkeypatch, step
):
    live = _live(tmp_path, MARKER)
    backups = tmp_path / "backups"
    monkeypatch.setattr(key_file.os, "replace", _StopAt(step))

    with pytest.raises(BackupError, match="stopped here"):
        change_passphrase(live.path, PASSPHRASE, NEW_PASSPHRASE, backups, now=NOW)
    monkeypatch.undo()
    monkeypatch.setattr(key_file, "SCRYPT_N", 2**10)

    # Until the new key file is published the change has not happened; after, it has.
    passphrase = PASSPHRASE if step <= 3 else NEW_PASSPHRASE
    assert _read(unlock_database(live.path, passphrase)) == MARKER
    # A backup that is not written changes nothing; once written, it opens with the new passphrase.
    written = sorted(backups.glob("*.zip"))
    if step == 1:
        assert written == []
    else:
        assert [inspect_backup(path, live.path, NEW_PASSPHRASE).path for path in written] == [
            backups / "app-20260927T101500Z.zip"
        ]
    assert not (live.path.parent / "app.db.copy").exists()


@pytest.mark.parametrize("step", [1, 2, 3, 4])
def test_a_restore_stopped_at_any_step_leaves_a_pair_that_opens(tmp_path, monkeypatch, step):
    live = _live(tmp_path, "original")
    backup = create_backup(live, tmp_path / "backups", now=NOW)
    change_passphrase(live.path, PASSPHRASE, NEW_PASSPHRASE, tmp_path / "backups")
    _write(unlock_database(live.path, NEW_PASSPHRASE), "changed")
    monkeypatch.setattr(key_file.os, "replace", _StopAt(step))

    with pytest.raises(BackupError, match="stopped here"):
        restore_backup(backup.path, live.path, PASSPHRASE, tmp_path / "backups")
    monkeypatch.undo()
    monkeypatch.setattr(key_file, "SCRYPT_N", 2**10)

    if step <= 2:
        assert _read(unlock_database(live.path, NEW_PASSPHRASE)) == "changed"
    else:
        assert _read(unlock_database(live.path, PASSPHRASE)) == "original"
