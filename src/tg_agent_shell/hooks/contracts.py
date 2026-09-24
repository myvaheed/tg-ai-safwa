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
read where the hook is about to work. A hook that runs work of its own is always on, unless it
names another hook as its switch: then it is on exactly when that one is. A check on the
model's own work — a response sent back, an answer held — has no switch either: the
application registers it or leaves it out.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import time
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from llm_gateway import LlmProvider

from ..foundation.changes import Committed


@dataclass(frozen=True, slots=True)
class AfterTurn:
    owner_id: int
    chat_id: int
    source_message_id: int
    dialogue_revision: int
    source: Literal["owner", "system"] = "owner"


@dataclass(frozen=True, slots=True)
class BeforeTurn:
    """A turn has read its dialogue and has not asked the model yet."""

    owner_id: int
    chat_id: int
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
class ProposedCall:
    """One call of a subagent response, validated and not prepared yet."""

    call_id: str
    tool: str
    entity: str
    action: str
    entity_id: int | None
    values: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class BeforeProposals:
    """A subagent response carried calls that would become proposals, and none is prepared.

    `text` is the words of that response, where a subagent writes its plan.
    """

    run_id: int
    agent_kind: str
    text: str
    calls: tuple[ProposedCall, ...]


@dataclass(frozen=True, slots=True)
class AfterRequest:
    """Safwa is about to answer the owner's message in words, after every screen of the
    request. `done` is each change the request made, with what became of it."""

    run_id: int
    conversation: str
    done: tuple[str, ...]
    answer: str


@dataclass(frozen=True, slots=True)
class OnBeforeProposals:
    """Every subagent response that carries calls."""

    def matches(self, event: BeforeProposals) -> bool:
        return True


@dataclass(frozen=True, slots=True)
class OnAfterRequest:
    """Every answer to a message of the owner's, once per request."""

    def matches(self, event: AfterRequest) -> bool:
        return True


@dataclass(frozen=True, slots=True)
class OnAfterTurn:
    source: Literal["owner", "system"] = "owner"

    def matches(self, event: AfterTurn) -> bool:
        return event.source == self.source


@dataclass(frozen=True, slots=True)
class OnBeforeTurn:
    """Every turn, whoever started it; `source` is on the event for a hook that needs it."""

    def matches(self, event: BeforeTurn) -> bool:
        return True


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
class ReturnProposals:
    """Before a response's calls are prepared: the check's words send every one of them back
    to the subagent, refused with `code` and those words, and the session runs again. The
    first hook in the catalogue that answers decides.

    A check that fails, or whose answer is not words, is not a pass: the turn ends there,
    and no call of that response is prepared."""

    code: str


@dataclass(frozen=True, slots=True)
class HoldAnswer[Payload]:
    """Before the answer to the owner's message is sent: `review` reads what the check
    returned, with the model to read it by, and gives words or None. Words hold the answer
    back and the same request goes on with them; its next answer is not held. A review that
    fails lets the answer through."""

    review: Callable[[Payload, LlmProvider], Awaitable[str | None]]


@dataclass(frozen=True, slots=True)
class HookSpec[Event, Payload]:
    name: str
    owner: str
    on: tuple[
        OnBeforeTurn
        | OnAfterTurn
        | OnAfterTool
        | OnBeforeTool
        | OnBeforeProposals
        | OnAfterRequest
        | OnCommitted
        | OnTick,
        ...,
    ]
    evaluate: Callable[[Event], Awaitable[Sequence[Payload]]]
    # OfferTool, RefuseTool and ReturnProposals checks return the words the model reads. Run
    # checks return operation data, HoldAnswer checks what its review reads. Advise checks
    # return the items the request will be about.
    effect: (
        Run[Payload]
        | OfferTool
        | RefuseTool
        | Advise[Payload]
        | ReturnProposals
        | HoldAnswer[Payload]
    )
    # What the owner reads about the hook: on the settings screen when it is theirs to
    # switch, and in the feature map either way.
    title: str
    description: str
    # The hook whose switch turns this one on and off, when it has none of its own.
    switch: str | None = None

    @property
    def agent_related(self) -> bool:
        """Whether the effect hands the agent something of the owner's — a helper, a refusal,
        a request — which is what makes the hook the owner's to switch. A check on the model's
        own work is not: it is registered or left out."""
        return isinstance(self.effect, OfferTool | RefuseTool | Advise)


# Whether the hook of that name is on, asked only for an agent-related hook.
HookPolicy = Callable[[AsyncSession, str], Awaitable[bool]]


async def every_switch_on(session: AsyncSession, name: str) -> bool:
    """The policy of an application with no settings of its own."""
    return True
