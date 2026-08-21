from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from llm_gateway import CompletionRequest, CompletionTurn, ToolCall
from safwa.ai.autoapproval import AutoApprovalReviewer
from safwa.ai.board import BOARD_PROMPT, BOARD_TOOLS
from safwa.ai.service import AIAdvisor, query_read_tool
from safwa.ai.sql import ReadOnlyQueryRunner, create_ai_views
from safwa.ai.subagents import RoutedSubagent
from safwa.domain import bootstrap_workspace
from safwa.foundation.database import Database, upgrade_database
from safwa.memory import MemoryFileStore


class ScriptedProvider:
    """Deterministic OpenAI-compatible boundary used by isolated E2E tests.

    A scripted response names the tools it calls, so the session it belongs to is known:
    when the next response asks for a tool this session was not offered, the model would
    have to `route` first, and so does this — with the scripted response left in place for
    the session that *can* run it.  Those synthetic hand-offs stay out of ``calls`` and
    ``options``, which record what the script itself saw.
    """

    def __init__(self, responses: list[str | CompletionTurn]) -> None:
        self.responses = deque(responses)
        self.calls: list[list[dict[str, object]]] = []
        self.options: list[dict[str, object]] = []

    async def complete(self, request: CompletionRequest) -> CompletionTurn:
        messages = request.messages
        offered = {tool["function"]["name"] for tool in request.tools}
        handover = self._handover(self.responses[0], offered) if self.responses else None
        if handover is not None:
            return handover
        if not self.responses:
            closing = self._closing_answer(messages, offered)
            if closing is not None:
                return closing
        self.calls.append([dict(message) for message in messages])
        self.options.append({"tools": list(request.tools), "tool_choice": request.tool_choice})
        if not self.responses:
            raise AssertionError("The advisor made an unexpected provider call")
        response = self.responses.popleft()
        return response if isinstance(response, CompletionTurn) else CompletionTurn(content=response)

    async def aclose(self) -> None:
        return None

    @staticmethod
    def _closing_answer(
        messages: list[dict[str, object]], offered: set[str]
    ) -> CompletionTurn | None:
        """The Advisor's last word when a script covers only the subagent's work.

        `route` returns to its caller, so every routed script would otherwise end with one
        more response repeating what the subagent already said.  The harness says it
        instead, in the subagent's own words, and stays out of ``calls`` and ``options``.
        """
        if "route" not in offered or not messages:
            return None
        last = messages[-1]
        if last.get("role") != "tool" or last.get("name") != "route":
            return None
        try:
            payload = json.loads(str(last.get("content") or "{}"))
        except json.JSONDecodeError:
            return None
        return CompletionTurn(content=str(payload.get("text") or ""))

    @staticmethod
    def _handover(response: str | CompletionTurn, offered: set[str]) -> CompletionTurn | None:
        if "route" not in offered or not isinstance(response, CompletionTurn):
            return None
        wanted = {call.name for call in response.tool_calls}
        if not wanted or wanted <= offered:
            return None
        target = "diary" if wanted & {"read_day", "diary"} else "board"
        return CompletionTurn(
            content="",
            tool_calls=(
                ToolCall(
                    id=f"route-{target}",
                    name="route",
                    arguments_json=json.dumps({"name": target}),
                ),
            ),
        )


@dataclass
class E2EHarness:
    sessions: async_sessionmaker[AsyncSession]
    database: Database
    database_path: Path
    memory: MemoryFileStore

    def board(self) -> RoutedSubagent:
        """The real board subagent: every mutation tool lives behind `route("board")`."""
        return RoutedSubagent(
            name="board",
            purpose="every change to the planning data",
            instructions=BOARD_PROMPT,
            read_tools=(query_read_tool(ReadOnlyQueryRunner(self.database_path)),),
            mutation_tools=BOARD_TOOLS,
            planning_state=True,
        )

    def advisor(
        self,
        responses: list[str | CompletionTurn],
        *,
        cache_breakpoints: bool = False,
        subagents: tuple[RoutedSubagent, ...] | None = None,
        autoapprove: bool = False,
    ) -> tuple[AIAdvisor, ScriptedProvider]:
        subagents = (self.board(),) if subagents is None else subagents
        provider = ScriptedProvider(responses)
        advisor = AIAdvisor(
            self.sessions,
            provider,
            self.memory,
            ReadOnlyQueryRunner(self.database_path),
            model_name="e2e-scripted-model",
            cache_breakpoints=cache_breakpoints,
            autoapproval=AutoApprovalReviewer(provider) if autoapprove else None,
            subagents=subagents,
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
