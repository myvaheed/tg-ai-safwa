from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..constants import (
    MAX_REPAIR_ROUNDS,
    MAX_TOOL_CALLS,
    RECEIPT_MEANINGS,
    SUBAGENT_DEADLINE_SECONDS,
    SUBAGENT_HISTORY_LAST_MESSAGES,
    SUSPENDED_BATCH_LOOKUP_LIMIT,
)
from ..domain import (
    CARD_REFERENCE_SPECS,
    CHECK_REFERENCE,
    TAG_REFERENCE,
    VALUE_REFERENCE,
    DomainError,
    ReferenceSpec,
    StaleStateError,
    archive_check,
    archive_saved_request,
    archive_subtree,
    archive_tag,
    archive_value,
    create_card,
    create_check,
    create_diary_entry,
    create_reminder,
    create_saved_request,
    create_tag,
    create_value,
    delete_diary_entry,
    delete_reminder,
    delete_subtree,
    finish_action,
    move_card,
    reschedule_reminder,
    resolve_check,
    resolve_references,
    set_card_parent,
    toggle_card_category,
    toggle_card_energy_type,
    update_card_fields,
    update_check_fields,
    update_diary_entry,
    update_reminder_text,
    update_saved_request,
    update_tag_fields,
    update_value_fields,
    utcnow,
)
from ..enums import (
    CHECK_ANSWER_ACTIONS,
    CHECK_OUTCOME_LABELS,
    TERMINAL_STAGES,
    ActorType,
    CardKind,
    CardStage,
    Category,
    EnergyType,
    Priority,
    ProposalStatus,
)
from ..history import conversation_block
from ..memory import MemoryFileStore
from ..models import (
    AgentRun,
    AgentStep,
    Card,
    CardCategory,
    CardEnergyType,
    CardTag,
    CardValue,
    ChangeProposal,
    Check,
    DiaryEntry,
    ProposalChange,
    Reminder,
    SavedRequest,
    Tag,
    Value,
    Workspace,
)
from ..reminders import (
    schedule_from_payload,
)
from .autoapproval import AutoApprovalCandidate, AutoApprovalReviewer
from .context import SYSTEM_PROMPT, DialogueMessage, planning_context
from .contracts import (
    MUTATION_TOOL_MODELS,
    AgentChange,
    QueryToolInput,
    RouteInput,
    mutation_change_from_tool,
    tool_json_schema,
)
from .mini import ReadToolSpec
from .prepare import ENTITY_MODELS, ChangePreparer, ToolPreparationError
from .provider import OpenAICompatibleProvider, ProviderToolCall, ProviderTurn
from .sql import ReadOnlyQueryRunner, UnsafeQueryError
from .subagents import RoutedSubagent

logger = logging.getLogger(__name__)

QUERY_SAFWA_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "query_safwa",
        "description": (
            "Explore Safwa's current planning data with one safe, read-only SQLite SELECT. "
            "Use it to find Cards, Tags, Values, Requests, Sprint state, metrics, or events "
            "before answering or preparing a change proposal."
        ),
        "parameters": tool_json_schema(QueryToolInput),
    },
}
MUTATION_TOOL_DESCRIPTIONS = {
    "card": (
        "Open the Card review UI. create proposes a new Card; update proposes exact "
        "field/set replacements; link and unlink add or remove one relationship type — Values, "
        "Tags, or Checks, since a Card owns all three links; move, complete, cancel, and reopen "
        "propose only that lifecycle action. Omit unused properties or send null; never invent "
        "placeholder IDs such as 0 or 1. In update, parent_id=null removes the parent. Nothing is "
        "saved until the "
        "user presses Save."
    ),
    "check": (
        "Open the Check review UI. create proposes a new Pending Check; update proposes a new title "
        "or repeatable flag; complete answers it Passed and cancel answers it Missed. Propose an "
        "answer only when the user already stated it — otherwise cite it so they answer it "
        "themselves. A Check is attached to a Card from the card tool (link/unlink with "
        "check_query or check_ids), never from here. Nothing is saved until the user presses Save."
    ),
    "value": "Open the Value editor with a create or update proposal;",
    "tag": "Open the Tag editor with a create or update proposal;",
    "request": "Prepare a saved Request create or update proposal.",
    "reminder": (
        "Propose a Reminder: instruction text plus timing in plain words. The text is handed "
        "to you as a request when the time comes, so it must stand on its own and must name "
        "every Safwa item it concerns by #id — look the id up with query_safwa first. "
        "Pass the timing through verbatim in when; never invent a date or an hour. Omit when "
        "in update mode to change only the text and leave the schedule alone."
    ),
    "remove": (
        "Take one item off the board: archive keeps its history, delete erases it and only a "
        "Card allows it. Name the entity and its id."
    ),
    "diary": (
        "Open the Diary review UI for one day. update proposes that day in the owner's voice, "
        "replacing whatever is saved; delete removes it. Nothing is saved until the user "
        "presses Save."
    ),
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
MUTATION_TOOLS: dict[str, dict[str, Any]] = {
    name: {
        "type": "function",
        "function": {
            "name": name,
            "description": MUTATION_TOOL_DESCRIPTIONS[name],
            "parameters": tool_json_schema(model),
        },
    }
    for name, model in MUTATION_TOOL_MODELS.items()
}
# The Advisor reads and routes. Every mutation tool belongs to the subagent that owns that
# feature, so judging *which* change to propose happens where the change is authored.
SAFWA_TOOLS = (QUERY_SAFWA_TOOL,)
# Tools that run during the turn instead of becoming a proposal the owner approves.
IMMEDIATE_TOOLS = frozenset({"query_safwa", "route"})


def query_read_tool(query_runner: ReadOnlyQueryRunner) -> ReadToolSpec:
    """`query_safwa` as a plain read tool, for a session that declares its own tools.

    The runner and its caps are shared; the session executes it through the same path
    the Advisor uses, so its steps are recorded the same way.
    """

    async def read(call: ProviderToolCall) -> list[dict[str, Any]]:
        try:
            query = QueryToolInput.model_validate(json.loads(call.arguments or "{}"))
            outcome = await query_runner.run(query.sql)
            return outcome.as_tool_result()
        except (
            UnsafeQueryError,
            sqlite3.Error,
            TimeoutError,
            OSError,
            ValidationError,
            json.JSONDecodeError,
            TypeError,
            ValueError,
        ) as error:
            return [
                {
                    "status": "error",
                    "code": "query_failed",
                    "error": str(error),
                    "hint": (
                        "Fix only this SELECT and call query_safwa again. One read-only "
                        "SELECT or WITH … SELECT over the ai_* views."
                    ),
                    "retryable": True,
                }
            ]

    return ReadToolSpec(QUERY_SAFWA_TOOL, read)


def _has_explicit_tool_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip()) and value.strip().casefold() not in {
            "null",
            "none",
            "nil",
            "undefined",
        }
    if isinstance(value, list):
        return bool(value)
    return True


