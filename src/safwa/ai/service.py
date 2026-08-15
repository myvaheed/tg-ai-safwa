from __future__ import annotations

import json
import logging
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..constants import (
    MAX_REPAIR_ROUNDS,
    MAX_TOOL_CALLS,
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
    create_reminder,
    create_saved_request,
    create_tag,
    create_value,
    delete_reminder,
    delete_subtree,
    finish_action,
    move_card,
    pending_checks,
    reschedule_reminder,
    resolve_check,
    resolve_references,
    set_card_parent,
    toggle_card_category,
    toggle_card_energy_type,
    update_card_fields,
    update_check_fields,
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
    ProposalStatus,
)
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
    ProposalChange,
    Reminder,
    SavedRequest,
    Tag,
    Value,
    Workspace,
)
from ..reminders import (
    ScheduleError,
    describe,
    schedule_from_payload,
    schedule_payload,
)
from ..saved_requests import RequestQueryError, normalize_request_sql
from .context import SYSTEM_PROMPT, DialogueMessage, planning_context
from .contracts import (
    MUTATION_TOOL_MODELS,
    AgentChange,
    QueryToolInput,
    mutation_change_from_tool,
    tool_json_schema,
)
from .provider import OpenAICompatibleProvider, ProviderToolCall, ProviderTurn
from .reminder_sessions import resolve_schedule
from .sql import ReadOnlyQueryRunner, UnsafeQueryError

logger = logging.getLogger(__name__)

def _allows_parent(child_kind: str | None, parent_kind: str | None) -> bool:
    if child_kind == CardKind.IDEA.value:
        return parent_kind == CardKind.GOAL.value
    if child_kind == CardKind.ACTION.value:
        return parent_kind in {CardKind.GOAL.value, CardKind.IDEA.value}
    return False

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
        "Open the Card review UI. create proposes a new Card; edit proposes exact "
        "field/set replacements; link and unlink add or remove one relationship type — Values, "
        "Tags, or Checks, since a Card owns all three links; move, complete, cancel, and reopen "
        "propose only that lifecycle action. Omit unused properties or send null; never invent "
        "placeholder IDs such as 0 or 1. In edit, parent_id=null removes the parent. Nothing is "
        "saved until the "
        "user presses Save."
    ),
    "check": (
        "Open the Check review UI. create proposes a new Pending Check; edit proposes a new title "
        "or repeatable flag; complete answers it Passed and cancel answers it Missed. Propose an "
        "answer only when the user already stated it — otherwise cite it so they answer it "
        "themselves. A Check is attached to a Card from the card tool (link/unlink with "
        "check_query or check_ids), never from here. Nothing is saved until the user presses Save."
    ),
    "value": "Open the Value editor with a creation or edit proposal;",
    "tag": "Open the Tag editor with a creation or edit proposal;",
    "request": "Prepare a saved Request creation or edit proposal.",
    "reminder": (
        "Propose a Reminder: instruction text plus timing in plain words. The text is handed "
        "to you as a request when the time comes, so it must stand on its own and must name "
        "every Safwa item it concerns by #id — look the id up with query_safwa first. "
        "Pass the timing through verbatim in when; never invent a date or an hour. Omit when "
        "in edit mode to change only the text and leave the schedule alone."
    ),
    "remove": "Prepare an archive or permanent Card-deletion confirmation.",
}
MUTATION_TOOLS: tuple[dict[str, Any], ...] = tuple(
    {
        "type": "function",
        "function": {
            "name": name,
            "description": MUTATION_TOOL_DESCRIPTIONS[name],
            "parameters": tool_json_schema(model),
        },
    }
    for name, model in MUTATION_TOOL_MODELS.items()
)
SAFWA_TOOLS = (QUERY_SAFWA_TOOL, *MUTATION_TOOLS)
# Tools that run during the turn instead of becoming a proposal the owner approves.
IMMEDIATE_TOOLS = frozenset({"query_safwa"})


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


