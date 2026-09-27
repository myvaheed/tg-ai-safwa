"""Safwa asks for the passphrase at the console, and its commands make, change and carry the key."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from database_key import held_elsewhere

from safwa import security
from safwa.bootstrap import main as bootstrap
from tg_agent_shell.backup import inspect_backup
from tg_agent_shell.foundation import key_file
from tg_agent_shell.foundation.key_file import create_key, key_path, unlock_database

PASSPHRASE = "the owner's own long passphrase"
NEW_PASSPHRASE = "the owner's next long passphrase"


@pytest.fixture(autouse=True)
def owner_console(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SAFWA_TELEGRAM_BOT_TOKEN", "123456:test-token")
    monkeypatch.setenv("SAFWA_TELEGRAM_OWNER_ID", "42")
    monkeypatch.setenv("SAFWA_DATABASE_PATH", str(tmp_path / "data" / "safwa.db"))
    monkeypatch.setenv("SAFWA_DATA_DIR", str(tmp_path / "data"))
    # Each try costs 128 MB by design; these tests are about the prompts, not scrypt.
    monkeypatch.setattr(key_file, "SCRYPT_N", 2**10)


def _typed(monkeypatch, *answers: str) -> list[str]:
    """The owner types `answers` in order; returns the prompts they were asked."""
    prompts: list[str] = []
    queue = list(answers)

    def getpass(prompt: str) -> str:
        prompts.append(prompt)
        return queue.pop(0)

    monkeypatch.setattr(security, "getpass", getpass)
    return prompts


def _command(monkeypatch, main, *arguments: str) -> None:
    monkeypatch.setattr(sys, "argv", ["safwa", *arguments])
    main()


def test_a_first_start_says_to_make_the_key_and_asks_nothing(tmp_path, monkeypatch):
    prompts = _typed(monkeypatch)

    with pytest.raises(SystemExit, match="safwa-key new"):
        security.unlock(tmp_path / "data" / "safwa.db")
    assert prompts == []


def test_a_database_without_its_key_file_is_refused_before_any_prompt(tmp_path, monkeypatch):
    database = tmp_path / "data" / "safwa.db"
    create_key(database, PASSPHRASE)
    unlock_database(database, PASSPHRASE).connect().close()
    key_path(database).unlink()
    prompts = _typed(monkeypatch)

    with pytest.raises(SystemExit, match="its key file"):
        security.unlock(database)
    assert prompts == []


def test_three_wrong_passphrases_stop_the_start(tmp_path, monkeypatch):
    database = tmp_path / "data" / "safwa.db"
    create_key(database, PASSPHRASE)
    prompts = _typed(monkeypatch, "wrong once, wrong!", "wrong twice, wrong!", "wrong thrice, wrong")

    with pytest.raises(SystemExit, match="3 wrong passphrases"):
        security.unlock(database)
    assert len(prompts) == security.PASSPHRASE_TRIES


def test_the_right_passphrase_on_a_later_try_unlocks(tmp_path, monkeypatch):
    database = tmp_path / "data" / "safwa.db"
    create_key(database, PASSPHRASE)
    _typed(monkeypatch, "a wrong passphrase first", PASSPHRASE)

    assert security.unlock(database).path == database


def test_safwa_a_restore_and_a_passphrase_change_refuse_before_any_prompt_while_it_is_held(
    tmp_path, monkeypatch
):
    database = tmp_path / "data" / "safwa.db"
    create_key(database, PASSPHRASE)
    prompts = _typed(monkeypatch)

    with held_elsewhere(database):
        with pytest.raises(SystemExit, match="Safwa is running"):
            bootstrap.main()
        with pytest.raises(SystemExit, match="Safwa is running"):
            _command(monkeypatch, security.key_main, "passphrase")
        with pytest.raises(SystemExit, match="Safwa is running"):
            _command(monkeypatch, security.restore_main, "any.zip", "--yes")
    assert prompts == []


def test_the_commands_make_back_up_rekey_and_restore_the_pair(tmp_path, monkeypatch, capsys):
    database = tmp_path / "data" / "safwa.db"
    _typed(monkeypatch, PASSPHRASE, PASSPHRASE)
    _command(monkeypatch, security.key_main, "new")
    unlock_database(database, PASSPHRASE).connect().close()

    _typed(monkeypatch, PASSPHRASE)
    _command(monkeypatch, security.backup_main)
    archive = Path(capsys.readouterr().out.strip().splitlines()[-1])
    assert archive.parent == tmp_path / "data" / "backups"

    _typed(monkeypatch, PASSPHRASE, NEW_PASSPHRASE, "not the same passphrase")
    with pytest.raises(SystemExit, match="differ"):
        _command(monkeypatch, security.key_main, "passphrase")
    _typed(monkeypatch, PASSPHRASE, NEW_PASSPHRASE, NEW_PASSPHRASE)
    _command(monkeypatch, security.key_main, "passphrase")
    unlock_database(database, NEW_PASSPHRASE)
    changed = [Path(line) for line in capsys.readouterr().out.splitlines() if line.endswith(".zip")]
    assert [path.parent for path in changed] == [tmp_path / "data" / "backups"]
    inspect_backup(changed[0], database, NEW_PASSPHRASE)

    prompts = _typed(monkeypatch)
    with pytest.raises(SystemExit, match="--yes"):
        _command(monkeypatch, security.restore_main, str(archive))
    assert prompts == []
    _typed(monkeypatch, PASSPHRASE)
    _command(monkeypatch, security.restore_main, str(archive), "--yes")
    unlock_database(database, PASSPHRASE)
