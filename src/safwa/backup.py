"""Portable, local backup and restore support for Safwa's user-owned data."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .config import Settings

# 2: the database alone. Memory is rows of it since the retro started writing it.
FORMAT_VERSION = 2
DATABASE_MEMBER = "safwa.db"
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
    safety_backup: Path


def database_path(database_url: str) -> Path:
    if not database_url.startswith("sqlite:///"):
        raise BackupError("Safwa backups require a local sqlite:/// database URL")
    return Path(database_url.removeprefix("sqlite:///"))


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _next_backup_path(destination: Path, timestamp: str) -> Path:
    candidate = destination / f"safwa-{timestamp}.zip"
    index = 2
    while candidate.exists():
        candidate = destination / f"safwa-{timestamp}-{index}.zip"
        index += 1
    return candidate


def _snapshot_database(source_path: Path, destination_path: Path) -> bytes:
    if not source_path.is_file():
        raise BackupError(f"Safwa database does not exist: {source_path}")
    try:
        source = sqlite3.connect(source_path)
        target = sqlite3.connect(destination_path)
        try:
            # SQLite's backup API is consistent even while a WAL-mode bot is running.
            source.backup(target)
        finally:
            target.close()
            source.close()
    except sqlite3.Error as error:
        raise BackupError(f"Could not snapshot the SQLite database: {error}") from error
    return destination_path.read_bytes()


def create_backup(
    database_url: str,
    destination: Path,
    *,
    now: datetime | None = None,
) -> BackupInfo:
    """Create a ZIP backup containing the database."""
    database = database_path(database_url)
    destination.mkdir(parents=True, exist_ok=True)
    timestamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    archive_path = _next_backup_path(destination, timestamp)
    with tempfile.TemporaryDirectory(dir=destination) as temporary_dir:
        database_data = _snapshot_database(database, Path(temporary_dir) / DATABASE_MEMBER)
    manifest = {
        "format_version": FORMAT_VERSION,
        "created_at": timestamp,
        "files": {
            DATABASE_MEMBER: {"sha256": _digest(database_data), "size": len(database_data)},
        },
    }
    temporary_archive = archive_path.with_suffix(".zip.tmp")
    try:
        with zipfile.ZipFile(temporary_archive, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(DATABASE_MEMBER, database_data)
            archive.writestr(MANIFEST_MEMBER, json.dumps(manifest, sort_keys=True, indent=2))
        os.replace(temporary_archive, archive_path)
    except (OSError, zipfile.BadZipFile) as error:
        temporary_archive.unlink(missing_ok=True)
        raise BackupError(f"Could not write backup archive: {error}") from error
    return BackupInfo(archive_path, timestamp)


def _validated_members(archive_path: Path) -> tuple[dict[str, object], bytes]:
    if not archive_path.is_file():
        raise BackupError(f"Backup archive does not exist: {archive_path}")
    try:
        with zipfile.ZipFile(archive_path) as archive:
            names = set(archive.namelist())
            if names != {DATABASE_MEMBER, MANIFEST_MEMBER}:
                raise BackupError("Backup archive has unexpected or missing files")
            manifest = json.loads(archive.read(MANIFEST_MEMBER))
            database_data = archive.read(DATABASE_MEMBER)
    except (OSError, ValueError, zipfile.BadZipFile, KeyError) as error:
        if isinstance(error, BackupError):
            raise
        raise BackupError(f"Could not read backup archive: {error}") from error
    if not isinstance(manifest, dict) or manifest.get("format_version") != FORMAT_VERSION:
        raise BackupError("Backup archive format is not supported")
    files = manifest.get("files")
    if not isinstance(files, dict):
        raise BackupError("Backup archive manifest is invalid")
    metadata = files.get(DATABASE_MEMBER)
    if not isinstance(metadata, dict) or metadata.get("sha256") != _digest(database_data):
        raise BackupError(f"Backup archive checksum failed for {DATABASE_MEMBER}")
    return manifest, database_data


def inspect_backup(archive_path: Path) -> BackupInfo:
    manifest, database_data = _validated_members(archive_path)
    with tempfile.TemporaryDirectory() as temporary_dir:
        temporary_database = Path(temporary_dir) / DATABASE_MEMBER
        temporary_database.write_bytes(database_data)
        try:
            database = sqlite3.connect(temporary_database)
            try:
                if database.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise BackupError("Backup database integrity check failed")
            finally:
                database.close()
        except sqlite3.Error as error:
            raise BackupError(f"Backup database is invalid: {error}") from error
    created_at = manifest.get("created_at")
    if not isinstance(created_at, str):
        raise BackupError("Backup archive creation timestamp is invalid")
    return BackupInfo(archive_path, created_at)


def restore_backup(
    archive_path: Path,
    database_url: str,
    *,
    confirmed: bool = False,
) -> RestoreResult:
    """Restore a validated archive. Safwa must be stopped before this is called."""
    if not confirmed:
        raise BackupError("Refusing to restore without the explicit --yes confirmation")
    inspect_backup(archive_path)
    database_data = _validated_members(archive_path)[1]
    database = database_path(database_url)
    database.parent.mkdir(parents=True, exist_ok=True)
    safety = create_backup(database_url, database.parent / "backups")

    replacement_database = database.with_name(f".{database.name}.{uuid4().hex}.restore")
    try:
        replacement_database.write_bytes(database_data)
        # Validate the copied replacement immediately before touching live files.
        replacement = sqlite3.connect(replacement_database)
        try:
            if replacement.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise BackupError("Restored database integrity check failed")
        finally:
            replacement.close()

        # A stale WAL/SHM pair would otherwise be replayed against the restored main DB.
        for sidecar in (Path(f"{database}-wal"), Path(f"{database}-shm")):
            sidecar.unlink(missing_ok=True)
        os.replace(replacement_database, database)
    except (OSError, sqlite3.Error) as error:
        raise BackupError(f"Could not restore backup: {error}") from error
    finally:
        replacement_database.unlink(missing_ok=True)
    return RestoreResult(archive_path, safety.path)


def backup_main() -> None:
    parser = argparse.ArgumentParser(description="Create a local Safwa backup")
    parser.add_argument(
        "destination", nargs="?", type=Path, help="Backup folder (defaults to data/backups)"
    )
    args = parser.parse_args()
    settings = Settings()
    result = create_backup(settings.database_url, args.destination or settings.data_dir / "backups")
    print(result.path)


def restore_main() -> None:
    parser = argparse.ArgumentParser(description="Restore a local Safwa backup")
    parser.add_argument("archive", type=Path, help="Safwa ZIP backup to restore")
    parser.add_argument(
        "--yes", action="store_true", help="Confirm replacing the local Safwa database"
    )
    args = parser.parse_args()
    settings = Settings()
    result = restore_backup(args.archive, settings.database_url, confirmed=args.yes)
    print(f"Restored {result.restored_from}; safety backup: {result.safety_backup}")
