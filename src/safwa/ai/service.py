from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from llm_gateway import CompletionRequest, CompletionTurn, LlmProvider, ToolCall

from ..constants import (
    MAX_REPAIR_ROUNDS,
    MAX_TOOL_CALLS,
    SUBAGENT_DEADLINE_SECONDS,
    SUBAGENT_HISTORY_LAST_MESSAGES,
)
from ..domain import (
    DomainError,
    utcnow,
)
from ..features.continuity.memory import MemoryFileStore
from ..features.diary.model import DiaryEntry
from ..features.proposals.api import (
    MutationToolSpec,
    ProposalDescription,
    ProposalRegistry,
    ToolPreparationError,
    detail_lines,
    result_value,
)
from ..features.proposals.model import (
    AUTO_SAVED_RECEIPT,
    DECISION_RECEIPTS,
    RECEIPT_MEANINGS,
    BatchDecision,
    ChangeAction,
    ProposalChange,
    QueueItem,
)
from ..features.proposals.reducer import INTERRUPTED
from ..features.proposals.store import ProposalStore
from ..features.proposals.use_cases import (
    decide_batch_item,
    interrupt_batch,
    number_queued_proposals,
    open_batch,
    prepare_proposal,
)
from ..history import citation_payload, conversation_block
from ..models import (
    AgentRun,
    AgentRunStatus,
    AgentStep,
    Card,
    Check,
    SavedRequest,
    Tag,
    Value,
)
from .autoapproval import AutoApprovalCandidate, AutoApprovalReviewer
from .context import DialogueMessage, board_context, ordered_owner_context
from .contracts import (
    AgentChange,
    CallHelperInput,
    OpenInput,
    QueryToolInput,
    RouteInput,
    ToolResultStatus,
    tool_json_schema,
)
from .mini import QUERY_SAFWA_TOOL, ReadToolSpec
from .prepare import ChangePreparer
from .sql import ReadOnlyQueryRunner, UnsafeQueryError, is_complex_read
from .subagents import RoutedSubagent

logger = logging.getLogger(__name__)

# The `open` tool's targets.  This is the last central item-kind table left in the AI layer;
# `telegram/screens.py` holds the other half of it, and the two fold into one screen
# registry when the Telegram adapters move into their features.
OPENABLE_MODELS: dict[str, Any] = {
    "card": Card,
    "check": Check,
    "tag": Tag,
    "value": Value,
    "request": SavedRequest,
    "diary": DiaryEntry,
}

OPEN_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "open",
        "description": (
            "Put one item on the screen, exactly as the user opening it by hand. Call it only "
            "when the user asked to see or open one single item. Otherwise cite the item in "
            "your answer instead."
        ),
        "parameters": tool_json_schema(OpenInput),
    },
}
ROUTE_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "route",
        "description": (
            "Hand this turn to a subagent. It reads this same conversation, does the work, "
            "and comes back with a receipt of what it did. You write the message the user "
            "sees. You just pass the name of the subagent."
        ),
        "parameters": tool_json_schema(RouteInput),
    },
}
CALL_HELPER_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "call_helper",
        "description": (
            "Ask a helper a question one simple read could not answer. It writes the query "
            "and hands back its result. You keep the turn and you write the answer."
        ),
        "parameters": tool_json_schema(CallHelperInput),
    },
}
# The Advisor reads and routes. Every mutation tool belongs to the subagent that owns that
# feature, so judging *which* change to propose happens where the change is authored.
SAFWA_TOOLS = (QUERY_SAFWA_TOOL, OPEN_TOOL)
# Tools that run during the turn instead of becoming a proposal the owner approves.
IMMEDIATE_TOOLS = frozenset({"query_safwa", "route", "open", "call_helper"})

# What a helper is: it reads, it answers with rows, and it cannot open a screen. `route`
# is the other half — a subagent that writes, and whose screen suspends the whole chain.
Helper = Callable[..., Awaitable[dict[str, Any]]]



def _mutation_repair_details(
    tool: MutationToolSpec | None, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Give the model a compact valid shape instead of a raw validator traceback."""
    if tool is None:
        return {}
    schema = tool_json_schema(tool.input_model)
    details: dict[str, Any] = {
        "expected_schema": {
            "required": schema.get("required", []),
            "allowed_properties": list(schema.get("properties", {})),
        }
    }
    if tool.repair is not None:
        details.update(tool.repair(arguments))
    return details


def _validation_error_summary(error: ValidationError) -> str:
    messages: list[str] = []
    for issue in error.errors(include_url=False, include_input=False):
        location = ".".join(str(item) for item in issue.get("loc", ()))
        message = str(issue.get("msg", "Invalid value"))
        messages.append(f"{location}: {message}" if location else message)
    return "; ".join(messages) or "Invalid tool arguments"


class AIOutcomeKind(StrEnum):
    ANSWER = "answer"
    PROPOSAL = "proposal"


@dataclass
class AIOutcome:
    kind: AIOutcomeKind
    message: str
    proposal_id: int | None = None
    did: list[str] = field(default_factory=list)
    # The item `open` resolved, as a deep-link payload: the chat shows its screen after
    # the answer.
    open_item: str | None = None


@dataclass
class PendingTool:
    call: ToolCall
    result: Any
    change: AgentChange | None = None


@dataclass
class AgentSession:
    """One model session: what it may call, what it has said, and what it has spent.

    The budget and the transcript belong to the session rather than to a single turn,
    because both survive an approval: the same session resumes once the owner decides.
    """

    run_id: int
    tools: tuple[dict[str, Any], ...]
    kind: str = "advisor"
    read_specs: dict[str, ReadToolSpec] = field(default_factory=dict)
    dialogue: list[dict[str, Any]] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list)
    prefix_len: int = 0
    tool_count: int = 0
    repair_rounds: int = 0
    result_summaries: list[str] = field(default_factory=list)
    display_result_summaries: list[str] = field(default_factory=list)
    # The session this one routed to, and the `route` call still waiting for its receipt.
    parent_run_id: int | None = None
    awaiting_route: dict[str, Any] | None = None
    # What this turn had already saved when the caller routed here.
    prior_receipts: list[str] = field(default_factory=list)
    # The item `open` resolved, kept until the session answers the owner.
    open_item: str | None = None
    # Whether a read in this session was complex enough to be offered a helper. The tool
    # is added when that happens, and `tools` is rebuilt from the kind on a resume — so a
    # session that routed a change and came back would lose a tool it had been shown.
    helper_offered: bool = False

    def offer_helper(self) -> None:
        """Put `call_helper` on this session's tools, once, and remember that it is there."""
        if self.helper_offered:
            return
        self.helper_offered = True
        self.tools = (*self.tools, CALL_HELPER_TOOL)

    @property
    def immediate(self) -> frozenset[str]:
        """Tool names that run inside the turn instead of becoming a proposal."""
        return frozenset({*IMMEDIATE_TOOLS, *self.read_specs})

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
            "transcript": _json_safe(self.transcript),
            "tool_count": self.tool_count,
            "repair_rounds": self.repair_rounds,
            "result_summaries": self.result_summaries,
            "display_result_summaries": self.display_result_summaries,
            "awaiting_route": self.awaiting_route,
            "prior_receipts": self.prior_receipts,
            "open_item": self.open_item,
            "helper_offered": self.helper_offered,
        }

    @classmethod
    def restore(
        cls,
        run: AgentRun,
        tools: tuple[dict[str, Any], ...],
        read_specs: dict[str, ReadToolSpec] | None = None,
    ) -> tuple[AgentSession, list[dict[str, Any]]]:
        """Rebuild a suspended session from its row, with the transcript it left behind.

        The context prefix is not restored — it is rebuilt from live state, so the owner's
        planning data and clock are current while the session's own steps are not replayed
        from anything but its own record.
        """
        state = dict(run.state_json or {})
        session = cls(
            run_id=run.id,
            tools=tools,
            kind=run.kind,
            read_specs=dict(read_specs or {}),
            dialogue=[dict(item) for item in state.get("dialogue") or []],
            tool_count=int(state.get("tool_count", 0)),
            repair_rounds=int(state.get("repair_rounds", 0)),
            result_summaries=list(state.get("result_summaries") or []),
            display_result_summaries=list(state.get("display_result_summaries") or []),
            parent_run_id=run.parent_run_id,
            awaiting_route=state.get("awaiting_route") or None,
            prior_receipts=list(state.get("prior_receipts") or []),
            open_item=state.get("open_item") or None,
        )
        if state.get("helper_offered"):
            session.offer_helper()
        return session, [dict(item) for item in state.get("transcript") or []]


@dataclass
class AgentLoopResult:
    """What one turn of a session produced: its words, its changes, or a suspension."""

    message: str
    pending_tools: list[PendingTool] = field(default_factory=list)
    # Set when a subagent this session routed to opened a screen: the whole chain waits
    # for the owner, and this is what they see meanwhile.
    suspended: AIOutcome | None = None


