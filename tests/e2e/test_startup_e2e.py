from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from database_key import keyed

from safwa.bootstrap import main as safwa_main
from safwa.bootstrap.modules import TEXT_MODEL
from safwa.config import Settings
from safwa.foundation.models import Base
from tg_agent_shell.foundation.database import DatabaseFile, upgrade_database
from tg_agent_shell.search.index import SearchIndex

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


class FakeProvider:
    instances: list[FakeProvider] = []

    def __init__(self, _config) -> None:
        self.closed = False
        self.__class__.instances.append(self)

    async def aclose(self) -> None:
        self.closed = True


class FakeEncoder:
    """The text model, never downloaded."""

    made: list[tuple[str, Path]] = []

    def __init__(self, model: str, cache_dir: Path) -> None:
        self.__class__.made.append((model, cache_dir))

    def encode(self, texts):
        return [[1.0] for _ in texts]


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


def _prepared_startup(tmp_path: Path, monkeypatch) -> tuple[DatabaseFile, Settings]:
    repository_root = Path(__file__).parents[2]
    monkeypatch.chdir(repository_root)
    database = keyed(tmp_path / "startup-e2e.db")
    upgrade_database(database, Base.metadata)

    FakeBot.instances.clear()
    FakeProvider.instances.clear()
    FakeDispatcher.instances.clear()
    monkeypatch.setattr(safwa_main, "Bot", FakeBot)
    monkeypatch.setattr(safwa_main, "OpenAICompatibleProvider", FakeProvider)
    monkeypatch.setattr(safwa_main, "Dispatcher", FakeDispatcher)
    FakeEncoder.made.clear()
    monkeypatch.setattr(safwa_main, "FastEmbedEncoder", FakeEncoder)

    return database, Settings(
        _env_file=None,
        telegram_bot_token="123456:test-token",
        telegram_bot_username="configured_safwa_bot",
        telegram_owner_id=42,
        database_path=database.path,
        data_dir=tmp_path / "data",
        ai_base_url="http://127.0.0.1:1234/v1",
        ai_api_key="test-key",
        ai_model="test-model",
        timezone="Europe/Istanbul",
    )


async def test_full_startup_reaches_polling_and_cleans_up(tmp_path: Path, monkeypatch):
    database, settings = _prepared_startup(tmp_path, monkeypatch)
    await safwa_main.run(settings, database)

    assert FakeDispatcher.instances[0].polling_started is True
    assert FakeDispatcher.instances[0].data["services"].bot_username == "configured_safwa_bot"
    assert FakeBot.instances[0].commands_set is True
    command_names = {command.command for command in FakeBot.instances[0].commands}
    assert {"memory", "sprint"} <= command_names
    # A screen the menu offers does not also take a command line.
    assert {"backlog", "profile"}.isdisjoint(command_names)
    assert FakeBot.instances[0].session.closed is True
    assert FakeProvider.instances[0].closed is True

    connection = database.connect()
    try:
        assert connection.execute("SELECT COUNT(*) FROM workspace").fetchone() == (1,)
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='tags'"
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='view' AND name='ai_cards'"
        ).fetchone() == (1,)
        assert connection.execute("SELECT name FROM tags").fetchall() == []
        assert connection.execute("SELECT name FROM saved_requests ORDER BY id").fetchall() == [
            ("Goals without children",), ("Goals without Values",), ("Values without Goals",)
        ]
    finally:
        connection.close()


async def test_ag_privacy_068_a_finder_that_cannot_load_stops_the_start(
    tmp_path: Path, monkeypatch
):
    """AG-PRIVACY-068 — tests/brd/tg_agent_shell/agents.feature"""
    database, settings = _prepared_startup(tmp_path, monkeypatch)

    def unloadable(*_args, **_kwargs):
        raise OSError("the model could not be downloaded")

    monkeypatch.setattr(safwa_main.HorizonPiiFinder, "load", unloadable)

    with pytest.raises(OSError, match="could not be downloaded"):
        await safwa_main.run(settings.model_copy(update={"ai_pseudonymize": True}), database)
    assert FakeProvider.instances == []
    assert FakeDispatcher.instances == []


async def test_defaults_are_seeded_once_and_deleted_requests_stay_deleted(
    tmp_path: Path, monkeypatch
):
    """SR-UI-013 — tests/brd/saved_requests.feature"""
    database, settings = _prepared_startup(tmp_path, monkeypatch)
    await safwa_main.run(settings, database)
    connection = database.connect()
    try:
        assert connection.execute("SELECT name FROM tags").fetchall() == []
        assert set(connection.execute("SELECT name FROM saved_requests").fetchall()) == {
            ("Goals without children",), ("Goals without Values",), ("Values without Goals",)
        }
        connection.execute("DELETE FROM saved_requests WHERE name = ?", ("Goals without children",))
        connection.commit()
    finally:
        connection.close()

    await safwa_main.run(settings, database)
    connection = database.connect()
    try:
        assert connection.execute("SELECT name FROM tags").fetchall() == []
        assert set(connection.execute("SELECT name FROM saved_requests").fetchall()) == {
            ("Goals without Values",), ("Values without Goals",)
        }
    finally:
        connection.close()


async def test_ag_search_059_the_text_model_is_loaded_into_the_one_index(
    tmp_path: Path, monkeypatch
):
    """AG-SEARCH-059 — tests/brd/tg_agent_shell/agents.feature"""
    database, settings = _prepared_startup(tmp_path, monkeypatch)
    loaded = asyncio.Event()
    loaded_indexes = []
    load = SearchIndex.load
    poll = FakeDispatcher.start_polling

    async def observe_load(index):
        await load(index)
        loaded_indexes.append(index)
        loaded.set()

    async def poll_until_loaded(dispatcher, bot, *, allowed_updates):
        await poll(dispatcher, bot, allowed_updates=allowed_updates)
        await asyncio.wait_for(loaded.wait(), timeout=5)

    monkeypatch.setattr(SearchIndex, "load", observe_load)
    monkeypatch.setattr(FakeDispatcher, "start_polling", poll_until_loaded)
    await safwa_main.run(settings, database)

    services = FakeDispatcher.instances[0].data["services"]
    assert isinstance(services.search, SearchIndex)
    assert loaded_indexes == [services.search]
    assert FakeEncoder.made[-1] == (TEXT_MODEL.name, settings.data_dir / "models")
