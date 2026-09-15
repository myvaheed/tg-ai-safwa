"""Events are facts; subscriptions select them; effects describe the permitted work.

Only the two event boundaries with real consumers are implemented. Checks receive no
session or delivery objects. Run handlers receive a publication port and the application's
resources, under the lease the event adapter owns.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal


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
class HookSpec[Event, Payload]:
    name: str
    owner: str
    on: tuple[OnAfterTurn | OnAfterTool, ...]
    evaluate: Callable[[Event], Awaitable[Sequence[Payload]]]
    # OfferTool checks return the notice the model reads. Run checks return operation data.
    effect: Run[Payload] | OfferTool


@dataclass(frozen=True, slots=True)
class HookRegistration:
    spec: HookSpec
    enabled: bool = True
