"""Events are facts; subscriptions select them; effects describe the permitted work.

Only the event boundaries with real consumers are implemented. Checks receive no session
or delivery objects. Run handlers receive a publication port and the application's
resources, under the lease the event adapter owns. Advise keeps what a check returned as
the hook's one pending request and asks the feature for the words just before they are
said, so what is said is what is still there.

A hook with a switch is the owner's to turn off; whether it is on is the application's
policy, read where the hook is about to work. A hook without one is always on.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

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
class OnAfterTurn:
    source: Literal["owner", "system"] = "owner"

    def matches(self, event: AfterTurn) -> bool:
        return event.source == self.source


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


@dataclass(frozen=True, slots=True)
class RunContext[Resources]:
    resources: Resources
    still_current: Callable[[], bool]
    # Text is plain text. The adapter escapes and registers every publication.
    publish: Callable[[str, str], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class Run[Payload]:
    run: Callable[[Payload, RunContext], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class OfferTool:
    helper: str


@dataclass(frozen=True, slots=True)
class Advise[Item]:
    """One pending request to the Advisor per hook, worded when it is about to be said.

    A check returns items — references, not words. They are merged into the hook's pending
    request; `prepare` reads what they refer to and returns the request text, or None when
    nothing is left to ask about.
    """

    prepare: Callable[[AsyncSession, Sequence[Item]], Awaitable[str | None]]


@dataclass(frozen=True, slots=True)
class HookSwitch:
    """What the owner reads on the settings screen that turns the hook off and on."""

    title: str
    description: str


@dataclass(frozen=True, slots=True)
class HookSpec[Event, Payload]:
    name: str
    owner: str
    on: tuple[OnAfterTurn | OnAfterTool | OnCommitted, ...]
    evaluate: Callable[[Event], Awaitable[Sequence[Payload]]]
    # OfferTool checks return the notice the model reads. Run checks return operation data.
    # Advise checks return the items the request will be about.
    effect: Run[Payload] | OfferTool | Advise[Payload]
    switch: HookSwitch | None = None


# Whether the hook of that name is on, asked only for a hook that has a switch.
HookPolicy = Callable[[AsyncSession, str], Awaitable[bool]]


async def every_switch_on(session: AsyncSession, name: str) -> bool:
    """The policy of an application with no settings of its own."""
    return True
