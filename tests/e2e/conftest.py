from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from functools import cache
from pathlib import Path

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from llm_gateway import CompletionRequest, CompletionTurn
from safwa.bootstrap.main import bootstrap_workspace
from safwa.bootstrap.modules import (
    AGENTS,
    AI_VIEWS,
    ALLOWED_VIEWS,
    HELPERS,
    HOOKS,
    MODULES,
    REGISTRY,
    SYSTEM_PROMPT,
    routed_prompt,
)
from safwa.features.advisor.agent import ADVISOR_ROW_LIMITS, ADVISOR_VIEWS
from safwa.features.memory.use_cases import MemoryReader
from safwa.features.workspace_mutator.state import workspace_context
from safwa.foundation.models import Base
from tg_agent_shell.ai.sql import ReadOnlyQueryRunner, create_ai_views
from tg_agent_shell.ai.subagents import RoutedSubagent
from tg_agent_shell.ai.tools import IMMEDIATE_TOOLS, HelperPort
from tg_agent_shell.foundation.database import Database, upgrade_database
from tg_agent_shell.hooks.contracts import HookSpec
from tg_agent_shell.hooks.registry import HookRegistry
from tg_agent_shell.proposals.hooks import PLAN_HOOK, REQUEST_REVIEW_HOOK
from tg_agent_shell.proposals.store import ProposalStore
from tg_agent_shell.registry import Registry
from tg_agent_shell.session import RootSession
from tg_agent_shell.telegram.manifest import AgentContext


@cache
def registry_with(checks: tuple[HookSpec, ...]) -> Registry:
    """Safwa's registry, with the checks on the model's work a test wants in place of its own.

    The feature toggles decide them for the running bot; a test decides them for itself, the
    way it decides autoapproval, because a script would otherwise answer a review in every
    turn it plays.
    """
    return replace(
        REGISTRY,
        hooks=HookRegistry.of(
            (*(spec for spec in HOOKS if spec not in (PLAN_HOOK, REQUEST_REVIEW_HOOK)), *checks),
            owners=frozenset(module.name for module in MODULES),
            helpers=frozenset(HELPERS),
            tools=IMMEDIATE_TOOLS - {"route"},
            policy=REGISTRY.hooks.policy,
        ),
    )


class ScriptedProvider:
    """Deterministic OpenAI-compatible boundary used by isolated E2E tests.

    The script is the whole of what the model says: one response per request, in order,
    with no transition supplied here.  A turn that needs a `route` says so, and a routed
    turn ends with the Advisor's own last word, because the run under test is the one the
    application makes and not one this class repaired.  A request the script does not
    answer fails the test rather than being answered anyway.
    """

    def __init__(self, responses: list[str | CompletionTurn]) -> None:
        self.responses = deque(responses)
        self.calls: list[list[dict[str, object]]] = []
        self.options: list[dict[str, object]] = []

    async def complete(self, request: CompletionRequest) -> CompletionTurn:
        self.calls.append([dict(message) for message in request.messages])
        self.options.append({"tools": list(request.tools), "tool_choice": request.tool_choice})
        if not self.responses:
            raise AssertionError("The advisor made an unexpected provider call")
        response = self.responses.popleft()
        return response if isinstance(response, CompletionTurn) else CompletionTurn(content=response)

    async def aclose(self) -> None:
        return None


TIMEZONE = "Europe/Istanbul"
OWNER_ID = 42


class SilentHistory:
    """The Telethon boundary for a subagent that never reads the conversation."""

    async def day_transcript(self, _chat_id: int, *, start, end, token_budget) -> str:  # noqa: ARG002
        return ""