def _mutation_repair_details(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Give the model a compact valid shape instead of a raw validator traceback."""
    model = MUTATION_TOOL_MODELS.get(name)
    if model is None:
        return {}

    schema = tool_json_schema(model)
    details: dict[str, Any] = {
        "expected_schema": {
            "required": schema.get("required", []),
            "allowed_properties": list(schema.get("properties", {})),
        }
    }
    if name != "card" or arguments.get("mode") != "create":
        return details

    expected: dict[str, Any] = {"mode": "create"}
    core_fields = (
        "kind",
        "title",
        "note",
        "stage",
        "priority",
        "hard_time",
        "blocked",
        "blocked_description",
        "effort_points",
        "repeatable",
        "categories",
        "energy_types",
    )
    for field_name in core_fields:
        if field_name in arguments and _has_explicit_tool_value(arguments[field_name]):
            expected[field_name] = arguments[field_name]

    # Keep intentional, non-placeholder relationship forms. Singular IDs are omitted from the
    # repair example because constrained decoders commonly invent the minimum allowed integer.
    for field_name in (
        "value_ids",
        "value_query",
        "tag_ids",
        "tag_query",
        "check_ids",
        "check_query",
        "parent_id",
        "parent_query",
    ):
        if field_name in arguments and _has_explicit_tool_value(arguments[field_name]):
            expected[field_name] = arguments[field_name]

    details.update(
        {
            "expected_arguments": expected,
            "argument_rules": [
                "For mode='create', omit id; it is assigned after Save.",
                "Omit unused relationship properties; never fill *_id with placeholder 0 or 1.",
                "Send only relationships that the user actually requested or that were resolved from data.",
            ],
        }
    )
    return details


def _validation_error_summary(error: ValidationError) -> str:
    messages: list[str] = []
    for issue in error.errors(include_url=False, include_input=False):
        location = ".".join(str(item) for item in issue.get("loc", ()))
        message = str(issue.get("msg", "Invalid value"))
        messages.append(f"{location}: {message}" if location else message)
    return "; ".join(messages) or "Invalid tool arguments"


@dataclass
class AIOutcome:
    kind: str
    message: str
    proposal_id: int | None = None


@dataclass(frozen=True)
class ProposalDescription:
    """One proposal in owner-facing words: a headline plus its `Label: value` lines."""

    summary: str
    fields: list[str] = field(default_factory=list)


@dataclass
class PendingTool:
    call: ProviderToolCall
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
        )
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


def _system_note(content: str) -> dict[str, Any]:
    """Carry a system block as owner text.

    Only ``messages[0]`` may be a system message: the Qwen3.5 chat template raises
    ``System message must be at the beginning`` on any later one.
    """

    return {"role": "user", "content": f"[System]: {content}"}


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


def _result_value(value: Any) -> str:
    return " ".join(str(value).split())[:100]


_DETAIL_LABELS = {
    "kind": "Kind",
    "title": "Title",
    "name": "Name",
    "description": "Description",
    "note": "Note",
    "stage": "Stage",
    "priority": "Priority",
    "hard_time": "Hard Time",
    "blocked": "Blocked",
    "blocked_description": "Blocked Description",
    "effort_points": "Effort",
    "repeatable": "Repeatable",
    "categories": "Categories",
    "energy_types": "Energy",
    "parent_id": "Parent ID",
    "card_id": "Card ID",
    "outcome": "Status",
    "values": "Values",
    "tags": "Tags",
    "checks": "Checks",
    "check_ids": "Checks",
    "active": "Active",
    "query_sql": "SQL",
}


def _detail_value(value: Any) -> str:
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if value is None or value == "" or value == []:
        return "—"
    if isinstance(value, list):
        return ", ".join(_result_value(item) for item in value) or "—"
    return _result_value(value)


def _reference_details(values: dict[str, Any], prefix: str) -> list[str]:
    result: list[str] = []
    singular = values.get(f"{prefix}_id")
    if singular is not None:
        result.append(f"#{singular}")
    result.extend(f"#{item}" for item in values.get(f"{prefix}_ids") or [])
    query = values.get(f"{prefix}_query")
    if query is not None:
        result.extend(str(item) for item in (query if isinstance(query, list) else [query]))
    return result


def _normalized_card_details(values: dict[str, Any], *, creating: bool) -> dict[str, Any]:
    fields = {
        name: values[name]
        for name in (
            "kind",
            "title",
            "note",
            "stage",
            "priority",
            "hard_time",
            "blocked",
            "blocked_description",
            "effort_points",
            "repeatable",
            "categories",
            "energy_types",
            "parent_id",
        )
        if name in values
    }
    if creating:
        fields.setdefault("stage", CardStage.BACKLOG.value)
        fields.setdefault("note", "")
        fields.setdefault("priority", "medium")
        fields.setdefault("hard_time", False)
        fields.setdefault("blocked", False)
        if fields.get("kind") == CardKind.ACTION.value:
            fields.setdefault("repeatable", False)
            fields.setdefault("categories", [])
            fields.setdefault("energy_types", [])
    if referenced_values := _reference_details(values, "value"):
        fields["values"] = referenced_values
    if referenced_tags := _reference_details(values, "tag"):
        fields["tags"] = referenced_tags
    if referenced_checks := _reference_details(values, "check"):
        fields["checks"] = referenced_checks
    return fields


def _value_details(entity: str, values: dict[str, Any], *, creating: bool) -> list[str]:
    fields = (
        _normalized_card_details(values, creating=creating) if entity == "card" else dict(values)
    )
    return [
        f"{_DETAIL_LABELS.get(field, field.replace('_', ' ').title())}: {_detail_value(value)}"
        for field, value in fields.items()
    ]


def _diary_detail_lines(change: ProposalChange) -> list[str]:
    """The day's shape, never its text: a receipt stays in the conversation for good.

    A day printed here would be re-read on every later turn and would spend the history
    budget it costs.  The Diary session holds its own draft, and a saved day is in
    `ai_diary`, so the body never has to travel.
    """
    values = dict(change.values)
    lines = [f"Date: {values.get('entry_date', '')}"]
    if change.action == "delete":
        lines.append("Entry: removed")
    else:
        lines.append(f"Entry: {len(str(values.get('body') or ''))} characters")
        if values.get("feeling_score") is not None:
            lines.append(f"Feeling: {values['feeling_score']}")
    return lines


def _raw_change_details(change: AgentChange | None) -> list[str]:
    if change is None:
        return []
    return _value_details(change.entity, change.values, creating=change.action == "create")


def _stored_change_details(change: ProposalChange) -> list[str]:
    """The same lines taken from the persisted row, for a caller with no `AgentChange`."""
    return _value_details(
        change.entity, dict(change.values), creating=change.action == "create"
    )


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
        label += f" “{_result_value(name)}”"
    if values.get("tag_query"):
        label += f" → Tag “{_result_value(values['tag_query'])}”"
    elif values.get("value_query"):
        label += f" → Value “{_result_value(values['value_query'])}”"
    elif values.get("stage"):
        label += f" → {_result_value(values['stage']).title()}"
    return label


# The receipt renders one line under any outcome, so the verb stays imperative:
# "🗑 Discarded — New Tag “X”" cannot be misread as a Tag that now exists.
_ACTION_VERBS = {
    "create": "New",
    "update": "Edit",
    "move": "Move",
    "complete": "Complete",
    "cancel": "Cancel",
    "reopen": "Reopen",
    "archive": "Archive",
    "delete": "Delete",
    "link": "Link",
    "unlink": "Unlink",
}

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
        if not include_preparation_errors and not tool.get("target"):
            continue
        if not tool.get("target") and (
            not tool.get("change") or result.get("status") not in {"error", "rejected"}
        ):
            continue
        status = str(result.get("status", "failed"))
        prefix = {
            "approved": "✅ Saved",
            "discarded": "🗑 Discarded",
            "rejected": "🗑 Discarded",
            "failed": "⚠️ Failed",
            "error": "⚠️ Failed",
        }.get(status, f"⚠️ {status.title()}")
        if status == "approved" and result.get("approval_source") == "auto":
            prefix = "⚡ Auto-saved"
        if for_display:
            line = f"{prefix} — " + (
                str(tool.get("display") or "") or _approval_change_label(tool)
            )
        else:
            line = f"{prefix} — {_approval_change_label(tool)}"
            affected_ids = result.get("affected_ids") or []
            if status == "approved" and affected_ids:
                line += " [result ID" + ("s" if len(affected_ids) != 1 else "") + ": "
                line += ", ".join(f"#{item}" for item in affected_ids) + "]"
        error = result.get("error")
        if error:
            line += f": {_result_value(error)}"
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
    name: str, message: str, summaries: list[str], *, error: str | None = None
) -> dict[str, Any]:
    """What a finished subagent hands back to whoever routed to it.

    `did` is the same Saved/Discarded/Failed lines the owner reads, so there is one shape
    of receipt in the system.  `text` is the subagent's own words with its own citations —
    real ids the caller can reuse — and never the body of what it proposed.
    """
    receipt: dict[str, Any] = {
        "subagent": name,
        "outcome": "error" if error else "done",
        "did": [line for summary in summaries for line in summary.splitlines() if line.strip()],
    }
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
    if not queued or not isinstance(result, dict) or result.get("status") != "error":
        return result
    return {
        **result,
        "next": (
            f"{queued} other call(s) from this request were prepared and are queued for review; "
            "they were not cancelled. Wait for their results, then retry only this call."
        ),
    }


_DECISION_NEXT_STEPS = {
    "approved": (
        "This change is saved. Do not propose it again. Continue with the parts of the "
        "user's request that are still unfinished, then answer."
    ),
    "discarded": (
        "The user rejected this change, so it does not exist. Do not retry it unless the "
        "user asks again. Continue with the rest of the request, then answer."
    ),
    "failed": (
        "Applying this change failed, so nothing was written for it. Read `error`, fix only "
        "this call, and retry it once; every other resolved call in this request stands."
    ),
}


def _resolved_tool_result(
    tool: dict[str, Any], decision: str, result: dict[str, Any]
) -> dict[str, Any]:
    """Describe one resolved queue item in the tool message the model reads back.

    A bare ``{"status": "approved", "affected_ids": [9]}`` says nothing about *what* was
    saved, which is how a resumed turn ends up repeating or misreporting its own work.
    """
    payload: dict[str, Any] = {"status": decision, **_json_safe(result)}
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
    if decision == "approved" and result.get("approval_source") == "auto":
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


def _log_provider_response(turn: ProviderTurn) -> None:
    if turn.tool_calls:
        details = "\n".join(
            f"  tool {call.name}({_log_preview(call.arguments, 700)})" for call in turn.tool_calls
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
        provider: OpenAICompatibleProvider,
        memory: MemoryFileStore,
        query_runner: ReadOnlyQueryRunner,
        *,
        model_name: str,
        provider_name: str = "openai-compatible",
        cache_breakpoints: bool = False,
        subagents: tuple[RoutedSubagent, ...] = (),
        autoapproval: AutoApprovalReviewer | None = None,
    ) -> None:
        self.sessions = sessions
        self.provider = provider
        self.memory = memory
        self.query_runner = query_runner
        self.model_name = model_name
        self.provider_name = provider_name
        self.cache_breakpoints = cache_breakpoints
        self.subagents = {routed.name: routed for routed in subagents}
        self.autoapproval = autoapproval
        self.preparer = ChangePreparer(provider, query_runner)
        # An empty roster means there is nothing to route to, so the tool is not offered.
        self.tools = (*SAFWA_TOOLS, ROUTE_TOOL) if subagents else SAFWA_TOOLS

    def _tools_for(self, kind: str) -> tuple[dict[str, Any], ...]:
        routed = self.subagents.get(kind)
        if routed is None:
            return self.tools
        return (
            *(spec.schema for spec in routed.read_tools),
            *(MUTATION_TOOLS[name] for name in routed.mutation_tools),
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
            status="running",
            source_message_id=source_message_id,
        )
        async with self.sessions() as session:
            session.add(run)
            await session.commit()

        try:
            messages = await self._context_messages(dialogue or [])
            if not dialogue:
                messages.append({"role": "user", "content": text})
            turn_dialogue = (
                [{"role": item.role, "content": item.content} for item in dialogue]
                if dialogue
                else [{"role": "user", "content": text}]
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
                await self._finish_run(run.id, "awaiting_approval", started)
                return result.suspended
            outcome = await self._materialize(agent, result)
            status = "awaiting_approval" if outcome.kind == "proposal" else "completed"
            await self._finish_run(run.id, status, started)
            # The turn is over, so a saved subagent session it never routed back into has
            # missed its one chance.
            await self._close_lapsed_sessions()
            return outcome
        except Exception as error:
            logger.exception("AI advisor run failed")
            await self._finish_run(run.id, "failed", started, type(error).__name__)
            raise

    @staticmethod
    def _answer(agent: AgentSession, message: str) -> AIOutcome:
        """One session's words, and — for the session the owner reads — its receipts.

        A subagent's words go to whoever routed to it, so they are handed over untouched.
        The root is the only participant that writes to the chat, which makes it the one
        place that has to guarantee the owner is never left with nothing.
        """
        if agent.parent_run_id is not None:
            return AIOutcome("answer", message)
        composed = _compose_display_outcome(message, agent.display_result_summaries)
        return AIOutcome(
            "answer",
            composed or "⚠️ Safwa had nothing to say about that. You can ask again.",
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
            "answer",
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
                await self._finish_run(parent.run_id, "awaiting_approval", started)
                return result.suspended
            outcome = await self._materialize(parent, result)
            if outcome.kind == "proposal":
                await self._finish_run(parent.run_id, "awaiting_approval", started)
                return outcome
            await self._finish_run(parent.run_id, "completed", started)
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
                    status="running",
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
            if outcome.kind == "proposal":
                await self._finish_run(run_id, "awaiting_approval", started)
                return outcome, None
            await self._finish_run(run_id, "completed", started)
            # The materialized outcome, not the raw loop result: a repair round answers again.
            return outcome, _route_receipt(
                name, outcome.message, agent.display_result_summaries
            )
        except TimeoutError:
            logger.warning("SUBAGENT %s timed out after %.0fs", name, SUBAGENT_DEADLINE_SECONDS)
            await self._finish_run(run_id, "failed", started, "timeout")
            return AIOutcome("answer", ""), _route_receipt(
                name,
                "",
                [],
                error=f"{name} did not finish within {SUBAGENT_DEADLINE_SECONDS:.0f} seconds.",
            )
        except Exception as error:
            logger.exception("Routed subagent %s failed", name)
            await self._finish_run(run_id, "failed", started, type(error).__name__)
            return AIOutcome("answer", ""), _route_receipt(
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
                        AgentRun.status == "awaiting_approval",
                        AgentRun.claimed_at.is_(None),
                    )
                )
            )
            closed = 0
            for run in saved:
                if await self._live_batch(session, run.id):
                    continue
                run.status = "abandoned"
                closed += 1
            if closed:
                await session.commit()
                logger.info("Closed %d subagent session(s) the turn did not resume", closed)

    @staticmethod
    async def _live_batch(session: AsyncSession, run_id: int) -> bool:
        """Whether this session still has a screen the owner could answer."""
        return (
            await session.scalar(
                select(AgentStep.id).where(
                    AgentStep.run_id == run_id,
                    AgentStep.kind == "approval_batch",
                    AgentStep.metadata_json["status"].as_string() == "pending",
                )
            )
        ) is not None

    async def _resume_suspended(self, session: AsyncSession, name: str) -> AgentRun | None:
        """Claim this subagent's newest saved session, if it left one behind."""
        run_id = await session.scalar(
            select(AgentRun.id)
            .where(
                AgentRun.kind == name,
                AgentRun.status == "awaiting_approval",
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
            context = await planning_context(session)
        # Ordered by how often each block changes, so the stable prefix stays
        # byte-identical across turns and remote prompt caching can hit it.
        # Anything volatile goes after the dialogue, never into a system block.
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            _system_note(
                f"Current planning state:\n{context.state}"
                f"\n\nPersistent memory:\n{memory.text}"
            ),
        ]
        # The history source has already bounded the window by its token budget.
        messages.extend({"role": item.role, "content": item.content} for item in dialogue)
        if self.cache_breakpoints:
            messages[0] = _cache_breakpoint(messages[0])
            messages[1] = _cache_breakpoint(messages[1])
            if dialogue:
                messages[-1] = _cache_breakpoint(messages[-1])
        messages.append(_system_note(context.clock))
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
        if routed.planning_state:
            async with self.sessions() as session:
                context = await planning_context(session)
            messages.append(_system_note(f"Current planning state:\n{context.state}"))
        conversation = conversation_block(
            [
                DialogueMessage(role=str(item["role"]), content=str(item["content"]))
                for item in dialogue[-SUBAGENT_HISTORY_LAST_MESSAGES:]
            ]
        )
        if conversation:
            messages.append(
                _system_note(
                    "The conversation so far, newest last. None of it is yours: read it "
                    f"for what the owner wants changed.\n{conversation}"
                )
            )
        if self.cache_breakpoints:
            messages[0] = _cache_breakpoint(messages[0])
            if conversation:
                messages[-1] = _cache_breakpoint(messages[-1])
        lines = [line for line in prior_receipts or [] if line.strip()]
        if lines:
            messages.append(
                _system_note("Already saved in this request:\n" + "\n".join(lines))
            )
        if routed.clock is not None:
            messages.append(_system_note(routed.clock()))
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

    async def _provider_turn(self, agent: AgentSession) -> ProviderTurn:
        _log_provider_request(agent.messages)
        complete_turn = getattr(self.provider, "complete_turn", None)
        if complete_turn is None:
            raw = await self.provider.complete(agent.messages)
            turn = ProviderTurn(content=raw)
        else:
            turn = await complete_turn(
                agent.messages,
                tools=list(agent.tools),
                # A subagent was routed to for the work, so its first move is the work.
                # Only the first: the loop ends on a turn that calls no tool, and a
                # session that must always call one never ends.
                tool_choice=(
                    "required"
                    if agent.tool_count == 0 and agent.kind in self.subagents
                    else None
                ),
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
                        "function": {"name": call.name, "arguments": call.arguments},
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
                                        "status": "error",
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
                    elif call.name in agent.read_specs:
                        result = await self._execute_read_tool(agent, call)
                    elif has_reads and has_mutations:
                        result = {
                            "status": "error",
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
                        if change.entity == "card" and change.action == "create"
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
        self, agent: AgentSession, call: ProviderToolCall
    ) -> tuple[dict[str, Any], AIOutcome | None]:
        """Run the named subagent and hand back its receipt.

        The second value is set only when the subagent opened a screen: it has not
        finished, so this session suspends with it and the owner sees that screen.
        """
        try:
            name = RouteInput.model_validate(json.loads(call.arguments or "{}")).name.strip()
        except (ValidationError, json.JSONDecodeError, TypeError, ValueError) as error:
            return {
                "status": "error",
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
                "status": "error",
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

    async def _execute_read_tool(
        self, agent: AgentSession, call: ProviderToolCall
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
                        "arguments": call.arguments,
                    },
                )
            )
            await session.commit()
        logger.info("AI TOOL %s(%s)", call.name, _log_preview(call.arguments, 200))
        return result

    async def _execute_query_tool(
        self, agent: AgentSession, call: ProviderToolCall
    ) -> list[dict[str, Any]]:
        if call.name != "query_safwa":
            rows: list[dict[str, Any]] = [
                {
                    "status": "error",
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
                arguments = json.loads(call.arguments)
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
                        "status": "error",
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
                        "status": "error",
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
                        "arguments": call.arguments,
                        "sql": sql,
                        "row_count": len(rows),
                        "columns": list(rows[0]) if rows else [],
                        "result": _json_safe(rows),
                    },
                )
            )
            await session.commit()
        return rows

    async def _execute_mutation_tool(
        self, agent: AgentSession, call: ProviderToolCall
    ) -> tuple[AgentChange | None, dict[str, Any]]:
        arguments: Any = None
        try:
            arguments = json.loads(call.arguments)
            if not isinstance(arguments, dict):
                raise ValueError("Tool arguments must be an object")
            change = mutation_change_from_tool(call.name, arguments)
        except (ValueError, ValidationError, json.JSONDecodeError) as error:
            logger.info("AI TOOL %s rejected: %s", call.name, error)
            error_text = (
                _validation_error_summary(error)
                if isinstance(error, ValidationError)
                else str(error)
            )
            result = {
                "status": "error",
                "code": "invalid_arguments",
                "error": error_text,
                "hint": (
                    "Retry only this unfinished tool call using expected_arguments and the "
                    "argument_rules below; do not repeat successful calls."
                ),
                "retryable": True,
            }
            if isinstance(arguments, dict):
                result.update(_mutation_repair_details(call.name, arguments))
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
                        "arguments": call.arguments,
                        "tool": call.name,
                        "entity": change.entity,
                        "action": change.action,
                        "id": change.id,
                    },
                )
            )
            await session.commit()
        return change, {
            "status": "prepared",
            "entity": change.entity,
            "action": change.action,
            "id": change.id,
            "next": "Wait for the user's review or approval; do not say it is complete.",
        }

    async def _create_proposal(
        self,
        session: AsyncSession,
        message: str,
        tool: PendingTool,
    ) -> ChangeProposal:
        """Persist one prepared mutation tool call as its own reviewable proposal.

        Every mutation call gets its own proposal screen, so a proposal always holds
        exactly one change.  Cross-proposal references resolve by name against
        committed data once the earlier proposal has been saved.
        """
        workspace = await session.get(Workspace, 1)
        if workspace is None:
            raise DomainError("Workspace is missing")
        change = tool.change
        if change is None:
            raise DomainError("The proposal has no validated change to review")
        prepared = await self.preparer.prepare(session, change)
        proposal = ChangeProposal(
            message=message,
            workspace_revision=workspace.revision,
            expires_at=utcnow() + timedelta(hours=24),
        )
        session.add(proposal)
        await session.flush()
        session.add(
            ProposalChange(
                proposal_id=proposal.id,
                position=0,
                entity=change.entity,
                action=change.action,
                entity_id=change.id,
                expected_version=prepared.expected_version,
                values=prepared.values,
            )
        )
        return proposal

    async def _card_detail_snapshot(
        self, session: AsyncSession, card: Card
    ) -> dict[str, Any]:
        return {
            "kind": card.kind,
            "title": card.title,
            "note": card.note,
            "stage": card.effective_stage,
            "priority": card.priority,
            "hard_time": card.hard_time,
            "blocked": card.blocked,
            "blocked_description": card.blocked_description,
            "effort_points": card.effort_points,
            "repeatable": card.repeatable,
            "categories": sorted(
                await session.scalars(
                    select(CardCategory.category).where(CardCategory.card_id == card.id)
                )
            ),
            "energy_types": sorted(
                await session.scalars(
                    select(CardEnergyType.energy_type).where(CardEnergyType.card_id == card.id)
                )
            ),
            "parent_id": card.parent_id,
            "values": [
                f"#{item}"
                for item in sorted(
                    await session.scalars(
                        select(CardValue.value_id).where(CardValue.card_id == card.id)
                    )
                )
            ],
            "tags": [
                f"#{item}"
                for item in sorted(
                    await session.scalars(
                        select(CardTag.tag_id).where(CardTag.card_id == card.id)
                    )
                )
            ],
        }

    async def _reference_groups(
        self, session: AsyncSession, values: dict[str, Any]
    ) -> list[str]:
        """Name the Values, Tags and Checks a Card payload points at, for the owner."""
        groups: list[str] = []
        for spec in CARD_REFERENCE_SPECS:
            if not spec.mentioned_in(values):
                continue
            resolved = await resolve_references(session, spec, values)
            names: list[str] = []
            for entity_id in sorted(resolved.ids):
                entity = await session.get(spec.model, entity_id)
                if entity is not None:
                    names.append(_result_value(getattr(entity, spec.name_attr)))
            names.extend(_result_value(name) for name in resolved.unresolved)
            if len(names) == 1:
                groups.append(f"{spec.label} “{names[0]}”")
            elif names:
                groups.append(f"{spec.label}s {', '.join(f'“{name}”' for name in names)}")
        return groups

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
        change = await session.scalar(
            select(ProposalChange).where(ProposalChange.proposal_id == proposal_id)
        )
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
        values = dict(change.values)
        action = change.action
        verb = _ACTION_VERBS.get(action, action.title())
        if change.entity == "card":
            card = (
                await session.get(Card, change.entity_id)
                if change.entity_id is not None
                else None
            )
            title = _result_value(values.get("title") or (card.title if card else ""))
            kind = str(values.get("kind") or (card.kind if card else "") or "card")
            head = f"{kind.title()} “{title}”" if title else f"Card #{change.entity_id}"
            parent = (
                await session.get(Card, int(values["parent_id"]))
                if values.get("parent_id")
                else None
            )
            if action in {"link", "unlink"}:
                joined = " · ".join(await self._reference_groups(session, values))
                preposition = "to" if action == "link" else "from"
                return f"{verb} {joined} {preposition} {head}" if joined else f"{verb} {head}"
            parts: list[str] = []
            if action == "create":
                # Only what was actually chosen: the defaults a new Card lands on say
                # nothing, and a receipt naming them buries the fields that do.
                stage = str(values.get("stage") or CardStage.BACKLOG.value)
                if stage != CardStage.BACKLOG.value:
                    parts.append(stage.title())
                priority = str(values.get("priority") or Priority.MEDIUM.value)
                if priority != Priority.MEDIUM.value:
                    parts.append(priority.title())
                if values.get("effort_points"):
                    parts.append(f"{values['effort_points']} EP")
                for field_name in ("categories", "energy_types"):
                    if chosen := values.get(field_name):
                        parts.append(_detail_value(chosen))
                if values.get("hard_time"):
                    parts.append("Hard time")
                if values.get("repeatable"):
                    parts.append("Repeatable")
                if values.get("blocked"):
                    parts.append("Blocked")
                parts.extend(await self._reference_groups(session, values))
            elif action in {"move", "reopen"} and values.get("stage"):
                parts.append(str(values["stage"]).title())
            elif action == "update":
                parts.extend(
                    detail for detail in details if not detail.startswith("Parent ID:")
                )
            if parent is not None:
                head += f" under {parent.kind.title()} “{_result_value(parent.title)}”"
            return f"{verb} {head}" + (f" ({' · '.join(parts)})" if parts else "")

        if change.entity == "check" and action in {"complete", "cancel"}:
            check = (
                await session.get(Check, change.entity_id)
                if change.entity_id is not None
                else None
            )
            outcome = CHECK_ANSWER_ACTIONS[action]
            head = f"“{_result_value(check.title)}”" if check else f"#{change.entity_id}"
            return f"Answer Check {head} ({CHECK_OUTCOME_LABELS[outcome]})"
        if change.entity == "diary":
            label = f"{verb} Diary entry for {values.get('entry_date', '')}".strip()
            score = values.get("feeling_score")
            return label if score is None else f"{label} with feeling score {score}"
        if change.entity == "reminder":
            reminder = (
                await session.get(Reminder, change.entity_id)
                if change.entity_id is not None
                else None
            )
            text = str(values.get("instruction") or (reminder.instruction if reminder else ""))
            head = f"Reminder “{_result_value(text)}”" if text else f"Reminder #{change.entity_id}"
            schedule = values.get("schedule_text")
            return f"{verb} {head}" + (f" ({schedule})" if schedule else "")
        model = ENTITY_MODELS.get(change.entity)
        entity = (
            await session.get(model, change.entity_id)
            if model is not None and change.entity_id is not None
            else None
        )
        name = (
            values.get("name")
            or values.get("title")
            or getattr(entity, "name", None)
            or getattr(entity, "title", None)
        )
        label = change.entity.title()
        head = f"{label} “{_result_value(name)}”" if name else f"{label} #{change.entity_id}"
        tail = [] if action == "create" else list(details)
        return f"{verb} {head}" + (f" ({' · '.join(tail)})" if tail else "")

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
        proposed_change = await session.scalar(
            select(ProposalChange).where(ProposalChange.proposal_id == proposal_id)
        )
        if proposed_change is None:
            return _raw_change_details(fallback)
        values = dict(proposed_change.values)
        if proposed_change.entity == "card":
            if proposed_change.action == "create":
                proposed = _normalized_card_details(values, creating=True)
                return [
                    f"{_DETAIL_LABELS.get(field, field.replace('_', ' ').title())}: "
                    f"{_detail_value(value)}"
                    for field, value in proposed.items()
                ]
            card = (
                await session.get(Card, proposed_change.entity_id)
                if proposed_change.entity_id is not None
                else None
            )
            if card is None:
                return _raw_change_details(fallback) or _stored_change_details(proposed_change)
            before = await self._card_detail_snapshot(session, card)
            if proposed_change.action in {"link", "unlink"}:
                relationship = _normalized_card_details(values, creating=False)
                verb = "Link" if proposed_change.action == "link" else "Unlink"
                return [
                    f"{verb} {_DETAIL_LABELS.get(field, field.title())}: {_detail_value(value)}"
                    for field, value in relationship.items()
                ]
            proposed = _normalized_card_details(values, creating=False)
            if proposed_change.action == "move":
                proposed = {"stage": values.get("stage")}
            elif proposed_change.action == "complete":
                proposed = {"stage": CardStage.DONE.value}
            elif proposed_change.action == "cancel":
                proposed = {"stage": CardStage.CANCELLED.value}
            elif proposed_change.action == "reopen":
                proposed = {"stage": values.get("stage", CardStage.BACKLOG.value)}
            elif proposed_change.action in {"archive", "delete"}:
                return [f"Card: {card.kind.title()} #{card.id} “{card.title}”"]
            return [
                f"{_DETAIL_LABELS.get(field, field.replace('_', ' ').title())}: "
                f"{_detail_value(before.get(field))} → {_detail_value(value)}"
                for field, value in proposed.items()
                if before.get(field) != value
            ]

        if proposed_change.entity == "check":
            return await self._check_detail_lines(session, proposed_change)
        if proposed_change.entity == "diary":
            return _diary_detail_lines(proposed_change)

        model = {
            "tag": Tag,
            "value": Value,
            "request": SavedRequest,
        }.get(proposed_change.entity)
        if proposed_change.action == "create" or model is None:
            return _raw_change_details(fallback) or _stored_change_details(proposed_change)
        entity = (
            await session.get(model, proposed_change.entity_id)
            if proposed_change.entity_id is not None
            else None
        )
        if entity is None:
            return _raw_change_details(fallback) or _stored_change_details(proposed_change)
        if proposed_change.action in {"archive", "delete"}:
            label = getattr(entity, "name", f"#{entity.id}")
            return [f"Item: {_result_value(label)}"]
        return [
            f"{_DETAIL_LABELS.get(field, field.replace('_', ' ').title())}: "
            f"{_detail_value(getattr(entity, field, None))} → {_detail_value(value)}"
            for field, value in values.items()
            if getattr(entity, field, None) != value
        ]

    async def _check_detail_lines(
        self, session: AsyncSession, proposed_change: ProposalChange
    ) -> list[str]:
        values = dict(proposed_change.values)
        proposed = {name: values[name] for name in ("title", "repeatable") if name in values}
        if proposed_change.action in {"complete", "cancel"}:
            proposed["outcome"] = CHECK_ANSWER_ACTIONS[proposed_change.action]
        check = (
            await session.get(Check, proposed_change.entity_id)
            if proposed_change.entity_id is not None
            else None
        )
        if proposed_change.action == "create" or check is None:
            return [
                f"{_DETAIL_LABELS.get(field, field.replace('_', ' ').title())}: "
                f"{_detail_value(value)}"
                for field, value in proposed.items()
            ]
        if proposed_change.action == "archive":
            return [f"Check: #{check.id} “{_result_value(check.title)}”"]
        before = {
            "title": check.title,
            "repeatable": check.repeatable,
            "outcome": check.outcome or "pending",
        }
        return [
            f"{_DETAIL_LABELS.get(field, field.replace('_', ' ').title())}: "
            f"{_detail_value(before.get(field))} → {_detail_value(value)}"
            for field, value in proposed.items()
            if before.get(field) != value
        ]

    @staticmethod
    def _target_outcome(message: str, target: dict[str, Any]) -> AIOutcome:
        return AIOutcome("proposal", message, proposal_id=int(target["id"]))

    async def _materialize(
        self,
        agent: AgentSession,
        result: AgentLoopResult,
    ) -> AIOutcome:
        if not result.pending_tools:
            return self._answer(agent, result.message)
        mutation_tools = [tool for tool in result.pending_tools if tool.change is not None]
        targets: list[tuple[int, dict[str, Any], list[PendingTool]]] = []
        preparation_results = {tool.call.id: _json_safe(tool.result) for tool in result.pending_tools}
        failed_call_ids = {
            tool.call.id
            for tool in result.pending_tools
            if tool.call.name not in IMMEDIATE_TOOLS and tool.change is None
        }
        proposal_details: dict[str, list[str]] = {}
        proposal_displays: dict[str, str] = {}
        async with self.sessions() as session:
            for tool in mutation_tools:
                try:
                    proposal = await self._create_proposal(session, result.message, tool)
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
                targets.append(
                    (
                        result.pending_tools.index(tool),
                        {"type": "proposal", "id": proposal.id},
                        [tool],
                    )
                )
            targets.sort(key=lambda item: item[0])
            tool_targets: dict[str, dict[str, Any]] = {}
            queue: list[dict[str, Any]] = []
            for position, (_index, target, tools) in enumerate(targets, start=1):
                call_ids = [tool.call.id for tool in tools]
                queue_item = {**target, "call_ids": call_ids, "status": "pending"}
                queue.append(queue_item)
                if len(targets) > 1 and target["type"] == "proposal":
                    proposal = await session.get(ChangeProposal, int(target["id"]))
                    if proposal is not None:
                        proposal.message = f"Proposal {position}/{len(targets)}\n{result.message}"
                for call_id in call_ids:
                    tool_targets[call_id] = target
            tool_results = []
            for tool in result.pending_tools:
                target = tool_targets.get(tool.call.id)
                tool_results.append(
                    {
                        "id": tool.call.id,
                        "name": tool.call.name,
                        "arguments": tool.call.arguments,
                        "status": "pending" if target else "resolved",
                        "result": None
                        if target
                        else _with_queued_siblings(
                            preparation_results[tool.call.id], len(targets)
                        ),
                        "details": proposal_details.get(tool.call.id)
                        or _raw_change_details(tool.change),
                        "display": proposal_displays.get(tool.call.id),
                        "target": target,
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
            if targets:
                repair_exhausted = bool(
                    failed_call_ids and agent.repair_rounds >= MAX_REPAIR_ROUNDS
                )
                if failed_call_ids:
                    agent.repair_rounds += 1
                session.add(
                    AgentStep(
                        run_id=agent.run_id,
                        position=max(
                            (
                                step.position
                                for step in await session.scalars(
                                    select(AgentStep).where(AgentStep.run_id == agent.run_id)
                                )
                            ),
                            default=0,
                        )
                        + 1,
                        kind="approval_batch",
                        # The batch is the screens this suspension opened, nothing more:
                        # what the session must remember to continue lives on its own row.
                        metadata_json={
                            "status": "pending",
                            "repair_exhausted": repair_exhausted,
                            "tool_calls": tool_results,
                            "queue": queue,
                        },
                    )
                )
                run = await session.get(AgentRun, agent.run_id)
                if run is not None:
                    run.state_json = agent.state()
            await session.commit()
        if not targets:
            if failed_call_ids:
                if agent.repair_rounds >= MAX_REPAIR_ROUNDS:
                    return AIOutcome(
                        "answer",
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
            self._target_outcome(result.message, targets[0][1]),
            held_run_id=agent.run_id,
        )

    async def _autoapproval_candidate(
        self, proposal_id: int
    ) -> tuple[int, AutoApprovalCandidate] | None:
        """Build the reviewer's request-only view for the active head of one batch."""
        async with self.sessions() as session:
            batch = await self._pending_batch_for_target(session, "proposal", proposal_id)
            if batch is None:
                return None
            metadata = dict(batch.metadata_json or {})
            head = next(
                (item for item in metadata.get("queue", []) if item.get("status") == "pending"),
                None,
            )
            if (
                head is None
                or head.get("type") != "proposal"
                or int(head.get("id", 0)) != proposal_id
            ):
                return None
            change = await session.scalar(
                select(ProposalChange).where(ProposalChange.proposal_id == proposal_id)
            )
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
            return batch.id, AutoApprovalCandidate(
                owner_request=request,
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
        if self.autoapproval is None or outcome.kind != "proposal" or outcome.proposal_id is None:
            return outcome
        loaded = await self._autoapproval_candidate(outcome.proposal_id)
        if loaded is None:
            return outcome
        _batch_id, candidate = loaded
        verdict = await self.autoapproval.review(candidate)
        if not verdict.approved:
            return outcome
        try:
            advanced = await self.resolve_approval(
                "proposal",
                outcome.proposal_id,
                decision="approved",
                result={
                    "approval_source": "auto",
                    "autoapproval_reason": verdict.reason,
                },
                apply_proposal=True,
                held_run_id=held_run_id,
            )
        except Exception as error:
            # `apply_proposal` and the batch decision share one transaction. A failure
            # therefore leaves the original pending proposal safe to render as-is.
            logger.warning(
                "Autoapproval apply failed for proposal #%s; keeping manual review: %s",
                outcome.proposal_id,
                error,
            )
            return outcome
        return advanced or AIOutcome("answer", "⚡ Auto-saved the proposed change.")

    async def _pending_batch_for_target(
        self,
        session: AsyncSession,
        target_type: str,
        target_id: int,
    ) -> AgentStep | None:
        # Suspended batches are always recent: new dialogue cancels them, and a batch is
        # closed the moment its last item resolves.  Filtering and bounding this in SQL
        # keeps the lookup off the full agent-step history.
        steps = list(
            await session.scalars(
                select(AgentStep)
                .where(
                    AgentStep.kind == "approval_batch",
                    AgentStep.metadata_json["status"].as_string() == "pending",
                )
                .order_by(AgentStep.id.desc())
                .limit(SUSPENDED_BATCH_LOOKUP_LIMIT)
            )
        )
        for step in steps:
            metadata = dict(step.metadata_json or {})
            if any(
                item.get("type") == target_type and int(item.get("id", 0)) == target_id
                for item in metadata.get("queue", [])
            ):
                return step
        return None

    async def has_pending_approval(self, target_type: str, target_id: int) -> bool:
        """Return whether a UI target belongs to a suspended agent turn."""
        async with self.sessions() as session:
            return await self._pending_batch_for_target(session, target_type, target_id) is not None

    async def _refresh_queued_proposal(
        self,
        session: AsyncSession,
        proposal_id: int,
    ) -> None:
        """Snapshot a proposal when it becomes visible after earlier batch decisions."""
        proposal = await session.get(ChangeProposal, proposal_id)
        workspace = await session.get(Workspace, 1)
        if proposal is None or workspace is None or proposal.status != ProposalStatus.PENDING.value:
            return
        proposal.workspace_revision = workspace.revision
        changes = list(
            await session.scalars(
                select(ProposalChange).where(ProposalChange.proposal_id == proposal_id)
            )
        )
        models = {"card": Card, "tag": Tag, "value": Value, "request": SavedRequest}
        for change in changes:
            model = models.get(change.entity)
            if model is None or change.entity_id is None:
                continue
            entity = await session.get(model, change.entity_id)
            change.expected_version = entity.version if entity is not None else None

    async def resolve_approval(
        self,
        target_type: str,
        target_id: int,
        *,
        decision: str,
        result: dict[str, Any],
        dialogue: list[DialogueMessage] | None = None,
        apply_proposal: bool = False,
        held_run_id: int | None = None,
    ) -> AIOutcome | None:
        """Resolve one queued UI target and resume the suspended tool turn once complete."""
        started = time.monotonic()
        next_outcome: AIOutcome | None = None
        async with self.sessions() as session:
            batch = await self._pending_batch_for_target(session, target_type, target_id)
            if batch is None:
                return None
            if apply_proposal:
                if target_type != "proposal" or decision != "approved":
                    raise DomainError("Only an approved proposal can be applied while resolving")
                affected = await ProposalService(session).apply(target_id)
                result = {**result, "affected_ids": affected}
            if decision == "failed" and target_type == "proposal":
                proposal = await session.get(ChangeProposal, target_id)
                if proposal is not None and proposal.status == ProposalStatus.PENDING.value:
                    proposal.status = ProposalStatus.FAILED.value
            metadata = dict(batch.metadata_json or {})
            queue = [dict(item) for item in metadata.get("queue", [])]
            tools = [dict(item) for item in metadata.get("tool_calls", [])]
            resolved_call_ids: set[str] = set()
            found = False
            for item in queue:
                if item.get("type") == target_type and int(item.get("id", 0)) == target_id:
                    found = True
                    if item.get("status") == "pending":
                        item["status"] = decision
                    resolved_call_ids.update(str(value) for value in item.get("call_ids", []))
            if not found:
                return None
            for tool in tools:
                if str(tool.get("id")) in resolved_call_ids:
                    tool["status"] = "resolved"
                    tool["result"] = _resolved_tool_result(tool, decision, result)
            next_target = next((item for item in queue if item.get("status") == "pending"), None)
            metadata.update({"queue": queue, "tool_calls": tools})
            if next_target is not None:
                if next_target.get("type") == "proposal":
                    await self._refresh_queued_proposal(session, int(next_target["id"]))
                metadata["status"] = "pending"
                batch.metadata_json = metadata
                await session.commit()
                next_outcome = self._target_outcome(
                    "Review the next proposed change.",
                    next_target,
                )
            else:
                # The queue is empty, so the batch has done its whole job.  Closing it and
                # claiming the session in the same commit is what makes a crash here cost
                # nothing: no half-open batch is left to route a later press into, and the
                # session is left plainly interrupted.
                metadata["status"] = "completed"
                batch.metadata_json = metadata
                run = await self._claim_session(
                    session, batch.run_id, held_run_id=held_run_id
                )
                if run is None:
                    logger.warning("Session #%s is already resuming", batch.run_id)
                    await session.commit()
                    return None
                run_id = run.id
                repair_exhausted = bool(metadata.get("repair_exhausted", False))
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
                await self._finish_run(run_id, "completed", started)
                return await self._answer_or_deliver(agent, self._answer(agent, exhausted))
            messages = await self._session_messages(agent)
            agent.prefix_len = len(messages)
            messages.extend(_resumed_transcript(stored_transcript, tools))
            agent.messages = messages
            loop_result = await self._run_agent_loop(agent)
            if loop_result.suspended is not None:
                await self._suspend_for_child(agent)
                await self._finish_run(run_id, "awaiting_approval", started)
                return loop_result.suspended
            outcome = await self._materialize(agent, loop_result)
            if outcome.kind == "proposal":
                await self._finish_run(run_id, "awaiting_approval", started)
                return outcome
            await self._finish_run(run_id, "completed", started)
            return await self._answer_or_deliver(agent, outcome)
        except Exception as error:
            logger.exception("AI continuation failed after the approval queue was resolved")
            await self._finish_run(run_id, "failed", started, type(error).__name__)
            result_summary = _safe_approval_results_summary(tools, for_display=True)
            if result_summary:
                return AIOutcome(
                    "answer",
                    _compose_display_outcome(
                        f"⚠️ Safwa could not generate its follow-up ({failure_reason(error)}). "
                        "You can continue with a new message.",
                        [result_summary],
                    ),
                )
            raise

    async def cancel_approval_for_target(self, target_type: str, target_id: int) -> str | None:
        """Freeze a suspended batch when new dialogue supersedes its active UI.

        Returns the consolidated result of the interrupted request, or ``None`` when the
        target does not belong to a suspended batch.  The caller needs that text because
        earlier items in the queue may already be saved: freezing the screen as a plain
        "discarded" notice would tell both the owner and the model something untrue.

        The Advisor's session is superseded by the message that arrived, but a subagent's
        stays waiting: the owner's words go to the Advisor, and "the same, but capitalise
        the name" has to reach the session that wrote the refused proposal.  Its own
        results are folded into its transcript first, so it resumes on a settled record.
        """
        async with self.sessions() as session:
            batch = await self._pending_batch_for_target(session, target_type, target_id)
            if batch is None:
                return None
            metadata = dict(batch.metadata_json or {})
            queue = [dict(item) for item in metadata.get("queue", [])]
            tools = [dict(item) for item in metadata.get("tool_calls", [])]
            pending_call_ids: set[str] = set()
            for item in queue:
                if item.get("status") != "pending":
                    continue
                item["status"] = "discarded"
                pending_call_ids.update(str(value) for value in item.get("call_ids", []))
                item_type = str(item.get("type"))
                item_id = int(item.get("id", 0))
                if item_type == "proposal":
                    proposal = await session.get(ChangeProposal, item_id)
                    if proposal is not None and proposal.status == ProposalStatus.PENDING.value:
                        proposal.status = ProposalStatus.REJECTED.value
            for tool in tools:
                if str(tool.get("id")) in pending_call_ids:
                    tool["status"] = "resolved"
                    tool["result"] = {
                        "status": "discarded",
                        "reason": "The user continued with a new message.",
                    }
            metadata.update({"status": "cancelled", "queue": queue, "tool_calls": tools})
            batch.metadata_json = metadata
            run = await session.get(AgentRun, batch.run_id)
            prior_summaries: list[str] = []
            if run is not None:
                state = dict(run.state_json or {})
                prior_summaries = list(state.get("display_result_summaries") or [])
                if run.kind == "advisor":
                    run.status = "cancelled"
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
                    if caller is None or caller.status != "awaiting_approval":
                        break
                    caller.status = "cancelled"
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
            .values(claimed_at=utcnow(), status="running")
            .returning(AgentRun.id)
        )
        return None if claimed is None else await session.get(AgentRun, run_id)

    async def _finish_run(
        self, run_id: int, status: str, started: float, error_code: str | None = None
    ) -> None:
        async with self.sessions() as session:
            run = await session.get(AgentRun, run_id)
            if run:
                run.status = status
                run.duration_ms = int((time.monotonic() - started) * 1000)
                run.error_code = error_code
                # The turn is over either way, so the session is free for the next resume.
                run.claimed_at = None
                await session.commit()


class ProposalService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _parent_id(self, values: dict[str, Any]) -> int | None:
        return int(values["parent_id"]) if values.get("parent_id") is not None else None

    async def _apply_reminder_change(self, change: ProposalChange, affected: list[int]) -> None:
        """Save an approved Reminder through the same domain calls the UI uses.

        The schedule travels in the values blob, already resolved, so Save writes what the
        review screen showed.
        """
        workspace = await self.session.get(Workspace, 1)
        tz = ZoneInfo(workspace.timezone if workspace else "UTC")
        values = dict(change.values)
        payload = values.get("schedule")
        if change.action == "create":
            if payload is None:
                raise DomainError("A new Reminder needs a schedule")
            reminder = await create_reminder(
                self.session,
                instruction=str(values.get("instruction", "")),
                schedule=schedule_from_payload(payload),
                tz=tz,
            )
            affected.append(reminder.id)
            return
        if change.entity_id is None:
            raise DomainError("This Reminder change has no target")
        if change.action == "delete":
            await delete_reminder(self.session, change.entity_id)
            affected.append(change.entity_id)
            return
        if change.action != "update":
            raise DomainError(f"Unsupported approved Reminder action: {change.action}")
        reminder = await self.session.get(Reminder, change.entity_id)
        if reminder is None or reminder.version != change.expected_version:
            raise StaleStateError("A Reminder changed; refresh this proposal")
        if values.get("instruction"):
            await update_reminder_text(self.session, reminder.id, str(values["instruction"]))
        if payload is not None:
            await reschedule_reminder(
                self.session, reminder.id, schedule=schedule_from_payload(payload), tz=tz
            )
        affected.append(reminder.id)

    async def _named_ids(self, values: dict[str, Any], spec: ReferenceSpec) -> set[int]:
        """Resolve one relationship at approval time against committed data.

        A proposal holds one change, so a name referenced here always belongs to an
        item an earlier proposal already saved.
        """
        resolved = await resolve_references(self.session, spec, values)
        if resolved.unresolved:
            raise DomainError(
                f"{spec.label} '{resolved.unresolved[0]}' is not available for this approved link"
            )
        # Unknown numeric IDs stay for the domain command to reject with its own message.
        return resolved.ids | set(resolved.unknown_ids)

    async def _apply_stage_change(self, card: Card, stage: CardStage) -> None:
        """Route one approved stage change so terminal stages keep their accounting.

        ``finish_action`` owns completion timestamps, feedback, Sprint results and repeat
        successors; ``move_card`` owns live stages and subtree propagation.  Every approved
        stage change goes through here so no path can reach Done or Cancelled without the
        completion bookkeeping.
        """
        if stage in TERMINAL_STAGES:
            await finish_action(self.session, card.id, stage, actor=ActorType.AI)
            return
        await move_card(self.session, card.id, stage, actor=ActorType.AI)

    async def _replace_card_sets(self, card: Card, values: dict[str, Any]) -> None:
        if "categories" in values:
            current = set(
                await self.session.scalars(
                    select(CardCategory.category).where(CardCategory.card_id == card.id)
                )
            )
            target = set(values["categories"] or [])
            for category in sorted(current ^ target):
                await toggle_card_category(
                    self.session, card.id, Category(category), actor=ActorType.AI
                )
        if "energy_types" in values:
            current = set(
                await self.session.scalars(
                    select(CardEnergyType.energy_type).where(CardEnergyType.card_id == card.id)
                )
            )
            target = set(values["energy_types"] or [])
            for energy_type in sorted(current ^ target):
                await toggle_card_energy_type(
                    self.session, card.id, EnergyType(energy_type), actor=ActorType.AI
                )

        for spec in CARD_REFERENCE_SPECS:
            if not spec.mentioned_in(values):
                continue
            current = set(
                await self.session.scalars(
                    select(spec.link_column).where(spec.link_model.card_id == card.id)
                )
            )
            target = await self._named_ids(values, spec)
            for entity_id in sorted(current ^ target):
                await spec.toggle(self.session, card.id, entity_id, actor=ActorType.AI)

    async def _apply_card_links(
        self,
        card: Card,
        values: dict[str, Any],
        *,
        linked: bool,
    ) -> None:
        """Add or remove exactly one relationship type, leaving the others untouched."""
        for spec in CARD_REFERENCE_SPECS:
            if not spec.mentioned_in(values):
                continue
            for entity_id in sorted(await self._named_ids(values, spec)):
                exists = await self.session.get(spec.link_model, spec.link_key(card.id, entity_id))
                if linked != (exists is not None):
                    await spec.toggle(self.session, card.id, entity_id, actor=ActorType.AI)
            return
        raise DomainError("A Card link proposal needs one relationship type")

    async def _apply_check_change(self, change: ProposalChange, affected: list[int]) -> None:
        values = dict(change.values)
        if change.action == "create":
            created = await create_check(
                self.session,
                title=str(values["title"]),
                repeatable=bool(values.get("repeatable", False)),
            )
            affected.append(created.id)
            return
        check = await self.session.get(Check, change.entity_id) if change.entity_id else None
        if check is None or check.version != change.expected_version:
            raise StaleStateError("A Check changed; refresh this proposal")
        if change.action == "update":
            scalar_fields = {
                name: value for name, value in values.items() if name in {"title", "repeatable"}
            }
            if scalar_fields:
                await update_check_fields(self.session, check.id, scalar_fields)
        elif change.action in CHECK_ANSWER_ACTIONS:
            await resolve_check(
                self.session, check.id, CHECK_ANSWER_ACTIONS[change.action], actor=ActorType.AI
            )
        elif change.action == "archive":
            await archive_check(self.session, check.id)
        else:
            raise DomainError(f"Unsupported Check action: {change.action}")
        affected.append(check.id)

    async def _apply_diary_change(self, change: ProposalChange, affected: list[int]) -> None:
        values = dict(change.values)
        entry_date = date.fromisoformat(str(values["entry_date"]))
        feeling_score = values.get("feeling_score")
        if change.action == "create":
            entry = await create_diary_entry(
                self.session,
                entry_date=entry_date,
                body=str(values.get("body", "")),
                feeling_score=feeling_score,
            )
            affected.append(entry.id)
        elif change.action in {"update", "delete"}:
            entry = (
                await self.session.get(DiaryEntry, change.entity_id)
                if change.entity_id
                else None
            )
            if entry is None or entry.version != change.expected_version:
                raise StaleStateError("The Diary entry changed; refresh this proposal")
            entry_id = entry.id
            if change.action == "delete":
                await delete_diary_entry(self.session, entry_id)
            else:
                await update_diary_entry(
                    self.session, entry_id, str(values.get("body", "")), feeling_score
                )
            affected.append(entry_id)
        else:
            raise DomainError(f"Unsupported approved Diary action: {change.action}")

    async def apply(self, proposal_id: int, *, allow_destructive: bool = False) -> list[int]:
        proposal = await self.session.get(ChangeProposal, proposal_id)
        if proposal is None or proposal.status != ProposalStatus.PENDING.value:
            raise DomainError("Proposal is no longer pending")
        workspace = await self.session.get(Workspace, 1)
        if workspace is None or workspace.revision != proposal.workspace_revision:
            proposal.status = ProposalStatus.STALE.value
            raise StaleStateError("Planning state changed; refresh this proposal")
        changes = list(
            await self.session.scalars(
                select(ProposalChange)
                .where(ProposalChange.proposal_id == proposal.id)
                .order_by(ProposalChange.position)
            )
        )
        affected: list[int] = []
        for change in changes:
            if change.entity == "card":
                if change.action == "create":
                    values = dict(change.values)
                    value_ids = await self._named_ids(values, VALUE_REFERENCE)
                    tag_ids = await self._named_ids(values, TAG_REFERENCE)
                    check_ids = await self._named_ids(values, CHECK_REFERENCE)
                    card = await create_card(
                        self.session,
                        kind=values["kind"],
                        title=values["title"],
                        note=values.get("note", ""),
                        stage=values.get("stage", CardStage.BACKLOG.value),
                        priority=values.get("priority", "medium"),
                        hard_time=bool(values.get("hard_time", False)),
                        blocked=bool(values.get("blocked", False)),
                        blocked_description=values.get("blocked_description", ""),
                        effort_points=values.get("effort_points"),
                        repeatable=bool(values.get("repeatable", False)),
                        parent_id=await self._parent_id(values),
                        categories=set(values.get("categories") or []),
                        energy_types=set(values.get("energy_types") or []),
                        value_ids=value_ids,
                        tag_ids=tag_ids,
                        check_ids=check_ids,
                        actor=ActorType.AI,
                    )
                    affected.append(card.id)
                    continue
                card = await self.session.get(Card, change.entity_id) if change.entity_id else None
                if card is None or card.version != change.expected_version:
                    proposal.status = ProposalStatus.STALE.value
                    raise StaleStateError("A Card changed; refresh this proposal")
                if change.action == "move":
                    await self._apply_stage_change(card, CardStage(change.values["stage"]))
                elif change.action == "complete":
                    await finish_action(self.session, card.id, CardStage.DONE, actor=ActorType.AI)
                elif change.action == "cancel":
                    await finish_action(
                        self.session, card.id, CardStage.CANCELLED, actor=ActorType.AI
                    )
                elif change.action == "reopen":
                    await self._apply_stage_change(
                        card,
                        CardStage(change.values.get("stage", CardStage.BACKLOG.value)),
                    )
                elif change.action == "update":
                    scalar_fields = {
                        name: value
                        for name, value in change.values.items()
                        if name
                        in {
                            "title",
                            "note",
                            "priority",
                            "hard_time",
                            "blocked",
                            "blocked_description",
                            "effort_points",
                            "repeatable",
                        }
                    }
                    if scalar_fields:
                        await update_card_fields(
                            self.session, card.id, scalar_fields, actor=ActorType.AI
                        )
                    if "parent_id" in change.values:
                        await set_card_parent(
                            self.session,
                            card.id,
                            change.values["parent_id"],
                            actor=ActorType.AI,
                        )
                    if "stage" in change.values:
                        await self._apply_stage_change(card, CardStage(change.values["stage"]))
                    await self._replace_card_sets(card, change.values)
                elif change.action == "archive":
                    await archive_subtree(self.session, card.id)
                elif change.action == "delete":
                    if not allow_destructive:
                        raise DomainError("Permanent deletion needs a second confirmation")
                    await delete_subtree(self.session, card.id)
                elif change.action in {"link", "unlink"}:
                    await self._apply_card_links(
                        card,
                        change.values,
                        linked=change.action == "link",
                    )
                else:
                    raise DomainError(f"Unsupported approved Card action: {change.action}")
                affected.append(card.id)
            elif change.entity == "check":
                await self._apply_check_change(change, affected)
            elif change.entity == "diary":
                await self._apply_diary_change(change, affected)
            elif change.entity == "reminder":
                await self._apply_reminder_change(change, affected)
            elif change.entity == "tag":
                tag = await self.session.get(Tag, change.entity_id) if change.entity_id else None
                if change.action == "create":
                    name = str(change.values.get("name", change.values.get("title", ""))).strip()
                    if not name:
                        raise DomainError("A new Tag needs a name")
                    tag = await create_tag(
                        self.session,
                        name,
                        change.values.get("description"),
                    )
                    await self.session.flush()
                else:
                    if tag is None or tag.version != change.expected_version:
                        raise StaleStateError("A Tag changed; refresh this proposal")
                    if change.action == "update":
                        tag = await update_tag_fields(
                            self.session,
                            tag.id,
                            name=change.values.get("name"),
                            description=change.values.get("description"),
                        )
                    elif change.action == "archive":
                        tag, _unlinked_count = await archive_tag(self.session, tag.id)
                    else:
                        raise DomainError(f"Unsupported Tag action: {change.action}")
                affected.append(tag.id)
            elif change.entity == "value":
                value = (
                    await self.session.get(Value, change.entity_id) if change.entity_id else None
                )
                if change.action == "create":
                    name = str(change.values["name"]).strip()
                    if not name:
                        raise DomainError("A new Value needs a name")
                    value = await create_value(
                        self.session,
                        name,
                        change.values.get("description"),
                        active=change.values.get("active"),
                    )
                    await self.session.flush()
                else:
                    if value is None or value.version != change.expected_version:
                        raise StaleStateError("A Value changed; refresh this proposal")
                    if change.action == "update":
                        value = await update_value_fields(
                            self.session,
                            value.id,
                            name=change.values.get("name"),
                            description=change.values.get("description"),
                            active=change.values.get("active"),
                        )
                    elif change.action == "archive":
                        value, _unlinked_count = await archive_value(self.session, value.id)
                    else:
                        raise DomainError(f"Unsupported Value action: {change.action}")
                affected.append(value.id)
            elif change.entity == "request":
                request = (
                    await self.session.get(SavedRequest, change.entity_id)
                    if change.entity_id
                    else None
                )
                if change.action == "create":
                    request = await create_saved_request(
                        self.session,
                        str(change.values["name"]),
                        change.values["query_sql"],
                        change.values.get("description"),
                    )
                elif request is None or request.version != change.expected_version:
                    raise StaleStateError("A Request changed; refresh this proposal")
                elif change.action == "update":
                    request = await update_saved_request(
                        self.session,
                        request.id,
                        name=(str(change.values["name"]) if "name" in change.values else None),
                        description=(
                            str(change.values["description"])
                            if "description" in change.values
                            else None
                        ),
                        query_sql=change.values.get("query_sql"),
                    )
                elif change.action == "archive":
                    request = await archive_saved_request(self.session, request.id)
                else:
                    raise DomainError(f"Unsupported Request action: {change.action}")
                affected.append(request.id)
            else:
                raise DomainError(f"Unsupported approved change: {change.entity}.{change.action}")
        proposal.status = ProposalStatus.APPROVED.value
        return affected

    async def reject(self, proposal_id: int) -> None:
        proposal = await self.session.get(ChangeProposal, proposal_id)
        if proposal and proposal.status == ProposalStatus.PENDING.value:
            proposal.status = ProposalStatus.REJECTED.value
