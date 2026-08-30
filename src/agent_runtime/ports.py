"""What the runtime asks of the application around it.

Four questions, and the runtime asks nothing else. Where does a session's state live
(`SessionStore`); what may this kind of session call and what happens when it does
(`ToolRunner`); what does it read before its own steps (`ContextSource`); and what does a
finished turn mean here (`Materializer`). `Observer` is the one optional port: a host that
keeps a trail implements it, and a host that does not passes nothing.
"""

from __future__ import annotations

from typing import Any, Protocol

from llm_gateway import ToolCall

from .model import (
    AgentDefinition,
    AgentLoopResult,
    AgentSession,
    RunRecord,
    RunStatus,
    ToolOutcome,
    TurnOutcome,
)


class SessionStore(Protocol):
    """Where a session's durable state lives, and who is allowed to resume it.

    `claim` is the whole guard against two resumes of one session: it must move the record
    to `RUNNING` only if nothing holds it, and answer `None` when something does.
    """

    async def create(
        self,
        *,
        kind: str,
        parent_run_id: int | None = None,
        source_message_id: int | None = None,
    ) -> RunRecord: ...

    async def get(self, run_id: int) -> RunRecord | None: ...

    async def save_state(self, run_id: int, state: dict[str, Any]) -> None: ...

    async def claim(self, run_id: int) -> RunRecord | None: ...

    async def finish(
        self,
        run_id: int,
        *,
        status: RunStatus,
        duration_ms: int,
        error_code: str | None = None,
    ) -> None: ...

    async def leave_interrupted(
        self, run_id: int, state: dict[str, Any], summary: str
    ) -> None:
        """Store an unfinished session, and tell its chain's root what it was left with.

        One write: the root's note is what a later turn looks for, so a session stored
        without it is one nothing can find its way back into.
        """

    async def adopt_interrupted_child(
        self, *, kind: str, parent_run_id: int
    ) -> RunRecord | None: ...

    async def take_interrupted_root(self) -> tuple[RunRecord, str] | None:
        """The root session a person wrote over, claimed, with what it had already done."""

    async def close_unfinished_children(self, run_id: int) -> int: ...


class ToolRunner(Protocol):
    """What a session of each kind may call, and what the application does when it calls."""

    def definition(self, kind: str) -> AgentDefinition: ...

    def is_immediate(self, agent: AgentSession, name: str) -> bool:
        """Whether this call runs inside the turn rather than waiting on a person."""

    async def run(self, agent: AgentSession, call: ToolCall) -> ToolOutcome: ...

    def route_target(self, call: ToolCall) -> tuple[str | None, dict[str, Any] | None]:
        """The session kind this `route` call names, or the error to hand back instead."""

    def refuse_mixed(self) -> dict[str, Any]:
        """What a call is told when the same response mixed the two kinds of tool."""

    def prepared_message(self) -> str:
        """The words that introduce a response whose calls all became changes."""

    def repair_exhausted_message(self) -> str:
        """The words for a response whose calls never became changes, after the last round."""


class ContextSource(Protocol):
    """What a session reads before its own steps, rebuilt from live state every turn."""

    async def messages_for(
        self,
        kind: str,
        dialogue: list[dict[str, Any]],
        prior_receipts: list[str] | None = None,
    ) -> list[dict[str, Any]]: ...


class Materializer(Protocol):
    """What a finished turn means to the application.

    ``None`` asks for one more round: the host corrected this turn's own tool results in
    place, and the session should run again rather than answer.
    """

    async def materialize(
        self, agent: AgentSession, result: AgentLoopResult
    ) -> TurnOutcome | None: ...


class Observer(Protocol):
    """A trail of what a session did. Optional: a host that keeps none passes nothing."""

    async def step(
        self, run_id: int, position: int, kind: str, metadata: dict[str, Any]
    ) -> None: ...
