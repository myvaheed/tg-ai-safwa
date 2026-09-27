"""A backup is one ZIP of the encrypted database and its locked key file; a restore puts both back.

The archive holds nothing readable: the database stays encrypted with its key, and the key
stays locked with the passphrase it was made under. So a backup opens anywhere with that
passphrase and nowhere without it, and restoring one means typing the passphrase it was made
with, which is from then on the one the application starts with. A passphrase change writes
a backup under the new passphrase before it replaces the pair, so it never finishes without one.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .foundation.database import DatabaseFile, DatabaseRefused, driver
from .foundation.key_file import (
    KeyFileError,
    hold,
    key_path,
    lock_key,
    new_key,
    remove_database_files,
    replace_pair,
    unlock_database,
    unlock_key,
)

# 3: the encrypted database and its locked key file. 2 was the database alone, unencrypted.
FORMAT_VERSION = 3
MANIFEST_MEMBER = "manifest.json"


class BackupError(RuntimeError):
    """A backup is malformed, incompatible, or unsafe to restore."""


@dataclass(frozen=True)
class BackupInfo:
    path: Path
    created_at: str


@dataclass(frozen=True)
class RestoreResult:
    restored_from: Path
    set_aside: Path | None


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _timestamp(now: datetime | None) -> str:
    return (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")


def _free_path(folder: Path, stem: str, suffix: str) -> Path:
    candidate = folder / f"{stem}{suffix}"
    index = 2
    while candidate.exists():
        candidate = folder / f"{stem}-{index}{suffix}"
        index += 1
    return candidate


def _copy(source: DatabaseFile, target: DatabaseFile) -> None:
    try:
        source.copy_to(target)
    except driver.Error as error:
        raise BackupError(f"Could not copy the database: {error}") from error


def _write_archive(
    destination: Path,
    database: Path,
    database_data: bytes,
    key_content: bytes,
    now: datetime | None,
) -> BackupInfo:
    """A ZIP holding `database_data` and `key_content` under the names of `database`'s pair."""
    destination.mkdir(parents=True, exist_ok=True)
    timestamp = _timestamp(now)
    archive_path = _free_path(destination, f"{database.stem}-{timestamp}", ".zip")
    members = {database.name: database_data, key_path(database).name: key_content}
    manifest = {
        "format_version": FORMAT_VERSION,
        "created_at": timestamp,
        "files": {
            name: {"sha256": _digest(data), "size": len(data)} for name, data in members.items()
        },
    }
    partial = archive_path.with_suffix(".zip.partial")
    try:
        # Encrypted bytes do not compress, so they are stored as they are.
        with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_STORED) as archive:
            for name, data in members.items():
                archive.writestr(name, data)
            archive.writestr(MANIFEST_MEMBER, json.dumps(manifest, sort_keys=True, indent=2))
        os.replace(partial, archive_path)
    except OSError as error:
        partial.unlink(missing_ok=True)
        raise BackupError(f"Could not write the backup archive: {error}") from error
    return BackupInfo(archive_path, timestamp)


def create_backup(
    database: DatabaseFile, destination: Path, *, now: datetime | None = None
) -> BackupInfo:
    """A ZIP of the database, copied consistently even while it is written, and its key file."""
    if not database.path.is_file():
        raise BackupError(f"There is no database at {database.path}")
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination) as temporary:
        snapshot = DatabaseFile(Path(temporary) / database.path.name, database.key)
        _copy(database, snapshot)
        database_data = snapshot.path.read_bytes()
    key_content = key_path(database.path).read_bytes()
    return _write_archive(destination, database.path, database_data, key_content, now)


def change_passphrase(
    database: Path, current: str, new: str, destination: Path, *, now: datetime | None = None
) -> BackupInfo:
    """Lock the database under `new` with a new key, and back it up under `new` on the way.

    The database is copied under the new key beside the live one, and that copy is backed up
    before it replaces the live pair: a backup that fails changes nothing, and a change that
    finishes has one. The old key opens nothing written after. `DatabaseBusy` refuses it while
    another process holds the database.
    """
    with hold(database):
        live = unlock_database(database, current)
        key = new_key()
        content = lock_key(key, new)
        copy = database.with_name(f"{database.name}.copy")
        remove_database_files(copy)
        try:
            _copy(live, DatabaseFile(copy, key))
            backup = _write_archive(destination, database, copy.read_bytes(), content, now)
            replace_pair(database, copy, content)
        except OSError as error:
            raise BackupError(f"Could not change the passphrase: {error}") from error
        finally:
            remove_database_files(copy)
        return backup


