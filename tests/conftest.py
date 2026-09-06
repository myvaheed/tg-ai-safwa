from __future__ import annotations

import pytest
import pytest_asyncio
from brd_ids import SCENARIO_ID
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from safwa.bootstrap.main import bootstrap_workspace
from tg_agent_shell.foundation.models import Base


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--live-telegram",
        action="store_true",
        default=False,
        help="run tests that send messages to the dedicated Safwa-QA Telegram bot",
    )
    parser.addoption(
        "--brd",
        default=None,
        metavar="SCENARIO_ID",
        help="run only the tests that cite one BRD scenario, for example DI-DAY-001",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "brd(scenario_id): the approved BRD scenario this test is evidence for"
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    # The identifier is written once, in the docstring, and the marker is read off it, so
    # `pytest -m brd` and `--brd=DI-DAY-001` work without a second place to keep in step.
    wanted = config.getoption("--brd")
    selected: list[pytest.Item] = []
    for item in items:
        docstring = getattr(item.function, "__doc__", None) if hasattr(item, "function") else None
        match = SCENARIO_ID.match((docstring or "").strip())
        if match:
            item.add_marker(pytest.mark.brd(match.group("id")))
        if wanted is None or (match and match.group("id") == wanted):
            selected.append(item)
    if wanted is not None:
        config.hook.pytest_deselected(items=[i for i in items if i not in selected])
        items[:] = selected

    if config.getoption("--live-telegram"):
        return
    skip = pytest.mark.skip(reason="requires explicit --live-telegram opt-in")
    for item in items:
        if "live_telegram" in item.keywords:
            item.add_marker(skip)


@pytest_asyncio.fixture
async def sessions():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await bootstrap_workspace(session, 42, "Europe/Istanbul")
        await session.commit()
    yield factory
    await engine.dispose()


@pytest_asyncio.fixture
async def read_views(tmp_path):
    """A workspace on disk plus the `ai_*` views: what a test reads when Safwa reads.

    The views are dropped and rebuilt from the feature declarations, so a test that asks
    what Safwa sees asks the real catalogue rather than a copy of it.
    """
    from safwa.bootstrap.modules import AI_VIEWS, ALLOWED_VIEWS
    from tg_agent_shell.ai.sql import ReadOnlyQueryRunner, create_ai_views

    path = tmp_path / "views.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{path.as_posix()}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await connection.run_sync(lambda sync: create_ai_views(sync, AI_VIEWS))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await bootstrap_workspace(session, 42, "Europe/Istanbul")
        await session.commit()
    yield factory, ReadOnlyQueryRunner(path, ALLOWED_VIEWS, timezone="Europe/Istanbul")
    await engine.dispose()