class ToolPreparationError(DomainError):
    """A model-visible error for one mutation call, not for the whole agent turn."""

    def __init__(self, code: str, message: str, hint: str) -> None:
        super().__init__(message)
        self.code = code
        self.hint = hint

    def as_tool_result(self) -> dict[str, Any]:
        return {
            "status": "error",
            "code": self.code,
            "error": str(self),
            "hint": self.hint,
            "retryable": True,
        }


@dataclass
class AIOutcome:
    kind: str
    message: str
    proposal_id: int | None = None


@dataclass
class PendingTool:
    call: ProviderToolCall
    result: Any
    change: AgentChange | None = None


@dataclass
class AgentLoopResult:
    message: str
    pending_tools: list[PendingTool] = field(default_factory=list)
    assistant_content: str | None = None
    tool_count: int = 0
    repair_rounds: int = 0
    messages: list[dict[str, Any]] = field(default_factory=list)
    prefix_len: int = 0
    result_summaries: list[str] = field(default_factory=list)
    display_result_summaries: list[str] = field(default_factory=list)

    @property
    def transcript(self) -> list[dict[str, Any]]:
        """The assistant/tool exchanges this request produced, without its context prefix.

        Everything before ``prefix_len`` is rebuilt from live state on every turn; this
        tail is what an approval must replay so the model keeps its own intermediate
        steps instead of re-planning the request from the last tool call alone.
        """
        return self.messages[self.prefix_len :]


def failure_reason(error: Exception, limit: int = 160) -> str:
    """One short owner-readable clause; the traceback stays in the log."""
    text = " ".join(str(error).split()) or type(error).__name__
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _log_preview(content: str, limit: int = 500) -> str:
    compact = " ".join(content.split())
    return compact if len(compact) <= limit else compact[: limit - 3] + "..."


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


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


def _raw_change_details(change: AgentChange | None) -> list[str]:
    if change is None:
        return []
    values = (
        _normalized_card_details(change.values, creating=change.action == "create")
        if change.entity == "card"
        else dict(change.values)
    )
    return [
        f"{_DETAIL_LABELS.get(field, field.replace('_', ' ').title())}: {_detail_value(value)}"
        for field, value in values.items()
    ]


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

