"""Events are facts; subscriptions select them; effects describe the permitted work.

Only the event boundaries with real consumers are implemented. Checks receive no session
or delivery objects. Run handlers receive the application's resources and a session
factory, and around a turn and on a tick a publication port too: around a turn under the
lease the event adapter owns, on a tick while the chat is free and the owner has not acted
since the look. On a commit and at the start there is no chat, and words go through an
Advise hook.
Advise keeps what a check returned as
the hook's one pending request and asks the feature for the words just before they are
said, so what is said is what is still there.

A hook whose effect reaches the agent — a helper offered to its session, a call refused, a
request handed to the Advisor — is the owner's to turn off; whether it is on is the application's policy,
read where the hook is about to work. A hook that runs work of its own is always on, unless it
names another hook as its switch: then it is on exactly when that one is. A check on the
model's own work — a response sent back, an answer held, a proposal saved unseen — has no
switch either: the application registers it or leaves it out.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, time, timedelta
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
class SessionRead:
    """One call of the session's own read tools, and what came back, as the model read it."""

    tool: str
    result: str


@dataclass(frozen=True, slots=True)
class BeforeProposals:
    """A subagent response carried calls that would become proposals, and none is prepared.

    `text` is the words of that response, where a subagent writes its plan. `reads` is what
    the session read with its own read tools before it, oldest first.
    """

    run_id: int
    agent_kind: str
    text: str
    calls: tuple[ProposedCall, ...]
    reads: tuple[SessionRead, ...] = ()


@dataclass(frozen=True, slots=True)
class AfterRequest:
    """Safwa is about to answer the owner's message in words, after every screen of the
    request. `done` is each change the request made, with what became of it."""

    run_id: int
    conversation: str
    done: tuple[str, ...]
    answer: str


@dataclass(frozen=True, slots=True)
class ReviewedChange:
    """One change of a proposal waiting for its screen.

    `criterion` is how the owning feature said a change of this action is to be judged when it
    is saved unseen; None when the feature lists no such action, or the change sets a field
    that action may not.
    """

    entity: str
    action: str
    entity_id: int | None
    values: Mapping[str, Any]
    criterion: str | None


@dataclass(frozen=True, slots=True)
class BeforeReview:
    """A proposal is at the head of its queue, and its screen is not drawn yet. `request` is
    the owner's words that started it; `summary` and `fields` are how its screen reads."""

    proposal_id: int
    request: str
    changes: tuple[ReviewedChange, ...]
    summary: str
    fields: tuple[str, ...]


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
class OnBeforeReview:
    """Every proposal that reaches the head of its queue, before its screen is drawn."""

    def matches(self, event: BeforeReview) -> bool:
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


# How often a check may ask to run every so long: no more often than the tick poll looks,
# and at least once a day.
TICK_EVERY_MIN = timedelta(seconds=30)
TICK_EVERY_MAX = timedelta(hours=24)


@dataclass(frozen=True, slots=True)
class ChatState:
    """The owner's chat as one look saw it."""

    # When the owner last wrote, spoke, sent a photo or pressed a button.
    owner_acted_at: datetime
    # No turn, no review waiting, no session claimed: a message could go now.
    free: bool
    # The kind and time of the newest message kept in it; None while it keeps nothing.
    newest_kind: str | None = None
    newest_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class Tick:
    """A daily time passed, or an interval ran out, since the last look.

    `at` is the local time as "HH:MM", which is all a daily check has to keep. `clock` is the
    reader that named a daily time, `every` the interval that ran out, and `chat` the owner's
    chat at this look, where the application has one.
    """

    at: str
    clock: TickTime | None = None
    every: timedelta | None = None
    chat: ChatState | None = None


@dataclass(frozen=True, slots=True)
class OnTick:
    """A check once a day, at the local time the reader `at` names; or every `every`, from
    `TICK_EVERY_MIN` to `TICK_EVERY_MAX`, counted from the last time it ran."""

    at: TickTime | None = None
    every: timedelta | None = None

    def matches(self, event: Tick) -> bool:
        if self.every is not None:
            return event.every == self.every
        return event.clock is self.at


@dataclass(frozen=True, slots=True)
class Started:
    """The application has started: the shell has reconciled what the restart left, and no
    message has been taken yet."""


@dataclass(frozen=True, slots=True)
class OnStarted:
    def matches(self, event: Started) -> bool:
        return True


@dataclass(frozen=True, slots=True)
class RunContext[Resources]:
    resources: Resources
    still_current: Callable[[], bool]
    # Text is plain text, and the adapter escapes and registers every publication; on a tick,
    # a Home dashboard's is Markdown with citations, as an answer is.
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
class Shown:
    """A request that carries a block its feature wrote. The block opens the message the
    Advisor writes, as it is; the Advisor reads `request`, and is told the block was shown."""

    block: str
    request: str


@dataclass(frozen=True, slots=True)
class Advise[Item]:
    """One request to the Advisor per hook, worded when it is about to be said.

    A check returns items — references, not words. Each check writes them down as one row;
    a turn words every row of the hook together, and `prepare` reads what the items refer
    to and returns the request text, a `Shown`, or None when nothing is left to ask about.
    """

    prepare: Callable[[AsyncSession, Sequence[Item]], Awaitable[str | Shown | None]]


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
class SaveProposal[Payload]:
    """Before a proposal's screen is drawn: `review` reads what the check returned, with the
    model to read it by, and gives the reason to save it or None. A reason saves it with no
    screen, the way the owner's Save would, and the next in the queue is read the same way.
    None, or a review that fails, draws the screen."""

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
        | OnBeforeReview
        | OnCommitted
        | OnTick
        | OnStarted,
        ...,
    ]
    evaluate: Callable[[Event], Awaitable[Sequence[Payload]]]
    # OfferTool, RefuseTool and ReturnProposals checks return the words the model reads. Run
    # checks return operation data, HoldAnswer and SaveProposal checks what their review
    # reads. Advise checks return the items the request will be about.
    effect: (
        Run[Payload]
        | OfferTool
        | RefuseTool
        | Advise[Payload]
        | ReturnProposals
        | HoldAnswer[Payload]
        | SaveProposal[Payload]
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
