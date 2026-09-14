"""What a session is, and what one turn of it produces.

Nothing here knows what the application does. A session has a budget, a transcript and a
place in a chain; a turn ends in words, in changes waiting for a person, or in a
suspension. What a change *is* belongs to whoever implements the ports.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from llm_gateway import ToolCall


class RunStatus(StrEnum):
    """Where a session stands. A store keeps the last one it was told."""

    RUNNING = "running"
    # Waiting on a person: a screen is open somewhere in this chain.
    AWAITING_APPROVAL = "awaiting_approval"
    # The person wrote instead of deciding. The session keeps its plan and can be resumed.
    INTERRUPTED = "interrupted"
    COMPLETED = "completed"
    FAILED = "failed"
    # Ended without finishing: interrupted and never come back to, or waiting on a person
    # who never answered.
    ABANDONED = "abandoned"


@dataclass(frozen=True)
class InteractionRef:
    """What a session that stopped on a person is resumed with.

    Opaque on purpose: the runtime never learns what the person was shown. The token is
    minted when the session is checkpointed and cleared when it is answered, so a decision
    about a suspension the session has already left is refused instead of being spliced
    into a turn it does not belong to.
    """

    run_id: int
    token: str


@dataclass(frozen=True)
class Resumption:
    """What the person decided, in the shape the suspended session was waiting for.

    `results` answer that session's own unfinished tool calls, one per call id. `notes` are
    what the model reads back about the decision and `display_notes` what the person reads.
    `answer` ends the session with fixed words instead of running it on, for a host that
    already knows there is nothing left to ask.
    """

    results: Mapping[str, Any] = field(default_factory=dict)
    notes: tuple[str, ...] = ()
    display_notes: tuple[str, ...] = ()
    answer: str | None = None


@dataclass(frozen=True)
class AgentDefinition:
    """What a session of one kind may call.

    `read_specs` are the runner's own; the runtime uses only their names. `helper_tool` is
    a schema the session may earn part-way through a turn, and it has to be kept so a
    resumed session does not lose a tool it was already shown.
    """

    kind: str
    tools: tuple[dict[str, Any], ...]
    read_specs: dict[str, Any] = field(default_factory=dict)
    helper_tool: dict[str, Any] | None = None


@dataclass
class RunRecord:
    """One session as its store holds it."""

    id: int
    kind: str
    parent_run_id: int | None = None
    state: dict[str, Any] = field(default_factory=dict)


@dataclass
class PendingTool:
    """One tool call the turn made, with whatever the runner gave back for it."""

    call: ToolCall
    result: Any
    change: Any | None = None


@dataclass
class AgentSession:
    """One model session: what it may call, what it has said, and what it has spent.

    The budget and the transcript belong to the session rather than to a single turn,
    because both survive an approval: the same session resumes once the owner decides.
    """

    run_id: int
    tools: tuple[dict[str, Any], ...]
    kind: str = "advisor"
    read_specs: dict[str, Any] = field(default_factory=dict)
    dialogue: list[dict[str, Any]] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list)
    prefix_len: int = 0
    tool_count: int = 0
    repair_rounds: int = 0
    result_summaries: list[str] = field(default_factory=list)
    display_result_summaries: list[str] = field(default_factory=list)
    # Who routed here, and this session's own `route` call that has not been answered yet.
    parent_run_id: int | None = None
    awaiting_route: dict[str, Any] | None = None
    # What this turn had already saved when the caller routed here.
    prior_receipts: list[str] = field(default_factory=list)
    # Whatever the application needs this session to still know after a suspension. The
    # runtime carries it and never reads it.
    host_state: dict[str, Any] = field(default_factory=dict)
    # Set while this session is stopped on a person: the token half of its `InteractionRef`.
    interaction_token: str | None = None
    # Whether a read in this session was complex enough to be offered a helper. The tool
    # is added when that happens, and `tools` is rebuilt from the kind on a resume — so a
    # session that routed a change and came back would lose a tool it had been shown.
    helper_offered: bool = False
    helper_tool: dict[str, Any] | None = None

    def offer_helper(self) -> None:
        """Put the helper tool on this session's tools, once, and remember that it is there."""
        if self.helper_offered or self.helper_tool is None:
            return
        self.helper_offered = True
        self.tools = (*self.tools, self.helper_tool)

    @property
    def tool_names(self) -> frozenset[str]:
        """What this session may call: the names of the tools it was actually handed.

        `tools` is the list that goes to the model, so it is also the list a call is
        checked against — a name that is not here was never offered to this session, and
        answering it would run a tool of somebody else's on this session's behalf.
        """
        return frozenset(
            str(tool["function"]["name"]) for tool in self.tools if "function" in tool
        )

    @property
    def transcript(self) -> list[dict[str, Any]]:
        """The assistant/tool exchanges this request produced, without its context prefix.

        Everything before ``prefix_len`` is rebuilt from live state on every turn; this
        tail is what an approval must replay so the model keeps its own intermediate
        steps instead of re-planning the request from the last tool call alone.
        """
        return self.messages[self.prefix_len :]

    def state(self) -> dict[str, Any]:
        """Everything the session needs to continue once the owner has decided."""
        return {
            "dialogue": self.dialogue,
            "transcript": json_safe(self.transcript),
            "tool_count": self.tool_count,
            "repair_rounds": self.repair_rounds,
            "result_summaries": self.result_summaries,
            "display_result_summaries": self.display_result_summaries,
            "awaiting_route": self.awaiting_route,
            "prior_receipts": self.prior_receipts,
            "host_state": self.host_state,
            "helper_offered": self.helper_offered,
            "interaction_token": self.interaction_token,
        }

    @classmethod
    def start(
        cls,
        run_id: int,
        definition: AgentDefinition,
        *,
        parent_run_id: int | None = None,
        dialogue: list[dict[str, Any]] | None = None,
    ) -> AgentSession:
        """A session with nothing said yet."""
        return cls(
            run_id=run_id,
            tools=definition.tools,
            kind=definition.kind,
            read_specs=dict(definition.read_specs),
            helper_tool=definition.helper_tool,
            parent_run_id=parent_run_id,
            dialogue=list(dialogue or []),
        )

    @classmethod
    def restore(
        cls,
        record: RunRecord,
        definition: AgentDefinition,
    ) -> tuple[AgentSession, list[dict[str, Any]]]:
        """Rebuild a suspended session from its record, with the transcript it left behind.

        The context prefix is not restored — it is rebuilt from live state, so the owner's
        current data and clock are read again while the session's own steps are not
        replayed from anything but its own record.
        """
        state = dict(record.state or {})
        session = cls(
            run_id=record.id,
            tools=definition.tools,
            kind=record.kind,
            read_specs=dict(definition.read_specs),
            dialogue=[dict(item) for item in state.get("dialogue") or []],
            tool_count=int(state.get("tool_count", 0)),
            repair_rounds=int(state.get("repair_rounds", 0)),
            result_summaries=list(state.get("result_summaries") or []),
            display_result_summaries=list(state.get("display_result_summaries") or []),
            parent_run_id=record.parent_run_id,
            awaiting_route=state.get("awaiting_route") or None,
            prior_receipts=list(state.get("prior_receipts") or []),
            host_state=dict(state.get("host_state") or {}),
            interaction_token=state.get("interaction_token") or None,
            helper_tool=definition.helper_tool,
        )
        if state.get("helper_offered"):
            session.offer_helper()
        return session, [dict(item) for item in state.get("transcript") or []]