def _read_archive(archive_path: Path, database: Path) -> tuple[str, bytes, bytes]:
    """The creation time, database and key file of an archive whose checksums hold."""
    names = {database.name, key_path(database).name}
    try:
        with zipfile.ZipFile(archive_path) as archive:
            if set(archive.namelist()) != names | {MANIFEST_MEMBER}:
                raise BackupError(f"The archive does not hold exactly {sorted(names)}")
            manifest = json.loads(archive.read(MANIFEST_MEMBER))
            members = {name: archive.read(name) for name in names}
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        raise BackupError(f"Could not read the backup archive: {error}") from error
    if not isinstance(manifest, dict) or manifest.get("format_version") != FORMAT_VERSION:
        raise BackupError("This backup's format is not one this version restores")
    files = manifest.get("files")
    created_at = manifest.get("created_at")
    if not isinstance(files, dict) or not isinstance(created_at, str):
        raise BackupError("The backup's manifest is invalid")
    for name, data in members.items():
        recorded = files.get(name)
        if not isinstance(recorded, dict) or recorded.get("sha256") != _digest(data):
            raise BackupError(f"The backup's checksum failed for {name}")
    return created_at, members[database.name], members[key_path(database).name]


def _check_opens(database: Path, key: bytes) -> None:
    try:
        connection = DatabaseFile(database, key).connect()
        try:
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise BackupError("The backup's database failed its integrity check")
        finally:
            connection.close()
    except (DatabaseRefused, driver.Error) as error:
        raise BackupError(f"The backup's database does not open: {error}") from error


def _unlocked(key_content: bytes, passphrase: str) -> bytes:
    try:
        return unlock_key(key_content, passphrase)
    except KeyFileError as error:
        raise BackupError(f"{error}; it is the passphrase this backup was made with") from error


def inspect_backup(archive_path: Path, database: Path, passphrase: str) -> BackupInfo:
    """Prove an archive restores in place of `database`: its checksums, passphrase and integrity."""
    created_at, database_data, key_content = _read_archive(archive_path, database)
    key = _unlocked(key_content, passphrase)
    with tempfile.TemporaryDirectory() as temporary:
        copy = Path(temporary) / database.name
        copy.write_bytes(database_data)
        _check_opens(copy, key)
    return BackupInfo(archive_path, created_at)


def restore_backup(
    archive_path: Path,
    database: Path,
    passphrase: str,
    set_aside_to: Path,
    *,
    now: datetime | None = None,
) -> RestoreResult:
    """Put an archive's database and key file in place of the live pair.

    Nothing live is touched until the archive has proven it opens with `passphrase`. What was
    there before is then copied, as it is, into a folder under `set_aside_to`. `DatabaseBusy`
    refuses it while another process holds the database.
    """
    with hold(database):
        _, database_data, key_content = _read_archive(archive_path, database)
        key = _unlocked(key_content, passphrase)
        replacement = database.with_name(f"{database.name}.restore")
        remove_database_files(replacement)
        try:
            replacement.write_bytes(database_data)
            _check_opens(replacement, key)
            set_aside = _set_aside(database, set_aside_to, _timestamp(now))
            replace_pair(database, replacement, key_content)
        except OSError as error:
            raise BackupError(f"Could not restore the backup: {error}") from error
        finally:
            remove_database_files(replacement)
        return RestoreResult(archive_path, set_aside)


def _set_aside(database: Path, folder: Path, timestamp: str) -> Path | None:
    """Copy the live pair, WAL included, into a folder of its own; None when there is none."""
    present = [
        path
        for path in (database, Path(f"{database}-wal"), key_path(database))
        if path.exists()
    ]
    if not present:
        return None
    target = _free_path(folder, f"{database.stem}-before-restore-{timestamp}", "")
    target.mkdir(parents=True)
    for path in present:
        shutil.copy2(path, target / path.name)
    return target
