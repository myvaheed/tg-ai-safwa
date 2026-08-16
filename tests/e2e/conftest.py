from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from safwa.ai.autoapproval import AutoApprovalReviewer
from safwa.ai.provider import ProviderTurn
from safwa.ai.service import AIAdvisor
from safwa.ai.sql import ReadOnlyQueryRunner, create_ai_views
from safwa.ai.subagents import SubagentRunner
from safwa.db import Database, upgrade_database
from safwa.domain import bootstrap_workspace
from safwa.memory import MemoryFileStore


class ScriptedProvider:
    """Deterministic OpenAI-compatible boundary used by isolated E2E tests."""

    def __init__(self, responses: list[str | ProviderTurn]) -> None:
        self.responses = deque(responses)
        self.calls: list[list[dict[str, object]]] = []
        self.options: list[dict[str, object]] = []

    async def complete(self, messages: list[dict[str, object]], **kwargs) -> str:
        self.calls.append([dict(message) for message in messages])
        self.options.append(dict(kwargs))
        if not self.responses:
            raise AssertionError("The advisor made an unexpected provider call")
        response = self.responses.popleft()
        return response.content if isinstance(response, ProviderTurn) else response

    async def complete_turn(self, messages: list[dict[str, object]], **kwargs) -> ProviderTurn:
        self.calls.append([dict(message) for message in messages])
        self.options.append(dict(kwargs))
        if not self.responses:
            raise AssertionError("The advisor made an unexpected provider call")
        response = self.responses.popleft()
        return response if isinstance(response, ProviderTurn) else ProviderTurn(content=response)


@dataclass
class E2EHarness:
    sessions: async_sessionmaker[AsyncSession]
    database: Database
    database_path: Path
    memory: MemoryFileStore

    def advisor(
        self,
        responses: list[str | ProviderTurn],
        *,
        cache_breakpoints: bool = False,
        subagents: tuple[object, ...] = (),
        autoapprove: bool = False,
    ) -> tuple[AIAdvisor, ScriptedProvider]:
        provider = ScriptedProvider(responses)
        advisor = AIAdvisor(
            self.sessions,
            provider,  # type: ignore[arg-type]
            self.memory,
            ReadOnlyQueryRunner(self.database_path),
            model_name="e2e-scripted-model",
            cache_breakpoints=cache_breakpoints,
            autoapproval=AutoApprovalReviewer(provider) if autoapprove else None,
            subagents=(
                SubagentRunner(
                    self.sessions,
                    subagents,  # type: ignore[arg-type]
                    provider_name="e2e",
                    model_name="e2e-scripted-model",
                )
                if subagents
                else None
            ),
        )
        return advisor, provider


@pytest_asyncio.fixture
async def e2e_harness(tmp_path: Path, monkeypatch) -> E2EHarness:
    """Real migrated SQLite plus real services; only remote APIs are replaced."""

    repository_root = Path(__file__).parents[2]
    monkeypatch.chdir(repository_root)
    database_path = tmp_path / "safwa-e2e.db"
    upgrade_database(f"sqlite:///{database_path.as_posix()}")
    database = Database(f"sqlite+aiosqlite:///{database_path.as_posix()}")
    async with database.sessions() as session:
        await bootstrap_workspace(session, 42, "Europe/Istanbul")
        await session.run_sync(lambda sync_session: create_ai_views(sync_session.connection()))
        await session.commit()

    memory_path = tmp_path / "memory.md"
    memory_path.write_text("I prefer sustainable plans.\n", encoding="utf-8")
    memory = MemoryFileStore(memory_path, database.sessions)
    await memory.sync()
    harness = E2EHarness(database.sessions, database, database_path, memory)
    try:
        yield harness
    finally:
        await database.dispose()