_ENTITY_MODELS: dict[str, Any] = {
    "card": Card,
    "check": Check,
    "tag": Tag,
    "value": Value,
    "request": SavedRequest,
    "reminder": Reminder,
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


def _assistant_content_with_request_progress(
    content: str | None, result_summaries: list[str]
) -> str | None:
    parts: list[str] = []
    if result_summaries:
        parts.append(
            "[Current request progress — temporary]\n"
            "Do not repeat Saved or Discarded operations. Retry only unfinished Failed operations.\n"
            + "\n\n".join(result_summaries)
        )
    if content and content.strip():
        parts.append(content.strip())
    return "\n\n".join(parts) or None


_DECISION_NEXT_STEPS = {
    "approved": (
        "This change is saved. Do not propose it again. Continue with the parts of the user's "
        "request that are still unfinished, then answer."
    ),
    "discarded": (
        "The user rejected this change, so it does not exist. Do not retry it unless the user "
        "asks again. Continue with the rest of the request, then answer."
    ),
    "failed": (
        "Applying this change failed, so nothing was written for it. Read `error`, fix only this "
        "call, and retry it once; every other resolved call in this request stands."
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
    ) -> None:
        self.sessions = sessions
        self.provider = provider
        self.memory = memory
        self.query_runner = query_runner
        self.model_name = model_name
        self.provider_name = provider_name
        self.cache_breakpoints = cache_breakpoints

    async def handle(
        self,
        text: str,
        *,
        source_message_id: int | None = None,
        dialogue: list[DialogueMessage] | None = None,
    ) -> AIOutcome:
        started = time.monotonic()
        run = AgentRun(
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
            result = await self._run_agent_loop(messages, run.id, prefix_len=len(messages))
            outcome = await self._materialize(result, run.id, dialogue=turn_dialogue)
            status = "awaiting_approval" if outcome.kind == "proposal" else "completed"
            await self._finish_run(run.id, status, started)
            return outcome
        except Exception as error:
            logger.exception("AI advisor run failed")
            await self._finish_run(run.id, "failed", started, type(error).__name__)
            raise

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
            {
                "role": "system",
                "content": (
                    f"Current planning state:\n{context.state}"
                    f"\n\nPersistent memory:\n{memory.text}"
                ),
            },
        ]
        # The history source has already bounded the window by its token budget.
        messages.extend({"role": item.role, "content": item.content} for item in dialogue)
        if self.cache_breakpoints:
            messages[0] = _cache_breakpoint(messages[0])
            messages[1] = _cache_breakpoint(messages[1])
            if dialogue:
                messages[-1] = _cache_breakpoint(messages[-1])
        messages.append({"role": "system", "content": context.clock})
        return messages

    async def _provider_turn(self, messages: list[dict[str, Any]]) -> ProviderTurn:
        _log_provider_request(messages)
        complete_turn = getattr(self.provider, "complete_turn", None)
        if complete_turn is None:
            raw = await self.provider.complete(messages)
            turn = ProviderTurn(content=raw)
        else:
            turn = await complete_turn(
                messages,
                tools=list(SAFWA_TOOLS),
            )
        _log_provider_response(turn)
        return turn

    async def _run_agent_loop(
        self,
        messages: list[dict[str, Any]],
        run_id: int,
        *,
        prefix_len: int,
        tool_count: int = 0,
        repair_rounds: int = 0,
        allow_silence: bool = False,
    ) -> AgentLoopResult:
        """Run the model until it answers.

        ``allow_silence`` accepts an empty final answer, which is only meaningful when
        the owner has already been shown the request's results: after an approval queue
        the model may have nothing left to add, and that is not a failure.
        """
        while True:
            turn = await self._provider_turn(messages)
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
                pending_tools: list[PendingTool] = []
                has_reads = any(call.name in IMMEDIATE_TOOLS for call in turn.tool_calls)
                has_mutations = any(call.name not in IMMEDIATE_TOOLS for call in turn.tool_calls)
                for call in turn.tool_calls:
                    tool_count += 1
                    if tool_count > MAX_TOOL_CALLS:
                        raise DomainError("The advisor exceeded the tool-call limit")
                    change = None
                    if call.name == "query_safwa":
                        result = await self._execute_query_tool(call, run_id, tool_count)
                    elif has_reads and has_mutations:
                        result = {
                            "status": "error",
                            "code": "mixed_read_and_mutation_tools",
                            "error": "Mutation tools cannot share a response with query_safwa.",
                            "next": (
                                "Use the read result, then retry this mutation in the next response."
                            ),
                            "retryable": True,
                        }
                    else:
                        change, result = await self._execute_mutation_tool(call, run_id, tool_count)
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
                        assistant_content=turn.content or None,
                        tool_count=tool_count,
                        repair_rounds=repair_rounds,
                        messages=_json_safe(messages),
                        prefix_len=prefix_len,
                    )
                invalid_mutations = [
                    tool
                    for tool in pending_tools
                    if tool.call.name not in IMMEDIATE_TOOLS and tool.change is None
                ]
                if invalid_mutations:
                    if repair_rounds >= MAX_REPAIR_ROUNDS:
                        return AgentLoopResult(
                            message=(
                                "I could not prepare the requested change after five repair attempts. "
                                "No unfinished operation was applied."
                            ),
                            tool_count=tool_count,
                            repair_rounds=repair_rounds,
                            messages=_json_safe(messages),
                            prefix_len=prefix_len,
                        )
                    repair_rounds += 1
                continue

            if not turn.content and not allow_silence:
                raise DomainError("The advisor finished without a response")
            return AgentLoopResult(
                turn.content,
                tool_count=tool_count,
                repair_rounds=repair_rounds,
                messages=_json_safe(messages),
                prefix_len=prefix_len,
            )

    def mini_query_tool(self) -> tuple[dict[str, Any], Callable[..., Any]]:
        """`query_safwa` for a mini-session: the same guarded reader, no run bookkeeping.

        A mini-session has no ``AgentRun``, so it cannot use ``_execute_query_tool``; the
        query runner and its caps are shared, which is the part that matters.
        """

        async def read(call: ProviderToolCall) -> list[dict[str, Any]]:
            try:
                query = QueryToolInput.model_validate(json.loads(call.arguments or "{}"))
                outcome = await self.query_runner.run(query.sql)
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

        return QUERY_SAFWA_TOOL, read

    async def _execute_query_tool(
        self, call: ProviderToolCall, run_id: int, position: int
    ) -> list[dict[str, Any]]:
        if call.name != "query_safwa":
            rows: list[dict[str, Any]] = [
                {
                    "status": "error",
                    "code": "unknown_tool",
                    "error": f"Unknown tool: {call.name}",
                    "hint": (
                        "Call one of: query_safwa, card, check, value, tag, request, reminder, remove."
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
                    run_id=run_id,
                    position=position,
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
        self, call: ProviderToolCall, run_id: int, position: int
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
                    run_id=run_id,
                    position=position,
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

    async def _validate_named_references(
        self,
        session: AsyncSession,
        values: dict[str, Any],
        spec: ReferenceSpec,
    ) -> None:
        """Reject a relationship the owner could not act on, with a retryable hint."""
        reference_hint = (
            "The referenced item may have been proposed but is not saved yet. Wait for the "
            "earlier proposal result, then retry only this unfinished operation using the returned ID."
        )
        resolved = await resolve_references(session, spec, values)
        if resolved.blank:
            raise ToolPreparationError(
                "invalid_arguments",
                f"{spec.label} name must not be empty.",
                f"Provide one exact {spec.label} name or its numeric ID.",
            )
        if resolved.unknown_ids:
            raise ToolPreparationError(
                "reference_not_found",
                f"{spec.label} #{resolved.unknown_ids[0]} does not exist or is archived.",
                reference_hint,
            )
        if resolved.missing:
            raise ToolPreparationError(
                "reference_not_found",
                f"{spec.label} '{resolved.missing[0]}' was not found.",
                reference_hint,
            )
        if resolved.ambiguous:
            raise ToolPreparationError(
                "reference_ambiguous",
                f"{spec.label} '{resolved.ambiguous[0]}' matched more than one item.",
                f"Use query_safwa to choose one {spec.label} and retry with its numeric ID.",
            )

    async def _resolve_parent_reference(
        self,
        session: AsyncSession,
        values: dict[str, Any],
        child_kind: str | None,
    ) -> None:
        reference_hint = (
            "The parent may have been proposed but is not saved yet. Wait for the earlier proposal "
            "result, then retry only this unfinished Card operation using the returned parent ID."
        )
        raw_parent_query = values.pop("parent_query", None)
        if raw_parent_query is not None:
            parent_query = str(raw_parent_query).strip()
            if not parent_query:
                raise ToolPreparationError(
                    "invalid_arguments",
                    "Parent query must not be empty.",
                    "Provide one exact Card title, one numeric parent_id, or a safe SELECT returning id.",
                )
            if parent_query.casefold().startswith(("select", "with")):
                try:
                    # Only the rows matter here; a parent query must match exactly one Card,
                    # so a capped result is reported as ambiguous rather than silently used.
                    rows = (await self.query_runner.run(normalize_request_sql(parent_query))).rows
                except (RequestQueryError, UnsafeQueryError) as error:
                    raise ToolPreparationError(
                        "unsafe_query",
                        f"Invalid parent query: {error}",
                        "Use one read-only SELECT over ai_cards that returns only the id column.",
                    ) from error
                except (sqlite3.Error, TimeoutError) as error:
                    raise ToolPreparationError(
                        "invalid_arguments",
                        f"Parent query failed: {error}",
                        "Correct the SELECT and retry only this unfinished Card operation.",
                    ) from error
                if not rows:
                    raise ToolPreparationError(
                        "reference_not_found",
                        "The parent query returned no Cards.",
                        reference_hint,
                    )
                if len(rows) > 1:
                    raise ToolPreparationError(
                        "reference_ambiguous",
                        "The parent query returned more than one Card.",
                        "Narrow the query to one Card and retry with its numeric ID.",
                    )
                if set(rows[0]) != {"id"} or not isinstance(rows[0]["id"], int):
                    raise ToolPreparationError(
                        "invalid_arguments",
                        "The parent query must return exactly one integer id column.",
                        "Use SELECT id FROM ai_cards ... and make it match one Card.",
                    )
                values["parent_id"] = rows[0]["id"]
            else:
                matches = list(
                    await session.scalars(
                        select(Card).where(
                            Card.title.collate("NOCASE") == parent_query,
                            Card.archived_at.is_(None),
                        )
                    )
                )
                if not matches:
                    raise ToolPreparationError(
                        "reference_not_found",
                        f"Parent Card '{parent_query}' was not found.",
                        reference_hint,
                    )
                if len(matches) > 1:
                    raise ToolPreparationError(
                        "reference_ambiguous",
                        f"Parent Card '{parent_query}' matched more than one Card.",
                        "Use query_safwa to choose one parent and retry with its numeric ID.",
                    )
                values["parent_id"] = matches[0].id

        parent_id = values.get("parent_id")
        if parent_id is None:
            return
        parent = await session.get(Card, int(parent_id))
        if parent is None or parent.archived_at is not None:
            raise ToolPreparationError(
                "reference_not_found",
                f"Parent Card #{parent_id} does not exist or is archived.",
                reference_hint,
            )
        if not _allows_parent(child_kind, parent.kind):
            raise ToolPreparationError(
                "invalid_parent_kind",
                f"A {child_kind or 'Card'} cannot have a {parent.kind} parent.",
                "Goal is root-only; Idea may be under Goal; Action may be under Goal or Idea.",
            )

    async def _create_proposal(
        self,
        session: AsyncSession,
        message: str,
        tool: PendingTool,
    ) -> ChangeProposal:
        """Persist one validated mutation tool call as its own reviewable proposal.

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
        entity: Card | Check | Reminder | Tag | Value | SavedRequest | None = None
        expected_version = None
        if change.id and change.entity in _ENTITY_MODELS:
            entity = await session.get(_ENTITY_MODELS[change.entity], change.id)
            expected_version = entity.version if entity else None
        if change.id is not None and (
            entity is None or getattr(entity, "archived_at", None) is not None
        ):
            raise ToolPreparationError(
                "target_not_found",
                f"{change.entity.title()} #{change.id} does not exist or is archived.",
                "Use query_safwa to find the current numeric ID, then retry only this unfinished operation.",
            )
        values = dict(change.values)
        proposed_kind = (
            values.get("kind")
            if change.entity == "card" and change.action == "create"
            else getattr(entity, "kind", None)
        )
        if change.entity == "card" and proposed_kind != CardKind.ACTION.value:
            for action_only_field in {
                "effort_points",
                "repeatable",
                "categories",
                "energy_types",
            }:
                values.pop(action_only_field, None)
            if proposed_kind == CardKind.GOAL.value and (
                values.get("parent_id") is not None or values.get("parent_query") is not None
            ):
                raise ToolPreparationError(
                    "invalid_parent_kind",
                    "A Goal is always root-level and cannot take a parent.",
                    "Drop the parent from this call, or propose an Idea or Action instead.",
                )
            if change.action == "update" and not values:
                raise DomainError("The Card proposal contains no applicable fields")
        if change.entity == "card":
            await self._resolve_parent_reference(session, values, str(proposed_kind))
            for spec in CARD_REFERENCE_SPECS:
                await self._validate_named_references(session, values, spec)
            await self._guard_pending_checks(session, change, values)
        if change.entity == "reminder":
            values = await self._prepare_reminder_values(session, workspace, values)
        if change.entity == "request" and "sql" in values:
            try:
                values["query_sql"] = normalize_request_sql(values.pop("sql"))
            except RequestQueryError as error:
                raise ToolPreparationError(
                    "unsafe_query",
                    f"Invalid Request SQL: {error}",
                    "Use one read-only SELECT over ai_cards that returns an id column.",
                ) from error

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
                expected_version=expected_version,
                values=values,
            )
        )
        return proposal

    async def _guard_pending_checks(
        self, session: AsyncSession, change: AgentChange, values: dict[str, Any]
    ) -> None:
        """Refuse to prepare a completion while the Card still has Pending Checks.

        The error is model-visible and retryable, and it carries the titles so the model
        does not have to spend a `query_safwa` round discovering them.
        """
        completing = change.action == "complete" or (
            change.action in {"move", "update"} and values.get("stage") == CardStage.DONE.value
        )
        if not completing or change.id is None:
            return
        pending = await pending_checks(session, int(change.id))
        if not pending:
            return
        listed_checks = ", ".join(f"#{check.id} “{check.title}”" for check in pending)
        raise ToolPreparationError(
            "pending_checks",
            f"Card #{change.id} still has Pending Checks: {listed_checks}.",
            "Answer each one first: check(mode='complete'|'cancel', id=…) when the user already "
            "said how it went, otherwise cite them as [title](check:<id>) so they answer them "
            "themselves. Then retry only this unfinished completion.",
        )

    async def _prepare_reminder_values(
        self, session: AsyncSession, workspace: Workspace, values: dict[str, Any]
    ) -> dict[str, Any]:
        """Resolve the free-text timing before the proposal row exists, so the review screen
        shows a real schedule and Save applies exactly what the owner approved.

        An unresolvable phrase becomes a retryable tool error carrying the question to ask.
        """
        prepared = dict(values)
        when = str(prepared.pop("when", "") or "").strip()
        if not when:
            return prepared  # an edit with no timing leaves the schedule alone
        tz = ZoneInfo(workspace.timezone)
        now = utcnow()
        try:
            schedule = await resolve_schedule(
                self.provider,
                when=when,
                instruction=str(prepared.get("instruction", "")),
                now=now,
                tz=tz,
            )
        except ScheduleError as error:
            raise ToolPreparationError(
                "schedule_unclear",
                str(error),
                "Ask the owner this exact question, then call reminder again with their "
                "answer in when. Never invent a time.",
            ) from error
        prepared["schedule"] = schedule_payload(schedule)
        prepared["schedule_text"] = describe(schedule, tz=tz, now=now)
        return prepared

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
                parts.append(str(values.get("stage") or CardStage.BACKLOG.value).title())
                if values.get("effort_points"):
                    parts.append(f"{values['effort_points']} EP")
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
        model = _ENTITY_MODELS.get(change.entity)
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
                return _raw_change_details(fallback)
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

        model = {
            "tag": Tag,
            "value": Value,
            "request": SavedRequest,
        }.get(proposed_change.entity)
        if proposed_change.action == "create" or model is None:
            return _raw_change_details(fallback)
        entity = (
            await session.get(model, proposed_change.entity_id)
            if proposed_change.entity_id is not None
            else None
        )
        if entity is None:
            return _raw_change_details(fallback)
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
        result: AgentLoopResult,
        run_id: int,
        *,
        dialogue: list[dict[str, Any]],
    ) -> AIOutcome:
        if not result.pending_tools:
            return AIOutcome("answer", result.message)
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
                session.add(
                    AgentStep(
                        run_id=run_id,
                        position=max(
                            (
                                step.position
                                for step in await session.scalars(
                                    select(AgentStep).where(AgentStep.run_id == run_id)
                                )
                            ),
                            default=0,
                        )
                        + 1,
                        kind="approval_batch",
                        metadata_json={
                            "status": "pending",
                            "assistant_content": result.assistant_content,
                            "tool_count": result.tool_count,
                            "repair_rounds": result.repair_rounds
                            + (1 if failed_call_ids else 0),
                            "repair_exhausted": bool(
                                failed_call_ids and result.repair_rounds >= MAX_REPAIR_ROUNDS
                            ),
                            "result_summaries": result.result_summaries,
                            "display_result_summaries": result.display_result_summaries,
                            "tool_calls": tool_results,
                            "queue": queue,
                            # The suspended turn resumes from these, not from a fresh read:
                            # the request the owner actually made and everything the model
                            # already did for it inside this run.
                            "dialogue": dialogue,
                            "transcript": result.transcript,
                        },
                    )
                )
            await session.commit()
        if not targets:
            if failed_call_ids:
                if result.repair_rounds >= MAX_REPAIR_ROUNDS:
                    return AIOutcome(
                        "answer",
                        "I could not prepare the requested change after five repair attempts. "
                        "No unfinished operation was applied.",
                    )
                messages = _json_safe(result.messages)
                results_by_id = {tool["id"]: tool["result"] for tool in tool_results}
                for message in messages:
                    if message.get("role") != "tool":
                        continue
                    tool_call_id = str(message.get("tool_call_id"))
                    if tool_call_id in results_by_id:
                        message["content"] = json.dumps(
                            results_by_id[tool_call_id], ensure_ascii=False, default=str
                        )
                repaired = await self._run_agent_loop(
                    messages,
                    run_id,
                    prefix_len=result.prefix_len,
                    tool_count=result.tool_count,
                    repair_rounds=result.repair_rounds + 1,
                )
                repaired.result_summaries = list(result.result_summaries)
                repaired.display_result_summaries = list(result.display_result_summaries)
                if repaired.display_result_summaries:
                    repaired.message = (
                        "\n\n".join(repaired.display_result_summaries)
                        + f"\n\n{repaired.message}"
                    )
                return await self._materialize(repaired, run_id, dialogue=dialogue)
            return AIOutcome("answer", result.message)
        return self._target_outcome(result.message, targets[0][1])

    async def _pending_batch_for_target(
        self,
        session: AsyncSession,
        target_type: str,
        target_id: int,
    ) -> AgentStep | None:
        # Suspended batches are always recent: new dialogue cancels them and startup
        # recovery closes interrupted ones.  Filtering and bounding this in SQL keeps
        # the lookup off the full agent-step history.
        steps = list(
            await session.scalars(
                select(AgentStep)
                .where(
                    AgentStep.kind == "approval_batch",
                    AgentStep.metadata_json["status"].as_string().in_(["pending", "resuming"]),
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
    ) -> AIOutcome | None:
        """Resolve one queued UI target and resume the suspended tool turn once complete."""
        started = time.monotonic()
        async with self.sessions() as session:
            batch = await self._pending_batch_for_target(session, target_type, target_id)
            if batch is None:
                return None
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
                return self._target_outcome(
                    "Review the next proposed change.",
                    next_target,
                )
            metadata["status"] = "resuming"
            batch.metadata_json = metadata
            run_id = batch.run_id
            prior_tool_count = int(metadata.get("tool_count", 0))
            prior_repair_rounds = int(metadata.get("repair_rounds", 0))
            repair_exhausted = bool(metadata.get("repair_exhausted", False))
            prior_result_summaries = list(metadata.get("result_summaries", []))
            prior_display_result_summaries = list(
                metadata.get("display_result_summaries", [])
            )
            stored_transcript = [dict(item) for item in metadata.get("transcript") or []]
            turn_dialogue = [dict(item) for item in metadata.get("dialogue") or []] or [
                {"role": item.role, "content": item.content} for item in dialogue or []
            ]
            await session.commit()

        try:
            result_summary = _safe_approval_results_summary(tools)
            current_result_summaries = [*prior_result_summaries]
            if result_summary:
                current_result_summaries.append(result_summary)
            display_summary = _safe_approval_results_summary(
                tools, include_preparation_errors=False, for_display=True
            )
            current_display_result_summaries = [*prior_display_result_summaries]
            if display_summary:
                current_display_result_summaries.append(display_summary)
            if repair_exhausted:
                message = (
                    "\n\n".join(current_display_result_summaries) + "\n\n"
                    if current_display_result_summaries
                    else ""
                ) + (
                    "I could not prepare the remaining requested changes after five repair attempts. "
                    "No unfinished operation was applied."
                )
                async with self.sessions() as session:
                    stored_batch = await session.get(AgentStep, batch.id)
                    if stored_batch is not None:
                        final_metadata = dict(stored_batch.metadata_json or {})
                        final_metadata["status"] = "completed"
                        stored_batch.metadata_json = final_metadata
                        await session.commit()
                await self._finish_run(run_id, "completed", started)
                return AIOutcome("answer", message)
            messages = await self._context_messages(
                [
                    DialogueMessage(role=str(item["role"]), content=str(item["content"]))
                    for item in turn_dialogue
                ]
            )
            prefix_len = len(messages)
            if stored_transcript:
                messages.extend(_resumed_transcript(stored_transcript, tools))
            else:
                # A batch suspended before transcripts were persisted still has to resume.
                messages.append(
                    {
                        "role": "assistant",
                        "content": _assistant_content_with_request_progress(
                            metadata.get("assistant_content"), current_result_summaries
                        ),
                        "tool_calls": [
                            {
                                "id": tool["id"],
                                "type": "function",
                                "function": {
                                    "name": tool["name"],
                                    "arguments": tool["arguments"],
                                },
                            }
                            for tool in tools
                        ],
                    }
                )
                for tool in tools:
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool["id"],
                            "name": tool["name"],
                            "content": json.dumps(
                                tool.get("result"), ensure_ascii=False, default=str
                            ),
                        }
                    )
            loop_result = await self._run_agent_loop(
                messages,
                run_id,
                prefix_len=prefix_len,
                tool_count=prior_tool_count,
                repair_rounds=prior_repair_rounds,
                allow_silence=True,
            )
            loop_result.result_summaries = current_result_summaries
            loop_result.display_result_summaries = current_display_result_summaries
            if loop_result.display_result_summaries:
                loop_result.message = (
                    "\n\n".join(loop_result.display_result_summaries)
                    + f"\n\n{loop_result.message}"
                ).strip()
            elif not loop_result.message:
                # The model added nothing and there is no receipt to stand in for it, so
                # the resolved screen still has to say that the request is finished.
                loop_result.message = "✅ Done."
            outcome = await self._materialize(loop_result, run_id, dialogue=turn_dialogue)
            async with self.sessions() as session:
                stored_batch = await session.get(AgentStep, batch.id)
                if stored_batch is not None:
                    final_metadata = dict(stored_batch.metadata_json or {})
                    final_metadata["status"] = "completed"
                    stored_batch.metadata_json = final_metadata
                    await session.commit()
            run_status = "awaiting_approval" if outcome.kind == "proposal" else "completed"
            await self._finish_run(run_id, run_status, started)
            return outcome
        except Exception as error:
            logger.exception("AI continuation failed after the approval queue was resolved")
            async with self.sessions() as session:
                stored_batch = await session.get(AgentStep, batch.id)
                if stored_batch is not None:
                    final_metadata = dict(stored_batch.metadata_json or {})
                    final_metadata["status"] = "completed"
                    final_metadata["continuation_error"] = type(error).__name__
                    stored_batch.metadata_json = final_metadata
                    await session.commit()
            await self._finish_run(run_id, "failed", started, type(error).__name__)
            result_summary = _safe_approval_results_summary(tools, for_display=True)
            if result_summary:
                return AIOutcome(
                    "answer",
                    f"{result_summary}\n\n"
                    f"⚠️ Safwa could not generate its follow-up ({failure_reason(error)}). "
                    "You can continue with a new message.",
                )
            raise

    async def cancel_approval_for_target(self, target_type: str, target_id: int) -> str | None:
        """Cancel a whole suspended batch when new dialogue supersedes its active UI.

        Returns the consolidated result of the interrupted request, or ``None`` when the
        target does not belong to a suspended batch.  The caller needs that text because
        earlier items in the queue may already be saved: freezing the screen as a plain
        "discarded" notice would tell both the owner and the model something untrue.
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
            if run is not None:
                run.status = "cancelled"
            await session.commit()
        summaries = [
            *metadata.get("display_result_summaries", []),
            _safe_approval_results_summary(
                tools, include_preparation_errors=False, for_display=True
            ),
        ]
        return "\n\n".join(summary for summary in summaries if summary) or ""

    async def _finish_run(
        self, run_id: int, status: str, started: float, error_code: str | None = None
    ) -> None:
        async with self.sessions() as session:
            run = await session.get(AgentRun, run_id)
            if run:
                run.status = status
                run.duration_ms = int((time.monotonic() - started) * 1000)
                run.error_code = error_code
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
