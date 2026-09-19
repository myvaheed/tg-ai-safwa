from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from safwa.bootstrap import main as safwa_main
from safwa.config import Settings
from safwa.foundation.models import Base
from tg_agent_shell.foundation.database import upgrade_database

pytestmark = pytest.mark.e2e


class FakeSession:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class FakeBot:
    instances: list[FakeBot] = []

    def __init__(self, **_kwargs) -> None:
        self.session = FakeSession()
        self.commands_set = False
        self.commands = []
        self.__class__.instances.append(self)

    async def get_me(self):
        return SimpleNamespace(id=9001, username="safwa_qa_bot")

    async def set_my_commands(self, commands) -> None:
        self.commands = commands
        self.commands_set = bool(commands)


class FakeHistory:
    def __init__(self) -> None:
        self.started = False
        self.closed = False

    async def start(self) -> None:
        self.started = True

    async def close(self) -> None:
        self.closed = True


class FakeHistoryFactory:
    instance = FakeHistory()

    def __new__(cls, *_args, **_kwargs):
        return cls.instance


class FakeProvider:
    instances: list[FakeProvider] = []

    def __init__(self, _config) -> None:
        self.closed = False
        self.__class__.instances.append(self)

    async def aclose(self) -> None:
        self.closed = True


class MiddlewareRegistrar:
    def register(self, _middleware) -> None:
        return None


class Observer:
    def __init__(self) -> None:
        self.outer_middleware = MiddlewareRegistrar()
        self.registered: list[tuple[object, ...]] = []

    def register(self, handler, *filters) -> None:
        self.registered.append((handler, *filters))


class FakeDispatcher:
    instances: list[FakeDispatcher] = []

    def __init__(self) -> None:
        self.data = {}
        self.polling_started = False
        self.__class__.instances.append(self)

    def __setitem__(self, key, value) -> None:
        self.data[key] = value

    def include_router(self, _router) -> None:
        return None

    def resolve_used_update_types(self) -> list[str]:
        return ["message", "callback_query"]

    async def start_polling(self, bot, *, allowed_updates) -> None:
        assert isinstance(bot, FakeBot)
        assert allowed_updates == ["message", "callback_query"]
        self.polling_started = True
        await asyncio.sleep(0)


def _prepared_startup(tmp_path: Path, monkeypatch) -> tuple[Path, Settings]:
    repository_root = Path(__file__).parents[2]
    monkeypatch.chdir(repository_root)
    database_path = tmp_path / "startup-e2e.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    upgrade_database(database_url, Base.metadata)

    FakeBot.instances.clear()
    FakeProvider.instances.clear()
    FakeDispatcher.instances.clear()
    FakeHistoryFactory.instance = FakeHistory()
    monkeypatch.setattr(safwa_main, "Bot", FakeBot)
    monkeypatch.setattr(safwa_main, "OpenAICompatibleProvider", FakeProvider)
    monkeypatch.setattr(safwa_main, "TelegramHistorySource", FakeHistoryFactory)
    monkeypatch.setattr(safwa_main, "Dispatcher", FakeDispatcher)

    return database_path, Settings(
        _env_file=None,
        telegram_bot_token="123456:test-token",
        telegram_bot_username="configured_safwa_bot",
        telegram_owner_id=42,
        telegram_api_id=12345,
        telegram_api_hash="test-api-hash",
        telegram_history_required=True,
        telegram_user_session_path=tmp_path / "telegram-user",
        database_url=database_url,
        data_dir=tmp_path / "data",
        ai_base_url="http://127.0.0.1:1234/v1",
        ai_api_key="test-key",
        ai_model="test-model",
        timezone="Europe/Istanbul",
    )


async def test_full_startup_reaches_polling_and_cleans_up(tmp_path: Path, monkeypatch):
    database_path, settings = _prepared_startup(tmp_path, monkeypatch)
    await safwa_main.run(settings)

    assert FakeDispatcher.instances[0].polling_started is True
    assert FakeDispatcher.instances[0].data["services"].bot_username == "configured_safwa_bot"
    assert FakeBot.instances[0].commands_set is True
    command_names = {command.command for command in FakeBot.instances[0].commands}
    assert {"memory", "sprint"} <= command_names
    # A screen the menu offers does not also take a command line.
    assert {"backlog", "profile"}.isdisjoint(command_names)
    assert FakeBot.instances[0].session.closed is True
    assert FakeProvider.instances[0].closed is True
    assert FakeHistoryFactory.instance.started is True
    assert FakeHistoryFactory.instance.closed is True

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM workspace").fetchone() == (1,)
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='tags'"
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='view' AND name='ai_cards'"
        ).fetchone() == (1,)
