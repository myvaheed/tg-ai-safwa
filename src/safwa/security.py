"""The passphrase at Safwa's start, and the commands that back up, restore and re-key the database.

What each command is for, and when, is docs/SECURITY.md. The passphrase is only ever typed
at the console with echo off: never an argument or an environment variable, which other
programs can read.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from getpass import getpass
from pathlib import Path

from tg_agent_shell.backup import BackupError, change_passphrase, create_backup, restore_backup
from tg_agent_shell.foundation.database import DatabaseFile, DatabaseRefused
from tg_agent_shell.foundation.key_file import (
    DatabaseBusy,
    KeyFileError,
    WrongPassphrase,
    create_key,
    finish_replacement,
    hold,
    key_path,
    unlock_database,
)

from .config import Settings

PASSPHRASE_TRIES = 3
REFUSALS = "See 'When Safwa refuses to start' in docs/SECURITY.md."
INTERRUPTED = "See 'If a restore or a passphrase change was interrupted' in docs/SECURITY.md."
IN_USE = "Safwa is running, or a restore or a passphrase change is. Stop it, or let it finish."


@contextmanager
def held(database: Path) -> Iterator[None]:
    """Keep the database to this process for the block, or stop when another one has it."""
    with ExitStack() as stack:
        try:
            stack.enter_context(hold(database))
        except DatabaseBusy as error:
            raise SystemExit(f"{error}. {IN_USE}") from error
        yield


def unlock(database: Path) -> DatabaseFile:
    """Ask for the passphrase until it unlocks the database, three tries at most."""
    finish_replacement(database)
    if not key_path(database).exists():
        if database.exists():
            raise SystemExit(
                f"{database} is here, but its key file {key_path(database)} is not. {REFUSALS}"
            )
        raise SystemExit("There is no database yet. Make its key first: uv run safwa-key new")
    for _ in range(PASSPHRASE_TRIES):
        try:
            return unlock_database(database, getpass("Passphrase: "))
        except WrongPassphrase as error:
            print(f"{error}.")
        except (KeyFileError, DatabaseRefused) as error:
            raise SystemExit(f"{error}. {REFUSALS}") from error
    raise SystemExit(f"{PASSPHRASE_TRIES} wrong passphrases. Safwa stops.")


def _new_passphrase() -> str:
    passphrase = getpass("New passphrase: ")
    if getpass("The new passphrase again: ") != passphrase:
        raise SystemExit("The two passphrases differ. Nothing was changed.")
    return passphrase


def backup_main() -> None:
    parser = argparse.ArgumentParser(description="Back up the database and its key file")
    parser.add_argument(
        "destination", nargs="?", type=Path, help="Backup folder (defaults to data/backups)"
    )
    args = parser.parse_args()
    settings = Settings()
    database = unlock(settings.database_path)
    try:
        result = create_backup(database, args.destination or settings.data_dir / "backups")
    except BackupError as error:
        raise SystemExit(str(error)) from error
    print(result.path)


def restore_main() -> None:
    parser = argparse.ArgumentParser(description="Restore the database and its key file")
    parser.add_argument("archive", type=Path, help="The backup ZIP to restore")
    parser.add_argument(
        "--yes", action="store_true", help="Confirm replacing the database and its key file"
    )
    args = parser.parse_args()
    if not args.yes:
        raise SystemExit("A restore replaces the database and its key file. Add --yes to confirm.")
    settings = Settings()
    with held(settings.database_path):
        passphrase = getpass("The passphrase this backup was made with: ")
        try:
            result = restore_backup(
                args.archive, settings.database_path, passphrase, settings.data_dir / "backups"
            )
        except BackupError as error:
            raise SystemExit(str(error)) from error
    print(f"Restored {result.restored_from}.")
    if result.set_aside is not None:
        print(f"What was there before is in {result.set_aside}.")


def key_main() -> None:
    parser = argparse.ArgumentParser(description="Make the database key, or change its passphrase")
    parser.add_argument(
        "action",
        choices=("new", "passphrase"),
        help="new: the key for a new database; "
        "passphrase: a new passphrase, a new key, and a backup made with them",
    )
    args = parser.parse_args()
    settings = Settings()
    database = settings.database_path
    try:
        with held(database):
            if args.action == "new":
                create_key(database, _new_passphrase())
                print(f"Made {key_path(database)}. Keep the passphrase in your password manager.")
                return
            backup = change_passphrase(
                database,
                getpass("Current passphrase: "),
                _new_passphrase(),
                settings.data_dir / "backups",
            )
    except (KeyFileError, DatabaseRefused) as error:
        raise SystemExit(f"{error}. Nothing was changed.") from error
    except BackupError as error:
        raise SystemExit(f"{error}. {INTERRUPTED}") from error
    print("Changed. This backup opens with the new passphrase; copy it off this computer:")
    print(backup.path)
    print("Older backups open only with the old passphrase.")