def failure_reason(error: Exception, limit: int = 160) -> str:
    """One short owner-readable clause; the traceback stays in the log."""
    text = " ".join(str(error).split()) or type(error).__name__
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _log_preview(content: str, limit: int = 500) -> str:
    compact = " ".join(content.split())
    return compact if len(compact) <= limit else compact[: limit - 3] + "..."


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _conversation_for(dialogue: list[dict[str, Any]]) -> str:
    """The tail of the conversation as data, for anyone who is not its assistant."""
    return conversation_block(
        [
            DialogueMessage(role=str(item["role"]), content=str(item["content"]))
            for item in dialogue[-SUBAGENT_HISTORY_LAST_MESSAGES:]
        ]
    )


def _add_notice(rows: list[dict[str, Any]], text: str) -> None:
    """Attach a notice to a result, joining one that is already the last row.

    Two notice rows would be two instructions, and this model follows the last one it read.
    """
    if rows and set(rows[-1]) == {"notice"}:
        rows[-1] = {"notice": f"{rows[-1]['notice']} {text}"}
        return
    rows.append({"notice": text})


def _system_note(content: str) -> dict[str, Any]:
    """Carry a system block as owner text.

    Only ``messages[0]`` may be a system message: the Qwen3.5 chat template raises
    ``System message must be at the beginning`` on any later one.
    """

    return {"role": "user", "content": f"[System]: {content}"}


def _append_user_message(messages: list[dict[str, Any]], content: str) -> None:
    """Append user-side context without creating adjacent user turns."""
    if messages and messages[-1].get("role") == "user":
        messages[-1]["content"] += "\n" + content
        return
    messages.append({"role": "user", "content": content})


