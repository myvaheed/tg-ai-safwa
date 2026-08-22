"""What one Safwa feature plugs into one application.

This is a wiring DTO of the outermost layer, not a domain contract. It may know aiogram
and SQLAlchemy, and nothing that expresses a business rule imports it. `modules.py` is
the only place that lists the features themselves.

A new capability does not get a field here by default: it first gets its own mechanism,
and only a capability several features plug into earns a contribution.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..ai.mini import ReadToolSpec
from ..ai.sql import ReadOnlyQueryRunner, SqlView
from ..config import Settings
from ..features.proposals.api import (
    MutationToolSpec,
    ProposalHandler,
    ProposalPresenter,
)
from ..history import TelegramHistorySource
from ..telegram import Services


@dataclass(frozen=True, slots=True)
class AgentContext:
    """What a feature binds its agent's read tools against in this application."""

    settings: Settings
    query_runner: ReadOnlyQueryRunner
    history: TelegramHistorySource


@dataclass(frozen=True, slots=True)
class AgentSpec:
    """One subagent `route(name)` can hand the turn to."""

    name: str
    # The line the Advisor's prompt carries under `route("<name>")`.
    purpose: str
    instructions: str
    mutation_tools: tuple[str, ...] = ()
    board_state: bool = False
    read_tools: Callable[[AgentContext], tuple[ReadToolSpec, ...]] | None = None
    clock: Callable[[AgentContext], Callable[[], str]] | None = None


@dataclass(frozen=True, slots=True)
class BackgroundContext:
    """What a long-running feature task is given once the application is built.

    Nothing here names one feature: a task that needs its feature's own objects takes
    them off `services`, the container the whole application already shares. A field per
    feature would rebuild the registry `MODULES` exists to remove.
    """

    settings: Settings
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
    """Binds the three proposal responsibilities of one entity, only here."""

    handler: ProposalHandler
    tool: MutationToolSpec
    presenter: ProposalPresenter


@dataclass(frozen=True, slots=True)
class FeatureModule:
    """Everything a Safwa feature plugs into this application."""

    name: str

    # AI
    agents: tuple[AgentSpec, ...] = ()
    proposals: tuple[ProposalContribution, ...] = ()
    # A mutation tool whose change lands on an entity another feature owns.
    mutation_tools: tuple[MutationToolSpec, ...] = ()

    # Data
    views: tuple[SqlView, ...] = ()

    # Lifecycle
    recover: Callable[[AsyncSession], Awaitable[None]] | None = None
    background: tuple[BackgroundTask, ...] = ()
