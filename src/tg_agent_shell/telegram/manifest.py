"""What one feature plugs into one application.

This is a wiring DTO of the outermost layer, not a domain contract. It may know aiogram
and SQLAlchemy, and nothing that expresses a business rule imports it. The application's
registry is the only place that lists the features themselves.

A new capability does not get a field here by default: it first gets its own mechanism,
and only a capability several features plug into earns a contribution.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..adapters.telegram_history import TelegramHistorySource
from ..ai.autoapproval import AutoApprovalRule
from ..ai.mini import ReadToolSpec
from ..ai.sql import ReadOnlyQueryRunner, SqlView
from ..ai.subagents import RoutedSubagent
from ..ai.tools import Helper
from ..foundation.screens import ScreenCommand, ScreenSpec, StartLink, TextInputFlow
from ..proposals.api import (
    MutationToolSpec,
    ProposalHandler,
    ProposalPresenter,
)
from .services import Services


@dataclass(frozen=True, slots=True)
class AgentContext:
    """What a feature binds its agent's read tools against in this application."""

    owner_id: int
    timezone: str
    query_runner: ReadOnlyQueryRunner
    history: TelegramHistorySource


@dataclass(frozen=True, slots=True)
class AgentSpec:
    """One subagent as a feature declares it, before an application binds it."""

    name: str
    # The line the Advisor's prompt carries under `route("<name>")`.
    purpose: str
    instructions: str
    mutation_tools: tuple[str, ...] = ()
    # The views this subagent is told about, filled into `{views}` in its instructions.
    # A prompt that spells its own out leaves this empty and keeps what it wrote.
    views: tuple[str, ...] = ()
    workspace_state: bool = False
    read_tools: Callable[[AgentContext], tuple[ReadToolSpec, ...]] | None = None
    clock: Callable[[AgentContext], Callable[[], str]] | None = None

    def bind(self, context: AgentContext, *, prompt: str) -> RoutedSubagent:
        """The session this declaration runs as here: its read tools and its clock, bound."""
        return RoutedSubagent(
            name=self.name,
            prompt=prompt,
            read_tools=self.read_tools(context) if self.read_tools else (),
            mutation_tools=self.mutation_tools,
            workspace_state=self.workspace_state,
            clock=self.clock(context) if self.clock else None,
        )


@dataclass(frozen=True, slots=True)
class HelperSpec:
    """One helper `call_helper(name)` runs: a mini session, never handed the turn."""

    name: str
    instructions: str
    # Bound to this application's provider and query runner, with the prompt already filled.
    build: Callable[..., Helper]
    # The views this helper is told about, filled into `{views}` in its instructions.
    views: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BackgroundContext:
    """What a long-running feature task is given once the application is built.

    Nothing here names one feature: a task that needs its feature's own objects takes
    them off `services`, the container the whole application already shares. A field per
    feature would rebuild the registry `MODULES` exists to remove.
    """

    owner_id: int
    timezone: str
    scheduler_enabled: bool
    poll_seconds: float
    sessions: async_sessionmaker[AsyncSession]
    bot: Bot
    services: Services


@dataclass(frozen=True, slots=True)
class BackgroundTask:
    """One task the composition root starts and cancels with the polling loop."""

    name: str
    run: Callable[[BackgroundContext], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class ProposalContribution:
    """Binds the proposal responsibilities of one entity, only here."""

    handler: ProposalHandler
    tool: MutationToolSpec
    presenter: ProposalPresenter
    # Which of this entity's actions may be saved without the owner seeing the screen,
    # by action. Anything absent takes the review screen.
    autoapprovals: Mapping[str, AutoApprovalRule] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class FeatureModule:
    """Everything a feature plugs into this application."""

    name: str

    # AI
    agents: tuple[AgentSpec, ...] = ()
    # A helper the root session calls instead of handing the turn to a subagent.
    helpers: tuple[HelperSpec, ...] = ()
    proposals: tuple[ProposalContribution, ...] = ()
    # A mutation tool whose change lands on an entity another feature owns.
    mutation_tools: tuple[MutationToolSpec, ...] = ()

    # Data
    views: tuple[SqlView, ...] = ()

    # Telegram
    screens: tuple[ScreenSpec, ...] = ()
    commands: tuple[ScreenCommand, ...] = ()
    # The inline buttons this feature's screens draw, by the action their token carries.
    callback_actions: Mapping[str, Callable[..., Awaitable[None]]] = field(
        default_factory=dict
    )
    text_inputs: tuple[TextInputFlow, ...] = ()
    start_links: tuple[StartLink, ...] = ()

    # Lifecycle
    recover: Callable[[AsyncSession], Awaitable[None]] | None = None
    background: tuple[BackgroundTask, ...] = ()