@dataclass
class E2EHarness:
    sessions: async_sessionmaker[AsyncSession]
    database: Database
    database_path: Path
    memory: MemoryReader
    # One harness is one running bot: the reviews it opens outlive each advisor it builds.
    reviews: ProposalStore = field(default_factory=ProposalStore)

    def runner(self) -> ReadOnlyQueryRunner:
        """The application's one runner, which each reader is then scoped out of."""
        return ReadOnlyQueryRunner(self.database_path, ALLOWED_VIEWS, timezone=TIMEZONE)

    def subagent(self, name: str, *, history: object | None = None) -> RoutedSubagent:
        """One declared subagent, bound the way the composition root binds it.

        Its prompt, its views, its read tools and its clock all come out of the feature's
        own `AgentSpec`, so a test never states them: what the application runs is what
        answers here, and only Telethon is replaced.
        """
        spec = next(agent for agent in AGENTS if agent.name == name)
        return spec.bind(
            AgentContext(
                owner_id=OWNER_ID,
                timezone=TIMEZONE,
                query_runner=self.runner(),
                history=history or SilentHistory(),
                sessions=self.sessions,
            ),
            prompt=routed_prompt(spec),
        )

    def advisor(
        self,
        responses: list[str | CompletionTurn],
        *,
        cache_breakpoints: bool = False,
        subagents: tuple[RoutedSubagent, ...] | None = None,
        helpers: dict[str, object] | None = None,
        autoapprove: bool = False,
        plan_required: bool = True,
        request_review: bool = False,
        checks: tuple[HookSpec, ...] | None = None,
        provider_factory: Callable[[list[str | CompletionTurn]], ScriptedProvider] = ScriptedProvider,
    ) -> tuple[RootSession, ScriptedProvider]:
        subagents = (self.subagent("workspace_mutator"),) if subagents is None else subagents
        # A test about what happens *during* a turn needs the boundary to hold still, so
        # which scripted provider answers the script is the test's to say.
        provider = provider_factory(responses)
        # One harness is one running bot, so every advisor it builds shares its reviews,
        # and it is assembled through the same registry the composition root uses.
        if checks is None:
            checks = (PLAN_HOOK,) * plan_required + (REQUEST_REVIEW_HOOK,) * request_review
        registry = registry_with(checks)
        advisor = registry.root_session(
            self.sessions,
            provider,
            self.memory,
            self.runner(),
            views=ADVISOR_VIEWS,
            row_limits=ADVISOR_ROW_LIMITS,
            workspace_state=workspace_context,
            system_prompt=SYSTEM_PROMPT,
            model_name="e2e-scripted-model",
            cache_breakpoints=cache_breakpoints,
            autoapprove=autoapprove,
            subagents=subagents,
            # A test replaces what a helper does, never how the feature declared it.
            helpers={
                name: HelperPort(
                    run=run,
                )
                for name, run in (helpers or {}).items()
            },
            reviews=self.reviews,
        )
        return advisor, provider

    def analyzer(self, provider) -> dict[str, object]:
        """The real heavy analyzer, scoped to the views it declared like every reader."""
        runner = self.runner()
        return {
            name: spec.build(provider, runner.scoped(spec.views), prompt=spec.instructions)
            for name, spec in HELPERS.items()
        }


@pytest_asyncio.fixture
async def e2e_harness(tmp_path: Path, monkeypatch) -> E2EHarness:
    """Real migrated SQLite plus real services; only remote APIs are replaced."""

    repository_root = Path(__file__).parents[2]
    monkeypatch.chdir(repository_root)
    database_path = tmp_path / "safwa-e2e.db"
    upgrade_database(f"sqlite:///{database_path.as_posix()}", Base.metadata)
    database = Database(f"sqlite+aiosqlite:///{database_path.as_posix()}")
    async with database.sessions() as session:
        await bootstrap_workspace(session, 42, TIMEZONE)
        await session.run_sync(
            lambda sync_session: create_ai_views(sync_session.connection(), AI_VIEWS)
        )
        await session.commit()

    harness = E2EHarness(
        database.sessions, database, database_path, MemoryReader(database.sessions)
    )
    try:
        yield harness
    finally:
        await database.dispose()
