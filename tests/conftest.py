from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from safwa.domain import bootstrap_workspace
from safwa.models import Base


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--live-telegram",
        action="store_true",
        default=False,
        help="run tests that send messages to the dedicated Safwa-QA Telegram bot",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
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
