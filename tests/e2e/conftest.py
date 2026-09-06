from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from llm_gateway import CompletionRequest, CompletionTurn, ToolCall
from safwa.bootstrap.main import bootstrap_workspace
from safwa.bootstrap.modules import (
    AGENTS,
    AI_VIEWS,
    ALLOWED_VIEWS,
    AUTOAPPROVALS,
    HELPERS,
    PROPOSALS,
    SCREENS,
    SYSTEM_PROMPT,
    routed_prompt,
)
from safwa.features.memory.store import MemoryFileStore
from safwa.features.workspace_mutator.agent import MUTATOR_TOOLS
from safwa.features.workspace_mutator.state import workspace_context
from safwa.foundation.database import Database, upgrade_database
from tg_agent_shell.ai.autoapproval import AutoApprovalReviewer
from tg_agent_shell.ai.sql import ReadOnlyQueryRunner, create_ai_views
from tg_agent_shell.ai.subagents import RoutedSubagent
from tg_agent_shell.ai.tools import HelperPort
from tg_agent_shell.proposals.store import ProposalStore
from tg_agent_shell.session import RootSession


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
        # Every request the application made, synthetic hand-offs included. A turn that runs
        # twice is invisible in ``calls`` — the harness answers the repeat itself — and shows
        # up here.
        self.total = 0

    async def complete(self, request: CompletionRequest) -> CompletionTurn:
        self.total += 1
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
        if not wanted or wanted <= offered or "call_helper" in wanted:
            return None
        target = "diary" if wanted & {"read_day", "diary"} else "workspace_mutator"
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


TIMEZONE = "Europe/Istanbul"


@dataclass
class E2EHarness:
    sessions: async_sessionmaker[AsyncSession]
    database: Database
    database_path: Path
    memory: MemoryFileStore
    # One harness is one running bot: the reviews it opens outlive each advisor it builds.
    reviews: ProposalStore = field(default_factory=ProposalStore)

    def workspace(self) -> RoutedSubagent:
        """The real workspace mutator: every mutation tool lives behind `route("workspace_mutator")`."""
        return RoutedSubagent(
            name="workspace_mutator",
            # The instructions as assembled, `{views}` filled in: what the application runs.
            prompt=next(
                routed_prompt(agent) for agent in AGENTS if agent.name == "workspace_mutator"
            ),
            # No read tool of its own: `query_data` is published by the adapters.
            mutation_tools=MUTATOR_TOOLS,
            workspace_state=True,
        )

    def advisor(
        self,
        responses: list[str | CompletionTurn],
        *,
        cache_breakpoints: bool = False,
        subagents: tuple[RoutedSubagent, ...] | None = None,
        helpers: dict[str, object] | None = None,
        autoapprove: bool = False,
    ) -> tuple[RootSession, ScriptedProvider]:
        subagents = (self.workspace(),) if subagents is None else subagents
        provider = ScriptedProvider(responses)
        # One harness is one running bot, so every advisor it builds shares its reviews.
        advisor = RootSession(
            self.sessions,
            provider,
            self.memory,
            ReadOnlyQueryRunner(self.database_path, ALLOWED_VIEWS, timezone=TIMEZONE),
            PROPOSALS,
            screens=SCREENS,
            workspace_state=workspace_context,
            system_prompt=SYSTEM_PROMPT,
            model_name="e2e-scripted-model",
            cache_breakpoints=cache_breakpoints,
            autoapproval=AutoApprovalReviewer(provider, AUTOAPPROVALS) if autoapprove else None,
            subagents=subagents,
            # A test replaces what a helper does, never how the feature declared it.
            helpers={
                name: HelperPort(
                    run=run,
                    offer_when=HELPERS[name].offer_when,
                    offer=HELPERS[name].offer,
                )
                for name, run in (helpers or {}).items()
            },
            reviews=self.reviews,
        )
        return advisor, provider

    def analyzer(self, provider) -> dict[str, object]:
        """The real heavy analyzer, reading the real views through the same runner."""
        runner = ReadOnlyQueryRunner(self.database_path, ALLOWED_VIEWS, timezone=TIMEZONE)
        return {
            name: spec.build(provider, runner, prompt=spec.instructions)
            for name, spec in HELPERS.items()
        }


@pytest_asyncio.fixture
async def e2e_harness(tmp_path: Path, monkeypatch) -> E2EHarness:
    """Real migrated SQLite plus real services; only remote APIs are replaced."""

    repository_root = Path(__file__).parents[2]
    monkeypatch.chdir(repository_root)
    database_path = tmp_path / "safwa-e2e.db"
    upgrade_database(f"sqlite:///{database_path.as_posix()}")
    database = Database(f"sqlite+aiosqlite:///{database_path.as_posix()}")
    async with database.sessions() as session:
        await bootstrap_workspace(session, 42, TIMEZONE)
        await session.run_sync(
            lambda sync_session: create_ai_views(sync_session.connection(), AI_VIEWS)
        )
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