def _cache_breakpoint(message: dict[str, Any]) -> dict[str, Any]:
    """Mark the end of a reusable prefix.

    OpenRouter accepts the Anthropic form and converts it to OpenAI's
    ``prompt_cache_breakpoint`` for GPT-5.6 and newer, so one marker is portable.
    """

    content = message.get("content")
    if not isinstance(content, str) or not content:
        return message
    return {
        **message,
        "content": [
            {"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}
        ],
    }


def _flatten_content(content: Any) -> str:
    if isinstance(content, list):
        return " ".join(str(block.get("text", "")) for block in content if isinstance(block, dict))
    return str(content or "")


def _approval_change_label(tool: dict[str, Any]) -> str:
    change = dict(tool.get("change") or {})
    entity = str(change.get("entity", tool.get("name", "item"))).title()
    action = str(change.get("action", "change")).title()
    entity_id = change.get("id")
    values = dict(change.get("values") or {})
    label = f"{action} {entity}"
    if entity_id is not None:
        label += f" #{entity_id}"
    name = values.get("name") or values.get("title")
    if name:
        label += f" “{result_value(name)}”"
    if values.get("tag_query"):
        label += f" → Tag “{result_value(values['tag_query'])}”"
    elif values.get("value_query"):
        label += f" → Value “{result_value(values['value_query'])}”"
    elif values.get("stage"):
        label += f" → {result_value(values['stage']).title()}"
    return label


# What the owner reads under a resolved call. A decision has its own line; a call that
# never reached a screen failed before one, and reads the same as one that failed on Save.
_RESULT_RECEIPTS = {
    **{decision.value: receipt for decision, receipt in DECISION_RECEIPTS.items()},
    ToolResultStatus.ERROR.value: DECISION_RECEIPTS[BatchDecision.FAILED],
}

# The reviewer saved it, so "you decided this" would be wrong in the reply.
AUTOAPPROVED = "auto"


def _approval_results_summary(
    tools: list[dict[str, Any]],
    *,
    include_preparation_errors: bool = True,
    for_display: bool = False,
) -> str:
    """Render one queue receipt.

    The owner and the model need different things from the same tools: the model reads
    IDs and every resolved field so it does not repeat its own work, while the owner
    reads one sentence per change.  ``for_display`` picks the short form, which comes
    from the ``display`` line built while the proposal still had a session.
    """
    lines = [] if for_display else ["Proposal results:"]
    for tool in tools:
        # Only mutation calls store a dict result; a read call in the same suspended
        # turn stores its rows as a list, which must not be read as an outcome.
        stored_result = tool.get("result")
        result = stored_result if isinstance(stored_result, dict) else {}
        if not include_preparation_errors and not tool.get("proposal_id"):
            continue
        if not tool.get("proposal_id") and (
            not tool.get("change") or result.get("status") != ToolResultStatus.ERROR
        ):
            continue
        status = str(result.get("status", BatchDecision.FAILED))
        prefix = _RESULT_RECEIPTS.get(status, f"⚠️ {status.title()}")
        if status == BatchDecision.APPROVED and result.get("approval_source") == AUTOAPPROVED:
            prefix = AUTO_SAVED_RECEIPT
        if for_display:
            line = f"{prefix} — " + (
                str(tool.get("display") or "") or _approval_change_label(tool)
            )
        else:
            line = f"{prefix} — {_approval_change_label(tool)}"
            affected_ids = result.get("affected_ids") or []
            if status == BatchDecision.APPROVED and affected_ids:
                line += " [result ID" + ("s" if len(affected_ids) != 1 else "") + ": "
                line += ", ".join(f"#{item}" for item in affected_ids) + "]"
        error = result.get("error")
        if error:
            line += f": {result_value(error)}"
        lines.append(line)
        if not for_display:
            # The detail lines carry what Safwa resolved rather than what the model sent:
            # parent_query/tag_query turned into IDs, and old → new values for an edit.
            # Trimming them for saved items costs the model information and invites repeats.
            lines.extend(f"  • {detail}" for detail in tool.get("details") or [])
    return "\n".join(lines) if lines and (for_display or len(lines) > 1) else ""


def _safe_approval_results_summary(
    tools: list[dict[str, Any]],
    *,
    include_preparation_errors: bool = True,
    for_display: bool = False,
) -> str:
    """Render the queue receipt, or nothing when rendering itself fails.

    By the time this runs the approved changes are already committed, so a defect in
    one label must never abort the turn that reports them back to the owner and to
    the model.
    """
    try:
        return _approval_results_summary(
            tools,
            include_preparation_errors=include_preparation_errors,
            for_display=for_display,
        )
    except Exception:
        logger.exception("Could not render the approval result summary")
        return ""


def _route_receipt(
    name: str,
    message: str,
    summaries: list[str],
    *,
    did: list[str] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    """What a finished subagent hands back to whoever routed to it.

    `did` is the same Saved/Discarded/Failed lines the owner reads, so there is one shape
    of receipt in the system.  `text` is the subagent's own words with its own citations —
    real ids the caller can reuse — and never the body of what it proposed.
    """
    receipt_lines = [line for summary in summaries for line in summary.splitlines() if line.strip()]
    receipt_lines.extend(line for line in did or [] if line.strip())
    receipt: dict[str, Any] = {
        "subagent": name,
        "outcome": "error" if error else "done",
        "did": list(dict.fromkeys(receipt_lines)),
    }
    if receipt["did"]:
        message = "\n".join(
            line for line in message.splitlines() if not line.strip().startswith(tuple(RECEIPT_MEANINGS))
        )
    if message.strip():
        receipt["text"] = message.strip()
    if error:
        receipt["error"] = error
    return receipt


def _compose_display_outcome(message: str, summaries: list[str]) -> str:
    """Attach each application-owned result receipt exactly once.

    The interface, rather than the model, owns Saved/Discarded/Failed receipts.  Approval
    batches can accumulate overlapping summary blocks, and a provider may still echo a
    receipt in wording of its own.  Anything that opens with a receipt prefix is therefore
    dropped from the body, not only a line that matches one of ours character for
    character.
    """
    receipt_lines: list[str] = []
    for summary in summaries:
        for raw_line in summary.splitlines():
            line = raw_line.strip()
            if line and line not in receipt_lines:
                receipt_lines.append(line)

    body = message.strip()
    if not receipt_lines:
        return body
    body_lines = [
        line for line in body.splitlines() if not line.strip().startswith(tuple(RECEIPT_MEANINGS))
    ]
    body = "\n".join(body_lines).strip()
    receipt = "\n".join(receipt_lines)
    return f"{receipt}\n\n{body}" if body else receipt


def _with_queued_siblings(result: Any, queued: int) -> Any:
    """Tell a failed call that the request's valid calls are still queued for review.

    One failed preparation never cancels its siblings, and the model has to know that
    before it retries.  Saying it here keeps it off a request that has no failures.
    """
    if not queued or not isinstance(result, dict) or result.get("status") != ToolResultStatus.ERROR:
        return result
    return {
        **result,
        "next": (
            f"{queued} other call(s) from this request were prepared and are queued for review; "
            "they were not cancelled. Wait for their results, then retry only this call."
        ),
    }


_DECISION_NEXT_STEPS = {
    BatchDecision.APPROVED: (
        "This change is saved. Do not propose it again. Continue with the parts of the "
        "user's request that are still unfinished, then answer."
    ),
    BatchDecision.DISCARDED: (
        "The user rejected this change, so it does not exist. Do not retry it unless the "
        "user asks again. Continue with the rest of the request, then answer."
    ),
    BatchDecision.FAILED: (
        "Applying this change failed, so nothing was written for it. Read `error`, fix only "
        "this call, and retry it once; every other resolved call in this request stands."
    ),
}


def _resolved_tool_result(
    tool: dict[str, Any], decision: BatchDecision, result: dict[str, Any]
) -> dict[str, Any]:
    """Describe one resolved queue item in the tool message the model reads back.

    A bare ``{"status": "approved", "affected_ids": [9]}`` says nothing about *what* was
    saved, which is how a resumed turn ends up repeating or misreporting its own work.
    """
    payload: dict[str, Any] = {"status": decision.value, **_json_safe(result)}
    change = dict(tool.get("change") or {})
    if change.get("entity"):
        payload["entity"] = change["entity"]
    if change.get("action"):
        payload["action"] = change["action"]
    try:
        payload["summary"] = _approval_change_label(tool)
    except Exception:  # a label defect must never break an already-committed change
        logger.exception("Could not label a resolved approval queue item")
    details = list(tool.get("details") or [])
    if details:
        payload["fields"] = details
    payload["next"] = _DECISION_NEXT_STEPS.get(
        decision, "Continue with the rest of the user's request."
    )
    if decision is BatchDecision.APPROVED and result.get("approval_source") == AUTOAPPROVED:
        # The user pressed nothing, so "you saved it" would be wrong in the reply.
        payload["next"] = "Safwa saved this one itself; the user did not decide. " + payload["next"]
    return payload


def _resumed_transcript(
    transcript: list[dict[str, Any]],
    tools: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Replay this request's own assistant/tool exchanges with the decisions filled in.

    No separate progress digest is injected here: every step of the request is present as
    its own call and result, each carrying its status and what to do next.  Restating them
    in an assistant message would duplicate the request once per approval.
    """
    results_by_id = {str(tool["id"]): tool.get("result") for tool in tools if tool.get("id")}
    replayed = [dict(message) for message in transcript]
    last_assistant = max(
        (index for index, message in enumerate(replayed) if message.get("role") == "assistant"),
        default=None,
    )
    if last_assistant is None:
        return replayed
    # Only the suspended turn's own results are unresolved; every earlier tool message
    # already carries its final content and must be replayed untouched.
    for message in replayed[last_assistant + 1 :]:
        if message.get("role") != "tool":
            continue
        tool_call_id = str(message.get("tool_call_id"))
        if tool_call_id in results_by_id:
            message["content"] = json.dumps(
                results_by_id[tool_call_id], ensure_ascii=False, default=str
            )
    return replayed


def _log_provider_request(messages: list[dict[str, Any]]) -> None:
    lines: list[str] = []
    for message in messages:
        content = _flatten_content(message.get("content"))
        if message.get("tool_calls"):
            content = "tool calls: " + ", ".join(
                call["function"]["name"] for call in message["tool_calls"]
            )
        elif message.get("role") == "tool":
            content = f"{message.get('name')}: {content}"
        lines.append(f"  {message['role']:<9} {_log_preview(content)}")
    logger.info("AI REQUEST ->\n%s\n%s", "\n".join(lines), "-" * 72)


def _log_provider_response(turn: CompletionTurn) -> None:
    if turn.tool_calls:
        details = "\n".join(
            f"  tool {call.name}({_log_preview(call.arguments_json, 700)})"
            for call in turn.tool_calls
        )
    else:
        details = "  " + _log_preview(turn.content, 1_000)
    if turn.usage is not None:
        usage = turn.usage
        cost = "" if usage.cost is None else f" cost={usage.cost}"
        details += (
            f"\n  usage prompt={usage.prompt_tokens} cached={usage.cached_tokens} "
            f"cache_write={usage.cache_write_tokens} completion={usage.completion_tokens}{cost}"
        )
    logger.info("AI RESPONSE <-\n%s\n%s", details, "-" * 72)


class AIAdvisor:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        provider: LlmProvider,
        memory: MemoryFileStore,
        query_runner: ReadOnlyQueryRunner,
        proposals: ProposalRegistry,
        *,
        system_prompt: str,
        model_name: str,
        provider_name: str = "openai-compatible",
        cache_breakpoints: bool = False,
        subagents: tuple[RoutedSubagent, ...] = (),
        helpers: Mapping[str, Helper] | None = None,
        autoapproval: AutoApprovalReviewer | None = None,
        reviews: ProposalStore | None = None,
    ) -> None:
        self.sessions = sessions
        self.provider = provider
        self.memory = memory
        self.query_runner = query_runner
        self.proposals = proposals
        self.system_prompt = system_prompt
        self.model_name = model_name
        self.provider_name = provider_name
        self.cache_breakpoints = cache_breakpoints
        self.subagents = {routed.name: routed for routed in subagents}
        self.helpers = dict(helpers or {})
        # Built once: the offer is the whole of what the model is ever told about helpers,
        # so it has to name the tool in the shape the tool actually takes.
        self.helper_offer = (
            "This read is complex. call_helper("
            + " or ".join(f'"{name}"' for name in self.helpers)
            + ', "<your question in words>") writes the query and hands back its result.'
        )
        self.autoapproval = autoapproval
        # Every review this process still owes an answer to. It is memory, not a table: a
        # restart is what ends them, and nothing outside this process ever reads one.
        self.reviews = reviews if reviews is not None else ProposalStore()
        self.preparer = ChangePreparer(provider, query_runner, proposals)
        # An empty roster means there is nothing to route to, so the tool is not offered.
        self.tools = (*SAFWA_TOOLS, ROUTE_TOOL) if subagents else SAFWA_TOOLS

    def _tools_for(self, kind: str) -> tuple[dict[str, Any], ...]:
        routed = self.subagents.get(kind)
        if routed is None:
            return self.tools
        return (
            *(spec.schema for spec in routed.read_tools),
            *(self.proposals.tools[name].schema() for name in routed.mutation_tools),
        )

    def _read_specs_for(self, kind: str) -> dict[str, ReadToolSpec]:
        routed = self.subagents.get(kind)
        if routed is None:
            return {}
        return {spec.name: spec for spec in routed.read_tools}

    async def handle(
        self,
        text: str,
        *,
        source_message_id: int | None = None,
        dialogue: list[DialogueMessage] | None = None,
    ) -> AIOutcome:
        started = time.monotonic()
        run = AgentRun(
            kind="advisor",
            provider=self.provider_name,
            model=self.model_name,
            status=AgentRunStatus.RUNNING.value,
            source_message_id=source_message_id,
        )
        async with self.sessions() as session:
            session.add(run)
            await session.commit()

        try:
            turn_dialogue = (
                [{"role": item.role, "content": item.content} for item in dialogue]
                if dialogue
                else [{"role": "user", "content": text}]
            )
            messages = await self._context_messages(
                dialogue or [DialogueMessage(role="user", content=text)]
            )
            agent = AgentSession(
                run_id=run.id,
                tools=self.tools,
                dialogue=turn_dialogue,
                messages=messages,
                prefix_len=len(messages),
            )
            result = await self._run_agent_loop(agent)
            if result.suspended is not None:
                # A subagent opened a screen, so this session waits for its receipt.
                await self._suspend_for_child(agent)
                await self._finish_run(run.id, AgentRunStatus.AWAITING_APPROVAL, started)
                return result.suspended
            outcome = await self._materialize(agent, result)
            status = (
                AgentRunStatus.AWAITING_APPROVAL
                if outcome.kind is AIOutcomeKind.PROPOSAL
                else AgentRunStatus.COMPLETED
            )
            await self._finish_run(run.id, status, started)
            # The turn is over, so a saved subagent session it never routed back into has
            # missed its one chance.
            await self._close_lapsed_sessions()
            return outcome
        except Exception as error:
            logger.exception("AI advisor run failed")
            await self._finish_run(
                run.id, AgentRunStatus.FAILED, started, type(error).__name__
            )
            raise

    @staticmethod
    def _answer(agent: AgentSession, message: str) -> AIOutcome:
        """One session's words, and — for the session the owner reads — its receipts.

        A subagent's words go to whoever routed to it, so they are handed over untouched.
        The root is the only participant that writes to the chat, which makes it the one
        place that has to guarantee the owner is never left with nothing.
        """
        if agent.parent_run_id is not None:
            return AIOutcome(AIOutcomeKind.ANSWER, message)
        composed = _compose_display_outcome(message, agent.display_result_summaries)
        return AIOutcome(
            AIOutcomeKind.ANSWER,
            composed or "⚠️ Safwa had nothing to say about that. You can ask again.",
            open_item=agent.open_item,
        )

    async def _suspend_for_child(self, agent: AgentSession) -> None:
        """Store a session that is waiting on a subagent it routed to."""
        async with self.sessions() as session:
            run = await session.get(AgentRun, agent.run_id)
            if run is not None:
                run.state_json = agent.state()
            await session.commit()

    async def _answer_or_deliver(self, agent: AgentSession, outcome: AIOutcome) -> AIOutcome:
        """End a resumed session: hand its receipt to its caller, or answer the owner."""
        if agent.parent_run_id is None:
            return outcome
        receipt = _route_receipt(agent.kind, outcome.message, agent.display_result_summaries)
        delivered = await self._deliver_to_parent(agent, receipt)
        if delivered is not None:
            return delivered
        # The caller is already resuming elsewhere; the owner still gets the receipts.
        return AIOutcome(
            AIOutcomeKind.ANSWER,
            _compose_display_outcome(outcome.message, agent.display_result_summaries)
            or "✅ Done.",
        )

    async def _deliver_to_parent(
        self, child: AgentSession, receipt: dict[str, Any]
    ) -> AIOutcome | None:
        """Answer the `route` call that started this session, and run its caller on.

        Returns ``None`` when there is no caller — the session was the root of its turn.
        The loop walks the whole chain, so the turn ends only when a session with no
        parent answers.
        """
        agent = child
        while agent.parent_run_id is not None:
            started = time.monotonic()
            async with self.sessions() as session:
                run = await self._claim_session(session, agent.parent_run_id, held_run_id=None)
                if run is None:
                    logger.warning("Session #%s is already resuming", agent.parent_run_id)
                    return None
                parent, transcript = AgentSession.restore(
                    run, self._tools_for(run.kind), self._read_specs_for(run.kind)
                )
                await session.commit()
            waiting = dict(parent.awaiting_route or {})
            parent.awaiting_route = None
            parent.display_result_summaries.extend(
                str(line) for line in receipt.get("did") or []
            )
            messages = await self._session_messages(parent)
            parent.prefix_len = len(messages)
            messages.extend(transcript)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": str(waiting.get("call_id", "")),
                    "name": "route",
                    "content": json.dumps(receipt, ensure_ascii=False, default=str),
                }
            )
            parent.messages = messages
            result = await self._run_agent_loop(parent)
            if result.suspended is not None:
                await self._suspend_for_child(parent)
                await self._finish_run(
                    parent.run_id, AgentRunStatus.AWAITING_APPROVAL, started
                )
                return result.suspended
            outcome = await self._materialize(parent, result)
            if outcome.kind is AIOutcomeKind.PROPOSAL:
                await self._finish_run(
                    parent.run_id, AgentRunStatus.AWAITING_APPROVAL, started
                )
                return outcome
            await self._finish_run(parent.run_id, AgentRunStatus.COMPLETED, started)
            if parent.parent_run_id is None:
                await self._close_lapsed_sessions()
                return outcome
            receipt = _route_receipt(
                parent.kind, outcome.message, parent.display_result_summaries
            )
            agent = parent
        return None

    async def _run_child(
        self, name: str, parent: AgentSession
    ) -> tuple[AIOutcome, dict[str, Any] | None]:
        """Run the named subagent to its own end, resuming its session if it has one.

        A saved session is picked up as it stands, so a correction to a proposal the owner
        just refused is answered by the session that wrote it, not by a rewrite.

        The receipt is ``None`` when the subagent opened a screen: it has not finished, and
        the outcome is what the owner sees while the whole chain waits for them.
        """
        routed = self.subagents[name]
        started = time.monotonic()
        async with self.sessions() as session:
            run = await self._resume_suspended(session, name)
            if run is None:
                run = AgentRun(
                    kind=name,
                    provider=self.provider_name,
                    model=self.model_name,
                    status=AgentRunStatus.RUNNING.value,
                    claimed_at=utcnow(),
                    parent_run_id=parent.run_id,
                )
                session.add(run)
                await session.commit()
                agent = AgentSession(
                    run_id=run.id,
                    tools=self._tools_for(name),
                    kind=name,
                    read_specs=self._read_specs_for(name),
                    parent_run_id=parent.run_id,
                )
                transcript: list[dict[str, Any]] = []
            else:
                run.parent_run_id = parent.run_id
                agent, transcript = AgentSession.restore(
                    run, self._tools_for(name), self._read_specs_for(name)
                )
            await session.commit()
        run_id = agent.run_id
        agent.dialogue = parent.dialogue
        agent.prior_receipts = list(parent.display_result_summaries)
        try:
            messages = await self._routed_context(routed, agent.dialogue, agent.prior_receipts)
            agent.prefix_len = len(messages)
            messages.extend(transcript)
            agent.messages = messages
            # The deadline bounds one active stretch of the session, never a suspension:
            # a session waiting on the owner is not a session that is taking too long.
            result = await asyncio.wait_for(
                self._run_agent_loop(agent), timeout=SUBAGENT_DEADLINE_SECONDS
            )
            outcome = await self._materialize(agent, result)
            if outcome.kind is AIOutcomeKind.PROPOSAL:
                await self._finish_run(run_id, AgentRunStatus.AWAITING_APPROVAL, started)
                return outcome, None
            await self._finish_run(run_id, AgentRunStatus.COMPLETED, started)
            # The materialized outcome, not the raw loop result: a repair round answers again.
            return outcome, _route_receipt(
                name,
                outcome.message,
                agent.display_result_summaries,
                did=outcome.did,
            )
        except TimeoutError:
            logger.warning("SUBAGENT %s timed out after %.0fs", name, SUBAGENT_DEADLINE_SECONDS)
            await self._finish_run(run_id, AgentRunStatus.FAILED, started, "timeout")
            return AIOutcome(AIOutcomeKind.ANSWER, ""), _route_receipt(
                name,
                "",
                [],
                error=f"{name} did not finish within {SUBAGENT_DEADLINE_SECONDS:.0f} seconds.",
            )
        except Exception as error:
            logger.exception("Routed subagent %s failed", name)
            await self._finish_run(
                run_id, AgentRunStatus.FAILED, started, type(error).__name__
            )
            return AIOutcome(AIOutcomeKind.ANSWER, ""), _route_receipt(
                name, "", [], error=failure_reason(error)
            )

    async def _close_lapsed_sessions(self) -> None:
        """End every saved subagent session this Advisor turn did not route back into.

        Called once, when a turn ends without suspending.  A session this turn did route
        into is `completed` by then, so what is left is exactly the drafts from earlier
        turns: the owner's words either came straight back to one — the correction case —
        or they were about something else, and then it is over rather than waiting for a
        later `route` that would answer the wrong question.

        A session whose screen is still live is left alone: the owner can still press Save,
        and that resumes it without the Advisor being involved at all.
        """
        async with self.sessions() as session:
            saved = list(
                await session.scalars(
                    select(AgentRun).where(
                        AgentRun.kind != "advisor",
                        AgentRun.status == AgentRunStatus.AWAITING_APPROVAL.value,
                        AgentRun.claimed_at.is_(None),
                    )
                )
            )
            closed = 0
            for run in saved:
                if self.reviews.batch_for_run(run.id) is not None:
                    continue
                run.status = AgentRunStatus.ABANDONED.value
                closed += 1
            if closed:
                await session.commit()
                logger.info("Closed %d subagent session(s) the turn did not resume", closed)

    async def _resume_suspended(self, session: AsyncSession, name: str) -> AgentRun | None:
        """Claim this subagent's newest saved session, if it left one behind."""
        run_id = await session.scalar(
            select(AgentRun.id)
            .where(
                AgentRun.kind == name,
                AgentRun.status == AgentRunStatus.AWAITING_APPROVAL.value,
                AgentRun.claimed_at.is_(None),
            )
            .order_by(AgentRun.id.desc())
            .limit(1)
        )
        if run_id is None:
            return None
        return await self._claim_session(session, int(run_id), held_run_id=None)

    async def _context_messages(
        self,
        dialogue: list[DialogueMessage],
    ) -> list[dict[str, Any]]:
        memory = await self.memory.sync()
        async with self.sessions() as session:
            context = await board_context(session)
        # Ordered by how often each block changes, so the stable prefix stays
        # byte-identical across turns and remote prompt caching can hit it.
        # Anything volatile goes after the dialogue, never into a system block.
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            _system_note(ordered_owner_context(memory.text, context.state)),
        ]
        # The history source has already bounded the window by its token budget.
        for item in dialogue:
            if item.role == "user":
                _append_user_message(messages, item.content)
            else:
                messages.append({"role": item.role, "content": item.content})
        _append_user_message(messages, f"[System]: {context.clock}")
        if self.cache_breakpoints:
            messages[0] = _cache_breakpoint(messages[0])
            if len(messages) > 2:
                messages[-2] = _cache_breakpoint(messages[-2])
        return messages

    async def _routed_context(
        self,
        routed: RoutedSubagent,
        dialogue: list[dict[str, Any]],
        prior_receipts: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """A routed subagent reads the conversation as data, under its own prompt.

        Same order as the Advisor's: prompt, then state, then conversation, then what this
        turn has already saved, then the clock — so the stable part stays byte-identical
        and the volatile part stays last.  The receipts sit outside the conversation, so
        the ``SUBAGENT_HISTORY_LAST_MESSAGES`` window never trims them away.
        """
        messages: list[dict[str, Any]] = [{"role": "system", "content": routed.prompt}]
        if routed.board_state:
            async with self.sessions() as session:
                context = await board_context(session)
            _append_user_message(messages, f"[System]: Current board state:\n{context.state}")
        conversation = _conversation_for(dialogue)
        if conversation:
            _append_user_message(
                messages,
                "[System]: The conversation so far, newest last. None of it is yours: read it "
                f"for what the owner wants changed.\n{conversation}",
            )
        if self.cache_breakpoints:
            messages[0] = _cache_breakpoint(messages[0])
        lines = [line for line in prior_receipts or [] if line.strip()]
        if lines:
            _append_user_message(
                messages, "[System]: Already saved in this request:\n" + "\n".join(lines)
            )
        if routed.clock is not None:
            _append_user_message(messages, f"[System]: {routed.clock()}")
        return messages

    async def _session_messages(self, agent: AgentSession) -> list[dict[str, Any]]:
        """Rebuild the context prefix a session reads, from live state, by its kind."""
        routed = self.subagents.get(agent.kind)
        if routed is not None:
            return await self._routed_context(routed, agent.dialogue, agent.prior_receipts)
        return await self._context_messages(
            [
                DialogueMessage(role=str(item["role"]), content=str(item["content"]))
                for item in agent.dialogue
            ]
        )

    async def _provider_turn(self, agent: AgentSession) -> CompletionTurn:
        _log_provider_request(agent.messages)
        turn = await self.provider.complete(
            CompletionRequest(
                messages=tuple(agent.messages),
                tools=tuple(agent.tools),
                # A subagent was routed to for the work, so its first move is the work.
                # Only the first: the loop ends on a turn that calls no tool, and a
                # session that must always call one never ends.
                tool_choice=(
                    "required" if agent.tool_count == 0 and agent.kind in self.subagents else None
                ),
            )
        )
        _log_provider_response(turn)
        return turn

    async def _run_agent_loop(self, agent: AgentSession) -> AgentLoopResult:
        """Run the model until it answers in words.

        Stopping with no content is not an answer, so the session says what it is waiting
        for and runs on.  Repair rounds bound that, and an empty result after them is
        `_materialize`'s to turn into something the owner can read.
        """
        messages = agent.messages
        while True:
            turn = await self._provider_turn(agent)
            if turn.tool_calls:
                assistant_tool_calls = [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {"name": call.name, "arguments": call.arguments_json},
                    }
                    for call in turn.tool_calls
                ]
                messages.append(
                    {
                        "role": "assistant",
                        "content": turn.content or None,
                        "tool_calls": assistant_tool_calls,
                    }
                )
                if len(turn.tool_calls) > 1 and any(
                    call.name == "route" for call in turn.tool_calls
                ):
                    # A route can suspend the whole chain, and a suspended response cannot
                    # carry results for its siblings: the transcript would resume malformed.
                    for call in turn.tool_calls:
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": call.id,
                                "name": call.name,
                                "content": json.dumps(
                                    {
                                        "status": ToolResultStatus.ERROR.value,
                                        "code": "route_is_not_shared",
                                        "error": "route must be the only tool call in a response.",
                                        "next": "Send route alone, then use what it hands back.",
                                        "retryable": True,
                                    },
                                    ensure_ascii=False,
                                ),
                            }
                        )
                    continue
                pending_tools: list[PendingTool] = []
                immediate = agent.immediate
                has_reads = any(call.name in immediate for call in turn.tool_calls)
                has_mutations = any(call.name not in immediate for call in turn.tool_calls)
                for call in turn.tool_calls:
                    agent.tool_count += 1
                    if agent.tool_count > MAX_TOOL_CALLS:
                        raise DomainError("The advisor exceeded the tool-call limit")
                    change = None
                    if call.name == "route":
                        result, suspended = await self._execute_route_tool(agent, call)
                        if suspended is not None:
                            return AgentLoopResult(message="", suspended=suspended)
                    elif call.name == "query_safwa":
                        result = await self._execute_query_tool(agent, call)
                    elif call.name == "open":
                        result = await self._execute_open_tool(agent, call)
                    elif call.name == "call_helper":
                        result = await self._execute_call_helper_tool(agent, call)
                    elif call.name in agent.read_specs:
                        result = await self._execute_read_tool(agent, call)
                    elif has_reads and has_mutations:
                        result = {
                            "status": ToolResultStatus.ERROR.value,
                            "code": "mixed_read_and_mutation_tools",
                            "error": (
                                "Mutation tools cannot share a response with a read tool or route."
                            ),
                            "next": (
                                "Use the read result, then retry this mutation in the next response."
                            ),
                            "retryable": True,
                        }
                    else:
                        change, result = await self._execute_mutation_tool(agent, call)
                    pending_tools.append(PendingTool(call=call, result=result, change=change))
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.id,
                            "name": call.name,
                            "content": json.dumps(result, ensure_ascii=False, default=str),
                        }
                    )
                changes = [tool.change for tool in pending_tools if tool.change is not None]
                if changes:
                    card_creates = [
                        change
                        for change in changes
                        if change.entity == "card" and change.action is ChangeAction.CREATE
                    ]
                    if card_creates and len(card_creates) == len(changes):
                        message = "I prepared the Card proposal for your review."
                    elif card_creates:
                        message = "I prepared Card proposals and other changes for your review."
                    else:
                        message = "I prepared the proposed changes for your approval."
                    return AgentLoopResult(
                        message=message,
                        pending_tools=pending_tools,
                    )
                invalid_mutations = [
                    tool
                    for tool in pending_tools
                    if tool.call.name not in immediate and tool.change is None
                ]
                if invalid_mutations:
                    if agent.repair_rounds >= MAX_REPAIR_ROUNDS:
                        return AgentLoopResult(
                            message=(
                                "I could not prepare the requested change after five repair attempts. "
                                "No unfinished operation was applied."
                            ),
                        )
                    agent.repair_rounds += 1
                continue

            if turn.content:
                return AgentLoopResult(turn.content)
            if agent.repair_rounds >= MAX_REPAIR_ROUNDS:
                return AgentLoopResult("")
            agent.repair_rounds += 1
            # A turn that stops with nothing leaves the owner with nothing.  The empty
            # assistant message is dropped rather than kept: it carries no information and
            # some chat templates reject it.
            messages.append(
                _system_note(
                    "You stopped without answering. Write the answer to the owner now, "
                    "in their language, using what the tool results already gave you."
                )
            )

    async def _execute_route_tool(
        self, agent: AgentSession, call: ToolCall
    ) -> tuple[dict[str, Any], AIOutcome | None]:
        """Run the named subagent and hand back its receipt.

        The second value is set only when the subagent opened a screen: it has not
        finished, so this session suspends with it and the owner sees that screen.
        """
        try:
            name = RouteInput.model_validate(json.loads(call.arguments_json or "{}")).name.strip()
        except (ValidationError, json.JSONDecodeError, TypeError, ValueError) as error:
            return {
                "status": ToolResultStatus.ERROR.value,
                "code": "invalid_arguments",
                "error": (
                    _validation_error_summary(error)
                    if isinstance(error, ValidationError)
                    else str(error)
                ),
                "hint": f'Send {{"name": "<subagent>"}}. One of: {", ".join(self.subagents)}.',
                "retryable": True,
            }, None
        if name not in self.subagents:
            return {
                "status": ToolResultStatus.ERROR.value,
                "code": "unknown_subagent",
                "error": f"There is no subagent named {name!r}.",
                "hint": f"Route to one of: {', '.join(self.subagents) or 'none'}.",
                "retryable": True,
            }, None
        # Saved before the subagent runs, because the subagent may suspend and this
        # session then has to come back to a call it has not answered yet.
        agent.awaiting_route = {"call_id": call.id, "subagent": name}
        async with self.sessions() as session:
            session.add(
                AgentStep(
                    run_id=agent.run_id,
                    position=agent.tool_count,
                    kind="route",
                    metadata_json={"tool_call_id": call.id, "subagent": name},
                )
            )
            run = await session.get(AgentRun, agent.run_id)
            if run is not None:
                run.state_json = agent.state()
            await session.commit()
        logger.info("ROUTE -> %s", name)
        outcome, receipt = await self._run_child(name, agent)
        if receipt is None:
            return {}, outcome
        agent.awaiting_route = None
        # The interface owns the Saved/Discarded/Failed lines, so they travel with the
        # session that will write to the chat rather than being left for the model to echo.
        agent.display_result_summaries.extend(str(line) for line in receipt.get("did") or [])
        return receipt, None

    async def _execute_call_helper_tool(
        self, agent: AgentSession, call: ToolCall
    ) -> dict[str, Any]:
        """Ask a helper one question and hand its rows back. Nothing suspends.

        The helper reads and answers with data, so this session keeps its turn: there is no
        screen to wait for and no receipt to compose.
        """
        try:
            payload = CallHelperInput.model_validate(json.loads(call.arguments_json or "{}"))
        except (ValidationError, json.JSONDecodeError, TypeError, ValueError) as error:
            return {
                "status": ToolResultStatus.ERROR.value,
                "code": "invalid_arguments",
                "error": (
                    _validation_error_summary(error)
                    if isinstance(error, ValidationError)
                    else str(error)
                ),
                "hint": (
                    f'Send {{"name": "<helper>", "request": "<your question>"}}. '
                    f"One of: {', '.join(self.helpers)}."
                ),
                "retryable": True,
            }
        helper = self.helpers.get(payload.name.strip())
        if helper is None:
            return {
                "status": ToolResultStatus.ERROR.value,
                "code": "unknown_helper",
                "error": f"There is no helper named {payload.name!r}.",
                "hint": f"Call one of: {', '.join(self.helpers) or 'none'}.",
                "retryable": True,
            }
        async with self.sessions() as session:
            session.add(
                AgentStep(
                    run_id=agent.run_id,
                    position=agent.tool_count,
                    kind="helper",
                    metadata_json={
                        "tool_call_id": call.id,
                        "helper": payload.name,
                        "request": payload.request,
                    },
                )
            )
            await session.commit()
        logger.info("HELPER -> %s %s", payload.name, _log_preview(payload.request, 200))
        try:
            return await helper(
                conversation=_conversation_for(agent.dialogue), request=payload.request
            )
        except Exception as error:
            # A helper is an optimisation. Losing the turn because one broke would be worse
            # than the answer the Advisor can still give from what it read itself.
            logger.exception("Helper %s failed", payload.name)
            return {
                "helper": payload.name,
                "status": ToolResultStatus.ERROR.value,
                "error": failure_reason(error),
                "hint": "Answer the owner with what you already have.",
            }

    def _should_offer_helper(
        self, agent: AgentSession, sql: str, rows: list[dict[str, Any]]
    ) -> bool:
        """Whether this read earned the model a helper it was not already carrying.

        A read that failed does not: its `hint` already says to repair that one SELECT, and
        a second instruction in the same result is the one this model would follow.
        """
        if not self.helpers or agent.kind != "advisor" or not sql:
            return False
        if rows and rows[0].get("status") == ToolResultStatus.ERROR:
            return False
        capped = bool(rows) and set(rows[-1]) == {"notice"}
        return capped or is_complex_read(sql)

    async def _execute_read_tool(
        self, agent: AgentSession, call: ToolCall
    ) -> Any:
        """Run one of this session's own read tools and record that it ran."""
        result = await agent.read_specs[call.name].run(call)
        async with self.sessions() as session:
            session.add(
                AgentStep(
                    run_id=agent.run_id,
                    position=agent.tool_count,
                    kind="read",
                    metadata_json={
                        "tool_call_id": call.id,
                        "tool": call.name,
                        "arguments": call.arguments_json,
                    },
                )
            )
            await session.commit()
        logger.info("AI TOOL %s(%s)", call.name, _log_preview(call.arguments_json, 200))
        return result

    async def _execute_query_tool(
        self, agent: AgentSession, call: ToolCall
    ) -> list[dict[str, Any]]:
        if call.name != "query_safwa":
            rows: list[dict[str, Any]] = [
                {
                    "status": ToolResultStatus.ERROR.value,
                    "code": "unknown_tool",
                    "error": f"Unknown tool: {call.name}",
                    "hint": (
                        "Call one of: query_safwa, card, check, value, tag, request, reminder, "
                        "remove."
                    ),
                    "retryable": True,
                }
            ]
            sql = ""
        else:
            try:
                arguments = json.loads(call.arguments_json)
                query = QueryToolInput.model_validate(arguments)
                sql = query.sql
                outcome = await self.query_runner.run(sql)
                rows = outcome.as_tool_result()
                if outcome.notice:
                    logger.info("AI TOOL query_safwa capped: %s", outcome.notice)
            # ``UnsafeQueryError`` is a ``ValueError``, so it has to be caught before the
            # argument-shape clause or a rejected SELECT is reported as a bad argument and
            # the model rewrites the call instead of the query.
            except (UnsafeQueryError, sqlite3.Error, TimeoutError, OSError) as error:
                # A rejected or broken read is the model's to repair. Raising here would
                # end the whole request, including any mutation queued alongside it.
                rows = [
                    {
                        "status": ToolResultStatus.ERROR.value,
                        "code": "unsafe_query"
                        if isinstance(error, UnsafeQueryError)
                        else "query_failed",
                        "error": str(error),
                        "hint": (
                            "Fix only this SELECT and call query_safwa again. One read-only "
                            "SELECT or WITH … SELECT over the ai_* views, no other statement. "
                            "This failure changed nothing: every step of the request already "
                            "resolved above still stands, so do not restart the request."
                        ),
                        "retryable": True,
                    }
                ]
            except (KeyError, TypeError, ValueError, ValidationError, json.JSONDecodeError) as error:
                sql = ""
                rows = [
                    {
                        "status": ToolResultStatus.ERROR.value,
                        "code": "invalid_arguments",
                        "error": (
                            _validation_error_summary(error)
                            if isinstance(error, ValidationError)
                            else str(error)
                        ),
                        "hint": (
                            'Send exactly one string argument, e.g. {"sql": "SELECT id, title '
                            'FROM ai_cards LIMIT 20"}, and call query_safwa again.'
                        ),
                        "retryable": True,
                    }
                ]
        if self._should_offer_helper(agent, sql, rows):
            agent.offer_helper()
            _add_notice(rows, self.helper_offer)
        logger.info(
            "AI TOOL query_safwa -> rows=%d sql=%s",
            len(rows),
            _log_preview(sql, 700),
        )
        async with self.sessions() as session:
            session.add(
                AgentStep(
                    run_id=agent.run_id,
                    position=agent.tool_count,
                    kind="read_query",
                    metadata_json={
                        "tool_call_id": call.id,
                        "tool": call.name,
                        "arguments": call.arguments_json,
                        "sql": sql,
                        "row_count": len(rows),
                        "columns": list(rows[0]) if rows else [],
                        "result": _json_safe(rows),
                    },
                )
            )
            await session.commit()
        return rows

    async def _execute_open_tool(
        self, agent: AgentSession, call: ToolCall
    ) -> dict[str, Any]:
        """Resolve the item to show and hand it to the session that writes to the chat."""
        try:
            request = OpenInput.model_validate(json.loads(call.arguments_json or "{}"))
        except (ValidationError, json.JSONDecodeError, TypeError, ValueError) as error:
            return {
                "status": ToolResultStatus.ERROR.value,
                "code": "invalid_arguments",
                "error": (
                    _validation_error_summary(error)
                    if isinstance(error, ValidationError)
                    else str(error)
                ),
                "hint": 'Send {"item_type": "card", "id": 12}.',
                "retryable": True,
            }
        async with self.sessions() as session:
            item = await session.get(OPENABLE_MODELS[request.item_type], request.id)
            if item is None:
                return {
                    "status": ToolResultStatus.ERROR.value,
                    "code": "not_found",
                    "error": f"There is no {request.item_type} #{request.id}.",
                    "hint": "Find the id with query_safwa, then call open again.",
                    "retryable": True,
                }
            item_id = item.id
        agent.open_item = citation_payload(request.item_type, item_id)
        logger.info("AI TOOL open -> %s", agent.open_item)
        return {
            "status": ToolResultStatus.OK.value,
            "opened": {"item_type": request.item_type, "id": item_id},
            "next": "The screen follows your message. Answer in one short line.",
        }

    async def _execute_mutation_tool(
        self, agent: AgentSession, call: ToolCall
    ) -> tuple[AgentChange | None, dict[str, Any]]:
        arguments: Any = None
        try:
            arguments = json.loads(call.arguments_json)
            if not isinstance(arguments, dict):
                raise ValueError("Tool arguments must be an object")
            change = self.proposals.change_from_tool(call.name, arguments)
        except (ValueError, ValidationError, json.JSONDecodeError) as error:
            logger.info("AI TOOL %s rejected: %s", call.name, error)
            error_text = (
                _validation_error_summary(error)
                if isinstance(error, ValidationError)
                else str(error)
            )
            result = {
                "status": ToolResultStatus.ERROR.value,
                "code": "invalid_arguments",
                "error": error_text,
                "hint": (
                    "Retry only this unfinished tool call using expected_arguments and the "
                    "argument_rules below; do not repeat successful calls."
                ),
                "retryable": True,
            }
            if isinstance(arguments, dict):
                result.update(
                    _mutation_repair_details(self.proposals.tools.get(call.name), arguments)
                )
            return None, result
        logger.info("AI TOOL %s prepared %s.%s", call.name, change.entity, change.action)
        async with self.sessions() as session:
            session.add(
                AgentStep(
                    run_id=agent.run_id,
                    position=agent.tool_count,
                    kind="mutation_intent",
                    metadata_json={
                        "tool_call_id": call.id,
                        "arguments": call.arguments_json,
                        "tool": call.name,
                        "entity": change.entity,
                        "action": change.action,
                        "id": change.id,
                    },
                )
            )
            await session.commit()
        return change, {
            "status": ToolResultStatus.PREPARED.value,
            "entity": change.entity,
            "action": change.action,
            "id": change.id,
            "next": "Wait for the user's review or approval; do not say it is complete.",
        }

    def _raw_details(self, change: AgentChange | None) -> list[str]:
        """Field lines for a change that never reached a proposal row."""
        if change is None:
            return []
        presenter = self.proposals.presenter(change.entity)
        if presenter is None:
            return detail_lines(dict(change.values))
        return presenter.raw_details(change)

    async def _proposal_display_line(
        self,
        session: AsyncSession,
        proposal_id: int,
        fallback: AgentChange | None,
        details: list[str],
    ) -> str:
        """One sentence describing a proposal the way the owner reads it.

        The model still gets `details`; this line trades their IDs for the names the
        owner recognises, so a receipt says which Tag landed on which Card.
        """
        change = self._only_change(proposal_id)
        if change is None:
            return _approval_change_label(
                {
                    "change": {
                        "entity": fallback.entity,
                        "action": fallback.action,
                        "id": fallback.id,
                        "values": fallback.values,
                    }
                    if fallback is not None
                    else None
                }
            )
        presenter = self.proposals.presenter(change.entity)
        if presenter is None:
            return _approval_change_label(
                {
                    "change": {
                        "entity": change.entity,
                        "action": change.action,
                        "id": change.entity_id,
                        "values": dict(change.values),
                    }
                }
            )
        return await presenter.summary(session, change, details)

    async def describe_proposal(
        self, session: AsyncSession, proposal_id: int
    ) -> ProposalDescription:
        """How one proposal reads to the owner: the same line and fields a receipt uses.

        Read it **before** applying — the field lines are a before/after diff against
        committed state, and after `apply` that diff is empty.
        """
        fields = await self._proposal_result_details(session, proposal_id, None)
        summary = await self._proposal_display_line(session, proposal_id, None, fields)
        return ProposalDescription(summary=summary, fields=fields)

    async def _proposal_result_details(
        self,
        session: AsyncSession,
        proposal_id: int,
        fallback: AgentChange | None,
    ) -> list[str]:
        change = self._only_change(proposal_id)
        if change is None:
            return self._raw_details(fallback)
        presenter = self.proposals.presenter(change.entity)
        if presenter is None:
            return self._raw_details(fallback) or detail_lines(dict(change.values))
        return await presenter.details(session, change, fallback)

    def _only_change(self, proposal_id: int) -> ProposalChange | None:
        """The change a review holds. Nothing writes a second one, and a receipt reads one."""
        proposal = self.reviews.proposal(proposal_id)
        return proposal.changes[0] if proposal is not None and proposal.changes else None

    async def _materialize(
        self,
        agent: AgentSession,
        result: AgentLoopResult,
    ) -> AIOutcome:
        if not result.pending_tools:
            return self._answer(agent, result.message)
        mutation_tools = [tool for tool in result.pending_tools if tool.change is not None]
        preparation_results = {tool.call.id: _json_safe(tool.result) for tool in result.pending_tools}
        failed_call_ids = {
            tool.call.id
            for tool in result.pending_tools
            if tool.call.name not in IMMEDIATE_TOOLS and tool.change is None
        }
        proposal_details: dict[str, list[str]] = {}
        proposal_displays: dict[str, str] = {}
        # In the order the model made the calls, which is the order the owner reviews them in.
        queued_proposal_ids: dict[str, int] = {}
        async with self.sessions() as session:
            for tool in mutation_tools:
                try:
                    proposal = await prepare_proposal(
                        session,
                        self.reviews,
                        self.preparer,
                        message=result.message,
                        change=tool.change,
                    )
                except ToolPreparationError as error:
                    failed_call_ids.add(tool.call.id)
                    preparation_results[tool.call.id] = error.as_tool_result()
                    logger.info(
                        "AI TOOL %s preparation error [%s]: %s",
                        tool.call.name,
                        error.code,
                        error,
                    )
                    continue
                except DomainError as error:
                    failed_call_ids.add(tool.call.id)
                    preparation_results[tool.call.id] = ToolPreparationError(
                        "invalid_arguments",
                        str(error),
                        "Correct only this unfinished tool call and retry it.",
                    ).as_tool_result()
                    logger.info("AI TOOL %s preparation error: %s", tool.call.name, error)
                    continue
                proposal_details[tool.call.id] = await self._proposal_result_details(
                    session, proposal.id, tool.change
                )
                proposal_displays[tool.call.id] = await self._proposal_display_line(
                    session, proposal.id, tool.change, proposal_details[tool.call.id]
                )
                queued_proposal_ids[tool.call.id] = proposal.id
            queue = [
                QueueItem(proposal_id=proposal_id, call_ids=(call_id,))
                for call_id, proposal_id in queued_proposal_ids.items()
            ]
            number_queued_proposals(self.reviews, queue, result.message)
            tool_results = []
            for tool in result.pending_tools:
                queued_id = queued_proposal_ids.get(tool.call.id)
                tool_results.append(
                    {
                        "id": tool.call.id,
                        "name": tool.call.name,
                        "arguments": tool.call.arguments_json,
                        # A call still on screen has no result yet; that is what waiting is.
                        "result": None
                        if queued_id
                        else _with_queued_siblings(
                            preparation_results[tool.call.id], len(queue)
                        ),
                        "details": proposal_details.get(tool.call.id)
                        or self._raw_details(tool.change),
                        "display": proposal_displays.get(tool.call.id),
                        "proposal_id": queued_id,
                        "change": (
                            {
                                "entity": tool.change.entity,
                                "action": tool.change.action,
                                "id": tool.change.id,
                                "values": _json_safe(tool.change.values),
                            }
                            if tool.change is not None
                            else None
                        ),
                    }
                )
            if queue:
                repair_exhausted = bool(
                    failed_call_ids and agent.repair_rounds >= MAX_REPAIR_ROUNDS
                )
                if failed_call_ids:
                    agent.repair_rounds += 1
                self.reviews.open_batch(
                    open_batch(
                        run_id=agent.run_id,
                        items=queue,
                        tool_calls=tool_results,
                        repair_exhausted=repair_exhausted,
                    )
                )
                run = await session.get(AgentRun, agent.run_id)
                if run is not None:
                    run.state_json = agent.state()
            await session.commit()
        if not queue:
            if failed_call_ids:
                if agent.repair_rounds >= MAX_REPAIR_ROUNDS:
                    return AIOutcome(
                        AIOutcomeKind.ANSWER,
                        "I could not prepare the requested change after five repair attempts. "
                        "No unfinished operation was applied.",
                    )
                messages = _json_safe(agent.messages)
                results_by_id = {tool["id"]: tool["result"] for tool in tool_results}
                for message in messages:
                    if message.get("role") != "tool":
                        continue
                    tool_call_id = str(message.get("tool_call_id"))
                    if tool_call_id in results_by_id:
                        message["content"] = json.dumps(
                            results_by_id[tool_call_id], ensure_ascii=False, default=str
                        )
                agent.messages = messages
                agent.repair_rounds += 1
                repaired = await self._run_agent_loop(agent)
                return await self._materialize(agent, repaired)
            return self._answer(agent, result.message)
        return await self._advance_autoapprovals(
            AIOutcome(AIOutcomeKind.PROPOSAL, result.message, proposal_id=queue[0].proposal_id),
            held_run_id=agent.run_id,
        )

    async def _autoapproval_candidate(self, proposal_id: int) -> AutoApprovalCandidate | None:
        """Build the reviewer's request-only view for the active head of one batch."""
        async with self.sessions() as session:
            batch = self.reviews.batch_for_proposal(proposal_id)
            if batch is None:
                return None
            head = batch.state.head
            if head is None or head.proposal_id != proposal_id:
                return None
            change = self._only_change(proposal_id)
            if change is None:
                return None
            description = await self.describe_proposal(session, proposal_id)
            run = await session.get(AgentRun, batch.run_id)
            request = next(
                (
                    str(item.get("content", ""))
                    for item in reversed((run.state_json or {}).get("dialogue") or [])
                    if item.get("role") == "user"
                ),
                "",
            ) if run is not None else ""
            return AutoApprovalCandidate(
                user_request=request,
                entity=change.entity,
                action=change.action,
                entity_id=change.entity_id,
                values=dict(change.values),
                summary=description.summary,
                fields=tuple(description.fields),
            )

    async def _advance_autoapprovals(
        self, outcome: AIOutcome, *, held_run_id: int | None = None
    ) -> AIOutcome:
        """Auto-save one eligible head; resolving it advances and checks the next head."""
        if (
            self.autoapproval is None
            or outcome.kind is not AIOutcomeKind.PROPOSAL
            or outcome.proposal_id is None
        ):
            return outcome
        candidate = await self._autoapproval_candidate(outcome.proposal_id)
        if candidate is None:
            return outcome
        verdict = await self.autoapproval.review(candidate)
        if not verdict.approved:
            return outcome
        try:
            advanced = await self.resolve_approval(
                outcome.proposal_id,
                decision=BatchDecision.APPROVED,
                result={
                    "approval_source": AUTOAPPROVED,
                    "autoapproval_reason": verdict.reason,
                },
                apply_proposal=True,
                held_run_id=held_run_id,
            )
        except Exception as error:
            # `approve_proposal` and the batch decision share one transaction. A failure
            # therefore leaves the original pending proposal safe to render as-is.
            logger.warning(
                "Autoapproval apply failed for proposal #%s; keeping manual review: %s",
                outcome.proposal_id,
                error,
            )
            return outcome
        return advanced or AIOutcome(
            AIOutcomeKind.ANSWER, f"{AUTO_SAVED_RECEIPT} the proposed change."
        )

    def has_pending_approval(self, proposal_id: int) -> bool:
        """Return whether this screen belongs to a suspended agent turn."""
        return self.reviews.batch_for_proposal(proposal_id) is not None

    async def resolve_approval(
        self,
        proposal_id: int,
        *,
        decision: BatchDecision,
        result: dict[str, Any],
        dialogue: list[DialogueMessage] | None = None,
        apply_proposal: bool = False,
        held_run_id: int | None = None,
    ) -> AIOutcome | None:
        """Resolve one queued screen and resume the suspended tool turn once complete."""
        started = time.monotonic()
        next_outcome: AIOutcome | None = None
        async with self.sessions() as session:
            decided = await decide_batch_item(
                session,
                self.reviews,
                self.proposals,
                proposal_id,
                decision=decision,
                apply_change=apply_proposal,
                render=lambda tool, affected: _resolved_tool_result(
                    tool,
                    decision,
                    {**result, "affected_ids": affected} if apply_proposal else result,
                ),
            )
            if decided is None:
                return None
            tools = decided.tool_calls
            head = decided.state.head
            if head is not None:
                await session.commit()
                next_outcome = AIOutcome(
                    AIOutcomeKind.PROPOSAL,
                    "Review the next proposed change.",
                    proposal_id=head.proposal_id,
                )
            else:
                # The queue is empty, so the batch has done its whole job.  Closing it and
                # claiming the session in the same commit is what makes a crash here cost
                # nothing: no half-open batch is left to route a later press into, and the
                # session is left plainly interrupted.
                run = await self._claim_session(
                    session, decided.run_id, held_run_id=held_run_id
                )
                if run is None:
                    logger.warning("Session #%s is already resuming", decided.run_id)
                    await session.commit()
                    return None
                run_id = run.id
                repair_exhausted = decided.state.repair_exhausted
                agent, stored_transcript = AgentSession.restore(
                    run, self._tools_for(run.kind), self._read_specs_for(run.kind)
                )
                if not agent.dialogue:
                    agent.dialogue = [
                        {"role": item.role, "content": item.content} for item in dialogue or []
                    ]
                await session.commit()

        if next_outcome is not None:
            return await self._advance_autoapprovals(next_outcome, held_run_id=held_run_id)

        try:
            result_summary = _safe_approval_results_summary(tools)
            if result_summary:
                agent.result_summaries.append(result_summary)
            display_summary = _safe_approval_results_summary(
                tools, include_preparation_errors=False, for_display=True
            )
            if display_summary:
                agent.display_result_summaries.append(display_summary)
            exhausted = (
                "I could not prepare the remaining requested changes after five repair "
                "attempts. No unfinished operation was applied."
            )
            if repair_exhausted:
                await self._finish_run(run_id, AgentRunStatus.COMPLETED, started)
                outcome = await self._answer_or_deliver(agent, self._answer(agent, exhausted))
                outcome.did.extend(display_summary.splitlines())
                return outcome
            messages = await self._session_messages(agent)
            agent.prefix_len = len(messages)
            messages.extend(_resumed_transcript(stored_transcript, tools))
            agent.messages = messages
            loop_result = await self._run_agent_loop(agent)
            if loop_result.suspended is not None:
                await self._suspend_for_child(agent)
                await self._finish_run(run_id, AgentRunStatus.AWAITING_APPROVAL, started)
                return loop_result.suspended
            outcome = await self._materialize(agent, loop_result)
            if outcome.kind is AIOutcomeKind.PROPOSAL:
                await self._finish_run(run_id, AgentRunStatus.AWAITING_APPROVAL, started)
                return outcome
            await self._finish_run(run_id, AgentRunStatus.COMPLETED, started)
            outcome = await self._answer_or_deliver(agent, outcome)
            outcome.did.extend(display_summary.splitlines())
            return outcome
        except Exception as error:
            logger.exception("AI continuation failed after the approval queue was resolved")
            await self._finish_run(
                run_id, AgentRunStatus.FAILED, started, type(error).__name__
            )
            result_summary = _safe_approval_results_summary(tools, for_display=True)
            if result_summary:
                return AIOutcome(
                    AIOutcomeKind.ANSWER,
                    _compose_display_outcome(
                        f"⚠️ Safwa could not generate its follow-up ({failure_reason(error)}). "
                        "You can continue with a new message.",
                        [result_summary],
                    ),
                )
            raise

    async def cancel_approval_for_proposal(self, proposal_id: int) -> str | None:
        """Freeze a suspended batch when new dialogue supersedes its active screen.

        Returns the consolidated result of the interrupted request, or ``None`` when the
        screen does not belong to a suspended batch.  The caller needs that text because
        earlier items in the queue may already be saved: freezing the screen as a plain
        "discarded" notice would tell both the owner and the model something untrue.

        The Advisor's session is superseded by the message that arrived, but a subagent's
        stays waiting: the owner's words go to the Advisor, and "the same, but capitalise
        the name" has to reach the session that wrote the refused proposal.  Its own
        results are folded into its transcript first, so it resumes on a settled record.
        """
        async with self.sessions() as session:
            interrupted = interrupt_batch(self.reviews, proposal_id, reason=INTERRUPTED)
            if interrupted is None:
                return None
            tools = interrupted.tool_calls
            run = await session.get(AgentRun, interrupted.run_id)
            prior_summaries: list[str] = []
            if run is not None:
                state = dict(run.state_json or {})
                prior_summaries = list(state.get("display_result_summaries") or [])
                if run.kind == "advisor":
                    run.status = AgentRunStatus.CANCELLED.value
                else:
                    state["transcript"] = _resumed_transcript(
                        [dict(item) for item in state.get("transcript") or []], tools
                    )
                    run.state_json = state
                # The subagent's draft is kept for one Advisor turn; the callers waiting on
                # it are not.  Their plan was made before these words arrived, and the turn
                # those words start is what decides what happens now.
                caller_id = run.parent_run_id
                while caller_id is not None:
                    caller = await session.get(AgentRun, caller_id)
                    if (
                        caller is None
                        or caller.status != AgentRunStatus.AWAITING_APPROVAL.value
                    ):
                        break
                    caller.status = AgentRunStatus.CANCELLED.value
                    caller_id = caller.parent_run_id
            await session.commit()
        summaries = [
            *prior_summaries,
            _safe_approval_results_summary(
                tools, include_preparation_errors=False, for_display=True
            ),
        ]
        return _compose_display_outcome("", [summary for summary in summaries if summary])

    async def _claim_session(
        self, session: AsyncSession, run_id: int, *, held_run_id: int | None
    ) -> AgentRun | None:
        """Take a suspended session for this resume, or report that it is already taken.

        The claim is the whole guard: two resumes of one session would replay the same
        transcript twice, and only one of them could own the answer.  ``held_run_id`` is
        the session the caller is already running inside — an autoapproval resolves the
        screen its own turn just opened, which is that turn continuing, not a second one.
        """
        if held_run_id == run_id:
            return await session.get(AgentRun, run_id)
        claimed = await session.scalar(
            update(AgentRun)
            .where(AgentRun.id == run_id, AgentRun.claimed_at.is_(None))
            .values(claimed_at=utcnow(), status=AgentRunStatus.RUNNING.value)
            .returning(AgentRun.id)
        )
        return None if claimed is None else await session.get(AgentRun, run_id)

    async def _finish_run(
        self,
        run_id: int,
        status: AgentRunStatus,
        started: float,
        error_code: str | None = None,
    ) -> None:
        async with self.sessions() as session:
            run = await session.get(AgentRun, run_id)
            if run:
                run.status = status.value
                run.duration_ms = int((time.monotonic() - started) * 1000)
                run.error_code = error_code
                # The turn is over either way, so the session is free for the next resume.
                run.claimed_at = None
                await session.commit()
