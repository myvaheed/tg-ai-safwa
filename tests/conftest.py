from __future__ import annotations

import pytest
import pytest_asyncio
from brd_ids import SCENARIO_ID
from database_key import keyed

from tg_agent_shell.foundation.database import Database, create_schema

# Safwa is imported inside the fixtures that need it, never here: `tests/shell/` runs the
# example application, and a collection that pulled Safwa in would create its tables too.


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--live-telegram",
        action="store_true",
        default=False,
        help="run tests that send messages to the dedicated Safwa-QA Telegram bot",
    )
    parser.addoption(
        "--live-provider",
        action="store_true",
        default=False,
        help="run tests that send requests to the provider SAFWA_AI_* configures",
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

    for marker, flag in (("live_telegram", "--live-telegram"), ("live_provider", "--live-provider")):
        if config.getoption(flag):
            continue
        skip = pytest.mark.skip(reason=f"requires explicit {flag} opt-in")
        for item in items:
            if marker in item.keywords:
                item.add_marker(skip)


@pytest_asyncio.fixture
async def sessions(tmp_path):
    from safwa.bootstrap.main import bootstrap_workspace
    from safwa.bootstrap.modules import AI_VIEWS
    from safwa.foundation.models import Base
    from tg_agent_shell.ai.sql import create_ai_views

    database = Database(keyed(tmp_path / "sessions.db"))
    async with database.engine.begin() as connection:
        await connection.run_sync(create_schema, Base.metadata)
        # A saved query is compiled against the `ai_*` views before it is stored, so a test
        # database without them is not the database the code under test runs on.
        await connection.run_sync(lambda sync: create_ai_views(sync, AI_VIEWS))
    async with database.sessions() as session:
        await bootstrap_workspace(session, 42, "Europe/Istanbul")
        await session.commit()
    yield database.sessions
    await database.dispose()


@pytest_asyncio.fixture
async def read_views(tmp_path):
    """A workspace on disk plus the `ai_*` views: what a test reads when Safwa reads.

    The views are dropped and rebuilt from the feature declarations, so a test that asks
    what Safwa sees asks the real catalogue rather than a copy of it.
    """
    from safwa.bootstrap.main import bootstrap_workspace
    from safwa.bootstrap.modules import AI_VIEWS, ALLOWED_VIEWS
    from safwa.foundation.models import Base
    from tg_agent_shell.ai.sql import ReadOnlyQueryRunner, create_ai_views

    file = keyed(tmp_path / "views.db")
    database = Database(file)
    async with database.engine.begin() as connection:
        await connection.run_sync(create_schema, Base.metadata)
        await connection.run_sync(lambda sync: create_ai_views(sync, AI_VIEWS))
    async with database.sessions() as session:
        await bootstrap_workspace(session, 42, "Europe/Istanbul")
        await session.commit()
    yield database.sessions, ReadOnlyQueryRunner(file, ALLOWED_VIEWS, timezone="Europe/Istanbul")
    await database.dispose()