@dataclass
class TurnOutcome:
    """What the host made of one finished turn.

    ``waiting`` means a person now has something to decide, so the session stays open and
    the chain stops here, and ``ref`` is what resumes it. ``payload`` is the host's own
    answer object; the runtime carries it back untouched.
    """

    message: str
    waiting: bool = False
    payload: Any = None
    ref: InteractionRef | None = None


@dataclass
class ToolOutcome:
    """What running one tool call produced."""

    result: Any
    change: Any | None = None


@dataclass
class AgentLoopResult:
    """What one turn of a session produced: its words, its changes, or a suspension."""

    message: str
    pending_tools: list[PendingTool] = field(default_factory=list)
    # Set when a session this one routed to opened a screen: the whole chain waits for the
    # person, and this is what they see meanwhile.
    suspended: TurnOutcome | None = None


def json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def log_preview(content: str, limit: int = 500) -> str:
    compact = " ".join(content.split())
    return compact if len(compact) <= limit else compact[: limit - 3] + "..."


def flatten_content(content: Any) -> str:
    if isinstance(content, list):
        return " ".join(str(block.get("text", "")) for block in content if isinstance(block, dict))
    return str(content or "")


def route_receipt(
    name: str,
    message: str,
    summaries: list[str],
    *,
    error: str | None = None,
    receipt_prefixes: tuple[str, ...] = (),
) -> dict[str, Any]:
    """What a finished subagent hands back to whoever routed to it.

    `did` is the same set of lines the person reads, taken from the summaries this session
    accumulated, so there is one shape of receipt in the system.  `text` is the subagent's
    own words with its own citations — real ids the caller can reuse — and never the body
    of what it proposed.  `receipt_prefixes` are the host's receipt openings, so a line the
    model echoed is not counted twice.
    """
    receipt_lines = [line for summary in summaries for line in summary.splitlines() if line.strip()]
    receipt: dict[str, Any] = {
        "subagent": name,
        "outcome": "error" if error else "done",
        "did": list(dict.fromkeys(receipt_lines)),
    }
    if receipt["did"] and receipt_prefixes:
        message = "\n".join(
            line for line in message.splitlines() if not line.strip().startswith(receipt_prefixes)
        )
    if message.strip():
        receipt["text"] = message.strip()
    if error:
        receipt["error"] = error
    return receipt
