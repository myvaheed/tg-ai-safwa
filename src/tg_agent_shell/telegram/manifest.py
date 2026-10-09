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
from functools import partial

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telegram_llm import ChatHost

from ..ai.autoapproval import AutoApprovalRule
from ..ai.mini import ReadToolSpec
from ..ai.sql import ReadOnlyQueryRunner, SqlView
from ..ai.subagents import SUBAGENT_HISTORY_LAST_MESSAGES, RoutedSubagent
from ..ai.tools import Helper
from ..foundation.screens import ScreenSpec
from ..history import TelegramHistorySource
from ..hooks.contracts import HookSpec
from ..media.library import MediaLibrary
from ..proposals.api import (
    MutationToolSpec,
    ProposalHandler,
    ProposalPresenter,
    SimilarItems,
)
from .contributions import ScreenCommand, StartLink, TextInputFlow


@dataclass(frozen=True, slots=True)
class AgentContext:
    """What a feature binds its agent's read tools against in this application."""

    owner_id: int
    timezone: str
    query_runner: ReadOnlyQueryRunner
    history: TelegramHistorySource
    sessions: async_sessionmaker[AsyncSession]
    # None where the application takes no photos: a tool that reads one is not handed out.
    media: MediaLibrary | None = None
    # The automatic reactions the owner switches, as the application's registry lists them.
    switches: tuple[HookSpec, ...] = ()
    # The owner's chat, for a read tool that sends pictures there itself; None where no tool
    # does, and a tool that would is not handed out.
    chat: ChatHost | None = None
    bot: Bot | None = None


@dataclass(frozen=True, slots=True)
class AgentSpec:
    """One subagent as a feature declares it, before an application binds it."""

    name: str
    # The line the Advisor's prompt carries under `route("<name>")`.
    purpose: str
    instructions: str
    mutation_tools: tuple[str, ...] = ()
    # The views this subagent may read. They are filled into `{views}` in its instructions
    # when it carries that placeholder; a prompt that spells its own out keeps what it
    # wrote, and this list is still what its reads are refused against. A subagent that
    # declares none reads nothing, and is not handed `query_data`.
    views: tuple[str, ...] = ()
    workspace_state: bool = False
    read_tools: Callable[[AgentContext], tuple[ReadToolSpec, ...]] | None = None
    # Its own current values, read again at every step: they follow the dialogue, so the
    # prompt above it stays byte-stable.
    current: Callable[[AgentContext], Awaitable[str]] | None = None
    # The item types it may put on the screen with `open`; none, and it is not handed the tool.
    opens: tuple[str, ...] = ()
    # How many of the conversation's newest messages it reads.
    history_messages: int = SUBAGENT_HISTORY_LAST_MESSAGES
    # It answers questions as well as doing work, so its first step may be words.
    answers_questions: bool = False

    def bind(self, context: AgentContext, *, prompt: str) -> RoutedSubagent:
        """The session this declaration runs as here: its reads, its scope and its current
        values."""
        return RoutedSubagent(
            name=self.name,
            prompt=prompt,
            query_runner=context.query_runner.scoped(self.views),
            read_tools=self.read_tools(context) if self.read_tools else (),
            mutation_tools=self.mutation_tools,
            workspace_state=self.workspace_state,
            current=partial(self.current, context) if self.current else None,
            opens=self.opens,
            history_messages=self.history_messages,
            answers_questions=self.answers_questions,
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
class ProposalContribution:
    """Binds the proposal responsibilities of one entity, only here."""

    handler: ProposalHandler
    tool: MutationToolSpec
    presenter: ProposalPresenter
    # Which of this entity's actions may be saved without the owner seeing the screen,
    # by action. Anything absent takes the review screen.
    autoapprovals: Mapping[str, AutoApprovalRule] = field(default_factory=dict)
    # What a new item is compared with before the owner saves it, or nothing.
    similar: SimilarItems | None = None


@dataclass(frozen=True, slots=True)
class FeatureModule:
    """Everything a feature plugs into this application."""

    name: str

    # AI
    agents: tuple[AgentSpec, ...] = ()
    # A helper the root session calls instead of handing the turn to a subagent.
    helpers: tuple[HelperSpec, ...] = ()
    proposals: tuple[ProposalContribution, ...] = ()
    # A mutation tool whose change lands on an entity another feature owns, or a second tool
    # for an entity this feature owns.
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
