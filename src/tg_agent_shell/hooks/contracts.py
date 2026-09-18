"""Events are facts; subscriptions select them; effects describe the permitted work.

Only the event boundaries with real consumers are implemented. Checks receive no session
or delivery objects. Run handlers receive the application's resources and a session
factory, and after a turn a publication port too, under the lease the event adapter owns;
on a tick there is no chat, and words go through a recorded change and an Advise hook.
Advise keeps what a check returned as
the hook's one pending request and asks the feature for the words just before they are
said, so what is said is what is still there.

A hook whose effect reaches the agent — a helper offered to its session, a call refused, a
request handed to the Advisor — is the owner's to turn off; whether it is on is the application's policy,
read where the hook is about to work. A hook that runs work of its own is always on.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import time
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..foundation.changes import Committed


@dataclass(frozen=True, slots=True)
class AfterTurn:
    owner_id: int
    chat_id: int
    source_message_id: int
    dialogue_revision: int
    source: Literal["owner", "system"] = "owner"


@dataclass(frozen=True, slots=True)
class AfterTool:
    run_id: int
    agent: Literal["root", "subagent"]
    agent_kind: str
    tool: str
    call_id: str
    arguments_json: str
    result: Any
    outcome: Literal["success", "error", "prepared"]


@dataclass(frozen=True, slots=True)
class BeforeTool:
    """The model called a tool and it has not run yet."""

    run_id: int
    agent: Literal["root", "subagent"]
    agent_kind: str
    tool: str
    call_id: str
    arguments_json: str


@dataclass(frozen=True, slots=True)
class OnAfterTurn:
    source: Literal["owner", "system"] = "owner"

    def matches(self, event: AfterTurn) -> bool:
        return event.source == self.source


@dataclass(frozen=True, slots=True)
class OnBeforeTool:
    tool: str
    agent: Literal["root", "subagent"] = "root"

    def matches(self, event: BeforeTool) -> bool:
        return (event.tool, event.agent) == (self.tool, self.agent)


@dataclass(frozen=True, slots=True)
class OnAfterTool:
    tool: str
    agent: Literal["root", "subagent"] = "root"
    outcome: Literal["success", "error", "prepared"] = "success"

    def matches(self, event: AfterTool) -> bool:
        return (event.tool, event.agent, event.outcome) == (
            self.tool, self.agent, self.outcome
        )


@dataclass(frozen=True, slots=True)
class OnCommitted:
    kind: str

    def matches(self, event: Committed) -> bool:
        return event.kind == self.kind


# A local time of day by the workspace's clock, read at every look, so a time the owner
# moves counts without a restart. The reader is the identity of the daily time: two hooks
# that name the same reader share one Tick.
TickTime = Callable[[AsyncSession], Awaitable[time]]


@dataclass(frozen=True, slots=True)
class Tick:
    """The workspace's clock passed a daily time since the last look. `at` is that time
    as "HH:MM", which is all a daily check has to keep; `clock` is the reader that named it."""

    at: str
    clock: TickTime


@dataclass(frozen=True, slots=True)
class OnTick:
    """A check once a day, at the local time the reader `at` names."""

    at: TickTime

    def matches(self, event: Tick) -> bool:
        return event.clock is self.at


@dataclass(frozen=True, slots=True)
class RunContext[Resources]:
    resources: Resources
    still_current: Callable[[], bool]
    # Text is plain text. The adapter escapes and registers every publication.
    publish: Callable[[str, str], Awaitable[None]]
    sessions: async_sessionmaker[AsyncSession]


@dataclass(frozen=True, slots=True)
class Run[Payload]:
    run: Callable[[Payload, RunContext], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class OfferTool:
    """After a tool ran: the model reads its result with the check's notice appended, and
    the named helper is on the session's tool list from the next model call on. Nothing
    makes the model call it."""

    helper: str


@dataclass(frozen=True, slots=True)
class RefuseTool:
    """Before a tool runs: it does not run, the check's notice is what the model reads as
    the call's result, and the named helper is on the session's tool list from the next
    model call on. The model answers with something else — the helper, or a different call.
    The refusal stands in a session the helper cannot be granted to.

    A check that fails, or whose notice is not words, is not a pass: the turn ends there,
    and the call is not run."""

    helper: str


@dataclass(frozen=True, slots=True)
class Advise[Item]:
    """One request to the Advisor per hook, worded when it is about to be said.

    A check returns items — references, not words. Each check writes them down as one row;
    a turn words every row of the hook together, and `prepare` reads what the items refer
    to and returns the request text, or None when nothing is left to ask about.
    """

    prepare: Callable[[AsyncSession, Sequence[Item]], Awaitable[str | None]]


@dataclass(frozen=True, slots=True)
class HookSpec[Event, Payload]:
    name: str
    owner: str
    on: tuple[OnAfterTurn | OnAfterTool | OnBeforeTool | OnCommitted | OnTick, ...]
    evaluate: Callable[[Event], Awaitable[Sequence[Payload]]]
    # OfferTool and RefuseTool checks return the notice the model reads. Run checks return
    # operation data. Advise checks return the items the request will be about.
    effect: Run[Payload] | OfferTool | RefuseTool | Advise[Payload]
    # What the owner reads about the hook: on the settings screen when it is theirs to
    # switch, and in the feature map either way.
    title: str
    description: str

    @property
    def agent_related(self) -> bool:
        """Whether the effect reaches the agent, which is what makes the hook the owner's to switch."""
        return isinstance(self.effect, OfferTool | RefuseTool | Advise)


# Whether the hook of that name is on, asked only for an agent-related hook.
HookPolicy = Callable[[AsyncSession, str], Awaitable[bool]]


async def every_switch_on(session: AsyncSession, name: str) -> bool:
    """The policy of an application with no settings of its own."""
    return True
