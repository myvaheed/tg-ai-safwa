from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..domain import (
    DomainError,
    StaleStateError,
    archive_saved_request,
    archive_subtree,
    archive_tag,
    archive_value,
    create_card,
    create_saved_request,
    create_tag,
    create_value,
    delete_subtree,
    finish_action,
    finish_sprint,
    move_card,
    set_card_parent,
    start_sprint,
    toggle_card_category,
    toggle_card_energy_type,
    toggle_card_tag,
    toggle_card_value,
    update_card_fields,
    update_saved_request,
    update_tag_fields,
    update_value_fields,
    utcnow,
)
from ..enums import ActorType, CardKind, CardStage, Category, EnergyType, ProposalStatus
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
    ProposalChange,
    SavedRequest,
    Tag,
    UserProfile,
    Value,
    Workspace,
)
from ..saved_requests import RequestQueryError, normalize_request_sql
from .context import SYSTEM_PROMPT, DialogueMessage, planning_context
from .contracts import MUTATION_TOOL_MODELS, AgentChange, mutation_change_from_tool
from .provider import OpenAICompatibleProvider, ProviderToolCall, ProviderTurn
from .sql import ReadOnlyQueryRunner, UnsafeQueryError

logger = logging.getLogger(__name__)

def _allows_parent(child_kind: str | None, parent_kind: str | None) -> bool:
    if child_kind == CardKind.IDEA.value:
        return parent_kind == CardKind.GOAL.value
    if child_kind == CardKind.ACTION.value:
        return parent_kind in {CardKind.GOAL.value, CardKind.IDEA.value}
    return False

SUBSESSION_RESULT_PROMPT = """Compress this isolated Safwa planning/advisory branch into one concise
context message for the parent conversation. Preserve concrete outcomes, decisions, personal insights,
unresolved issues, and any planning implications. Write in the conversation's language. Do not mention
summaries, sessions, prompts, tools, SQL, or AI. Do not claim unapproved changes happened."""

QUERY_SAFWA_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "query_safwa",
        "description": (
            "Explore Safwa's current planning data with one safe, read-only SQLite SELECT. "
            "Use it to find Cards, Tags, Values, Requests, Sprint state, metrics, or events "
            "before answering or preparing a change proposal."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "One SELECT or WITH ... SELECT over allowlisted ai_* views.",
                }
            },
            "required": ["sql"],
            "additionalProperties": False,
        },
    },
}
MUTATION_TOOL_DESCRIPTIONS = {
    "card": (
        "Open the Card review UI. create proposes a new Card; edit proposes exact "
        "field/set replacements; link and unlink add or remove one relationship type; move, "
        "complete, cancel, and reopen propose only that lifecycle action. Nothing is saved until "
        "the user presses Save."
    ),
    "value": "Open the Value editor with a creation or edit proposal;",
    "tag": "Open the Tag editor with a creation or edit proposal;",
    "request": "Prepare a saved Request creation or edit proposal.",
    "remove": "Prepare an archive or permanent Card-deletion confirmation.",
}
MUTATION_TOOLS: tuple[dict[str, Any], ...] = tuple(
    {
        "type": "function",
        "function": {
            "name": name,
            "description": MUTATION_TOOL_DESCRIPTIONS[name],
            "parameters": model.model_json_schema(),
        },
    }
    for name, model in MUTATION_TOOL_MODELS.items()
)
SAFWA_TOOLS = (QUERY_SAFWA_TOOL, *MUTATION_TOOLS)
MAX_TOOL_CALLS = 64
MAX_REPAIR_ROUNDS = 5


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
    result_summaries: list[str] = field(default_factory=list)
    display_result_summaries: list[str] = field(default_factory=list)


def _log_preview(content: str, limit: int = 500) -> str:
    compact = " ".join(content.split())
    return compact if len(compact) <= limit else compact[: limit - 3] + "..."


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


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
    "values": "Values",
    "tags": "Tags",
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


def _approval_results_summary(
    tools: list[dict[str, Any]], *, include_preparation_errors: bool = True
) -> str:
    lines = ["Proposal results:"]
    for tool in tools:
        result = dict(tool.get("result") or {})
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
        line = f"{prefix} — {_approval_change_label(tool)}"
        affected_ids = result.get("affected_ids") or []
        if status == "approved" and affected_ids:
            line += " [result ID" + ("s" if len(affected_ids) != 1 else "") + ": "
            line += ", ".join(f"#{item}" for item in affected_ids) + "]"
        error = result.get("error")
        if error:
            line += f": {_result_value(error)}"
        lines.append(line)
        lines.extend(f"  • {detail}" for detail in tool.get("details") or [])
    return "\n".join(lines) if len(lines) > 1 else ""


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


def _log_provider_request(messages: list[dict[str, Any]]) -> None:
    lines: list[str] = []
    for message in messages:
        content = str(message.get("content") or "")
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
    ) -> None:
        self.sessions = sessions
        self.provider = provider
        self.memory = memory
        self.query_runner = query_runner
        self.model_name = model_name

    async def handle(
        self,
        text: str,
        *,
        source_message_id: int | None = None,
        dialogue: list[DialogueMessage] | None = None,
    ) -> AIOutcome:
        started = time.monotonic()
        run = AgentRun(
            provider="openai-compatible",
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
            result = await self._run_agent_loop(messages, run.id)
            outcome = await self._materialize(result, run.id)
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
            state = await planning_context(session)
        system_sections = [
            SYSTEM_PROMPT,
            f"Current planning state:\n{state}\n\nPersistent memory:\n{memory.text}",
        ]
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": "\n\n".join(system_sections)}
        ]
        # The history source has already applied the real Telegram session or
        # Summary boundary and the 20-message summary context policy.
        messages.extend({"role": item.role, "content": item.content} for item in dialogue)
        return messages

    async def compress_subsession(
        self, dialogue: list[DialogueMessage], instruction: str = ""
    ) -> str:
        """Return a compact, natural-language result before a subsession is removed."""
        transcript = "\n".join(f"[{item.role.title()}]: {item.content}" for item in dialogue)
        if not transcript.strip():
            raise DomainError("The subsession has no canonical Safwa dialogue to compress")
        suffix = f"\n\nUser instruction:\n{instruction.strip()}" if instruction.strip() else ""
        return await self.provider.complete(
            [
                {"role": "system", "content": SUBSESSION_RESULT_PROMPT},
                {
                    "role": "user",
                    "content": f"<subsession_history>\n{transcript}\n</subsession_history>{suffix}",
                },
            ],
            temperature=0.1,
        )

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
        tool_count: int = 0,
        repair_rounds: int = 0,
    ) -> AgentLoopResult:
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
                for call in turn.tool_calls:
                    tool_count += 1
                    if tool_count > MAX_TOOL_CALLS:
                        raise DomainError("The advisor exceeded the tool-call limit")
                    if call.name == "query_safwa":
                        result = await self._execute_query_tool(call, run_id, tool_count)
                    else:
                        change, result = await self._execute_mutation_tool(call, run_id, tool_count)
                    pending_tools.append(
                        PendingTool(
                            call=call,
                            result=result,
                            change=change if call.name != "query_safwa" else None,
                        )
                    )
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
                    )
                invalid_mutations = [
                    tool
                    for tool in pending_tools
                    if tool.call.name != "query_safwa" and tool.change is None
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
                        )
                    repair_rounds += 1
                continue

            if not turn.content:
                raise DomainError("The advisor finished without a response")
            return AgentLoopResult(
                turn.content,
                tool_count=tool_count,
                repair_rounds=repair_rounds,
                messages=_json_safe(messages),
            )

    async def _execute_query_tool(
        self, call: ProviderToolCall, run_id: int, position: int
    ) -> list[dict[str, Any]]:
        if call.name != "query_safwa":
            rows: list[dict[str, Any]] = [{"error": f"Unknown tool: {call.name}"}]
            sql = ""
        else:
            try:
                arguments = json.loads(call.arguments)
                sql = str(arguments["sql"])
                rows = await self.query_runner.run(sql)
            except (KeyError, TypeError, json.JSONDecodeError) as error:
                sql = ""
                rows = [{"error": f"Invalid tool arguments: {error}"}]
            except (UnsafeQueryError, TimeoutError, OSError) as error:
                rows = [{"error": str(error)}]
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
        try:
            arguments = json.loads(call.arguments)
            if not isinstance(arguments, dict):
                raise ValueError("Tool arguments must be an object")
            change = mutation_change_from_tool(call.name, arguments)
        except (ValueError, ValidationError, json.JSONDecodeError) as error:
            logger.info("AI TOOL %s rejected: %s", call.name, error)
            return None, {
                "status": "error",
                "code": "invalid_arguments",
                "error": str(error),
                "hint": "Correct only this unfinished tool call and retry it.",
                "retryable": True,
            }
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

    @staticmethod
    def _reference_items(value: Any) -> list[Any]:
        if value is None:
            return []
        return value if isinstance(value, list) else [value]

    async def _validate_named_references(
        self,
        session: AsyncSession,
        values: dict[str, Any],
        *,
        singular_key: str,
        plural_key: str,
        query_key: str,
        model: type[Tag] | type[Value],
        label: str,
    ) -> None:
        reference_hint = (
            "The referenced item may have been proposed but is not saved yet. Wait for the "
            "earlier proposal result, then retry only this unfinished operation using the returned ID."
        )
        raw_ids = [
            *self._reference_items(values.get(singular_key)),
            *self._reference_items(values.get(plural_key)),
        ]
        for raw_id in raw_ids:
            entity = await session.get(model, int(raw_id))
            if entity is None or entity.archived_at is not None:
                raise ToolPreparationError(
                    "reference_not_found",
                    f"{label} #{raw_id} does not exist or is archived.",
                    reference_hint,
                )
        for raw_name in self._reference_items(values.get(query_key)):
            name = str(raw_name).strip()
            if not name:
                raise ToolPreparationError(
                    "invalid_arguments",
                    f"{label} name must not be empty.",
                    f"Provide one exact {label} name or its numeric ID.",
                )
            matches = list(
                await session.scalars(
                    select(model).where(
                        model.name.collate("NOCASE") == name,
                        model.archived_at.is_(None),
                    )
                )
            )
            if not matches:
                raise ToolPreparationError(
                    "reference_not_found",
                    f"{label} '{name}' was not found.",
                    reference_hint,
                )
            if len(matches) > 1:
                raise ToolPreparationError(
                    "reference_ambiguous",
                    f"{label} '{name}' matched more than one item.",
                    f"Use query_safwa to choose one {label} and retry with its numeric ID.",
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
        if "parent_query" in values:
            parent_query = str(values.pop("parent_query")).strip()
            if not parent_query:
                raise ToolPreparationError(
                    "invalid_arguments",
                    "Parent query must not be empty.",
                    "Provide one exact Card title, one numeric parent_id, or a safe SELECT returning id.",
                )
            if parent_query.casefold().startswith(("select", "with")):
                try:
                    rows = await self.query_runner.run(normalize_request_sql(parent_query))
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
        tools: list[PendingTool],
    ) -> ChangeProposal:
        workspace = await session.get(Workspace, 1)
        if workspace is None:
            raise DomainError("Workspace is missing")
        prepared_changes: list[tuple[int, AgentChange, int | None, dict[str, Any]]] = []
        for index, tool in enumerate(tools):
            change = tool.change
            if change is None:
                continue
            entity: Card | Tag | Value | SavedRequest | None = None
            expected_version = None
            if change.id and change.entity == "card":
                entity = await session.get(Card, change.id)
                expected_version = entity.version if entity else None
            elif change.id and change.entity == "tag":
                entity = await session.get(Tag, change.id)
                expected_version = entity.version if entity else None
            elif change.id and change.entity == "request":
                entity = await session.get(SavedRequest, change.id)
                expected_version = entity.version if entity else None
            elif change.id and change.entity == "value":
                entity = await session.get(Value, change.id)
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
                if proposed_kind == CardKind.GOAL.value:
                    values.pop("parent_id", None)
                    values.pop("parent_query", None)
                if change.action == "update" and not values:
                    raise DomainError("The Card proposal contains no applicable fields")
            if change.entity == "card":
                await self._resolve_parent_reference(session, values, str(proposed_kind))
                await self._validate_named_references(
                    session,
                    values,
                    singular_key="tag_id",
                    plural_key="tag_ids",
                    query_key="tag_query",
                    model=Tag,
                    label="Tag",
                )
                await self._validate_named_references(
                    session,
                    values,
                    singular_key="value_id",
                    plural_key="value_ids",
                    query_key="value_query",
                    model=Value,
                    label="Value",
                )
            if change.entity == "request" and "sql" in values:
                try:
                    values["query_sql"] = normalize_request_sql(values.pop("sql"))
                except RequestQueryError as error:
                    raise ToolPreparationError(
                        "unsafe_query",
                        f"Invalid Request SQL: {error}",
                        "Use one read-only SELECT over ai_cards that returns an id column.",
                    ) from error
            prepared_changes.append((index, change, expected_version, values))

        proposal = ChangeProposal(
            message=message,
            workspace_revision=workspace.revision,
            expires_at=utcnow() + timedelta(hours=24),
        )
        session.add(proposal)
        await session.flush()
        for index, change, expected_version, values in prepared_changes:
            session.add(
                ProposalChange(
                    proposal_id=proposal.id,
                    position=index,
                    entity=change.entity,
                    action=change.action,
                    entity_id=change.id,
                    expected_version=expected_version,
                    values=values,
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

    @staticmethod
    def _target_outcome(message: str, target: dict[str, Any]) -> AIOutcome:
        return AIOutcome("proposal", message, proposal_id=int(target["id"]))

    async def _materialize(self, result: AgentLoopResult, run_id: int) -> AIOutcome:
        if not result.pending_tools:
            return AIOutcome("answer", result.message)
        mutation_tools = [tool for tool in result.pending_tools if tool.change is not None]
        targets: list[tuple[int, dict[str, Any], list[PendingTool]]] = []
        preparation_results = {tool.call.id: _json_safe(tool.result) for tool in result.pending_tools}
        failed_call_ids = {
            tool.call.id
            for tool in result.pending_tools
            if tool.call.name != "query_safwa" and tool.change is None
        }
        proposal_details: dict[str, list[str]] = {}
        async with self.sessions() as session:
            for tool in mutation_tools:
                try:
                    proposal = await self._create_proposal(session, result.message, [tool])
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
                        "result": None if target else preparation_results[tool.call.id],
                        "details": proposal_details.get(tool.call.id)
                        or _raw_change_details(tool.change),
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
                        ),
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
                return await self._materialize(repaired, run_id)
            return AIOutcome("answer", result.message)
        return self._target_outcome(result.message, targets[0][1])

    async def _pending_batch_for_target(
        self,
        session: AsyncSession,
        target_type: str,
        target_id: int,
    ) -> AgentStep | None:
        steps = list(
            await session.scalars(
                select(AgentStep)
                .where(AgentStep.kind == "approval_batch")
                .order_by(AgentStep.id.desc())
            )
        )
        for step in steps:
            metadata = dict(step.metadata_json or {})
            if metadata.get("status") not in {"pending", "resuming"}:
                continue
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
        dialogue: list[DialogueMessage],
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
            tool_result = {"status": decision, **_json_safe(result)}
            for tool in tools:
                if str(tool.get("id")) in resolved_call_ids:
                    tool["status"] = "resolved"
                    tool["result"] = tool_result
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
            await session.commit()

        try:
            result_summary = _approval_results_summary(tools)
            current_result_summaries = [*prior_result_summaries]
            if result_summary:
                current_result_summaries.append(result_summary)
            display_summary = _approval_results_summary(
                tools, include_preparation_errors=False
            )
            current_display_result_summaries = [*prior_display_result_summaries]
            if display_summary:
                current_display_result_summaries.append(display_summary)
            if repair_exhausted:
                message = (
                    "\n\n".join(current_result_summaries) + "\n\n"
                    if current_result_summaries
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
            messages = await self._context_messages(dialogue)
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
                        "content": json.dumps(tool.get("result"), ensure_ascii=False, default=str),
                    }
                )
            loop_result = await self._run_agent_loop(
                messages,
                run_id,
                tool_count=prior_tool_count,
                repair_rounds=prior_repair_rounds,
            )
            loop_result.result_summaries = current_result_summaries
            loop_result.display_result_summaries = current_display_result_summaries
            if loop_result.display_result_summaries:
                loop_result.message = (
                    "\n\n".join(loop_result.display_result_summaries)
                    + f"\n\n{loop_result.message}"
                ).strip()
            outcome = await self._materialize(loop_result, run_id)
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
            result_summary = _approval_results_summary(tools)
            if result_summary:
                return AIOutcome(
                    "answer",
                    f"{result_summary}\n\n"
                    "⚠️ Safwa could not generate its follow-up. "
                    "You can continue with a new message.",
                )
            raise

    async def cancel_approval_for_target(self, target_type: str, target_id: int) -> bool:
        """Cancel a whole suspended batch when new dialogue supersedes its active UI."""
        async with self.sessions() as session:
            batch = await self._pending_batch_for_target(session, target_type, target_id)
            if batch is None:
                return False
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
            return True

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

    @staticmethod
    def _items(value: Any) -> list[Any]:
        if value is None:
            return []
        return value if isinstance(value, list) else [value]

    async def _parent_id(self, values: dict[str, Any]) -> int | None:
        return int(values["parent_id"]) if values.get("parent_id") is not None else None

    async def _named_ids(
        self,
        values: dict[str, Any],
        *,
        singular_key: str,
        plural_key: str,
        query_key: str,
        model: type[Tag] | type[Value],
        label: str,
        created: dict[str, int],
    ) -> set[int]:
        ids = {
            int(item)
            for item in [
                *self._items(values.get(singular_key)),
                *self._items(values.get(plural_key)),
            ]
        }
        for raw_name in self._items(values.get(query_key)):
            name = str(raw_name).strip()
            if not name:
                continue
            entity_id = created.get(name.casefold())
            if entity_id is None:
                matches = list(
                    await self.session.scalars(
                        select(model).where(
                            model.name.collate("NOCASE") == name,
                            model.archived_at.is_(None),
                        )
                    )
                )
                if len(matches) == 1:
                    entity_id = matches[0].id
            if entity_id is None:
                raise DomainError(f"{label} '{name}' is not available for this approved link")
            ids.add(entity_id)
        return ids

    async def _replace_card_sets(
        self,
        card: Card,
        values: dict[str, Any],
        *,
        created_tag_ids: dict[str, int],
        created_value_ids: dict[str, int],
    ) -> None:
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

        value_fields = {"value_id", "value_ids", "value_query"}
        if value_fields & values.keys():
            current = set(
                await self.session.scalars(
                    select(CardValue.value_id).where(CardValue.card_id == card.id)
                )
            )
            target = await self._named_ids(
                values,
                singular_key="value_id",
                plural_key="value_ids",
                query_key="value_query",
                model=Value,
                label="Value",
                created=created_value_ids,
            )
            for value_id in sorted(current ^ target):
                await toggle_card_value(self.session, card.id, value_id, actor=ActorType.AI)

        tag_fields = {"tag_id", "tag_ids", "tag_query"}
        if tag_fields & values.keys():
            current = set(
                await self.session.scalars(select(CardTag.tag_id).where(CardTag.card_id == card.id))
            )
            target = await self._named_ids(
                values,
                singular_key="tag_id",
                plural_key="tag_ids",
                query_key="tag_query",
                model=Tag,
                label="Tag",
                created=created_tag_ids,
            )
            for tag_id in sorted(current ^ target):
                await toggle_card_tag(self.session, card.id, tag_id, actor=ActorType.AI)

    async def _apply_card_links(
        self,
        card: Card,
        values: dict[str, Any],
        *,
        linked: bool,
        created_tag_ids: dict[str, int],
        created_value_ids: dict[str, int],
    ) -> None:
        if {"value_id", "value_ids", "value_query"} & values.keys():
            target_ids = await self._named_ids(
                values,
                singular_key="value_id",
                plural_key="value_ids",
                query_key="value_query",
                model=Value,
                label="Value",
                created=created_value_ids,
            )
            for value_id in sorted(target_ids):
                exists = await self.session.get(
                    CardValue, {"card_id": card.id, "value_id": value_id}
                )
                if linked != (exists is not None):
                    await toggle_card_value(self.session, card.id, value_id, actor=ActorType.AI)
            return
        if {"tag_id", "tag_ids", "tag_query"} & values.keys():
            target_ids = await self._named_ids(
                values,
                singular_key="tag_id",
                plural_key="tag_ids",
                query_key="tag_query",
                model=Tag,
                label="Tag",
                created=created_tag_ids,
            )
            for tag_id in sorted(target_ids):
                exists = await self.session.get(CardTag, {"card_id": card.id, "tag_id": tag_id})
                if linked != (exists is not None):
                    await toggle_card_tag(self.session, card.id, tag_id, actor=ActorType.AI)
            return
        raise DomainError("A Card link proposal needs one relationship type")

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
        created_tag_ids: dict[str, int] = {}
        created_value_ids: dict[str, int] = {}
        for change in changes:
            if change.entity == "card":
                if change.action == "create":
                    values = dict(change.values)
                    value_ids = await self._named_ids(
                        values,
                        singular_key="value_id",
                        plural_key="value_ids",
                        query_key="value_query",
                        model=Value,
                        label="Value",
                        created=created_value_ids,
                    )
                    tag_ids = await self._named_ids(
                        values,
                        singular_key="tag_id",
                        plural_key="tag_ids",
                        query_key="tag_query",
                        model=Tag,
                        label="Tag",
                        created=created_tag_ids,
                    )
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
                        actor=ActorType.AI,
                    )
                    affected.append(card.id)
                    continue
                card = await self.session.get(Card, change.entity_id) if change.entity_id else None
                if card is None or card.version != change.expected_version:
                    proposal.status = ProposalStatus.STALE.value
                    raise StaleStateError("A Card changed; refresh this proposal")
                if change.action == "move":
                    await move_card(
                        self.session, card.id, CardStage(change.values["stage"]), actor=ActorType.AI
                    )
                elif change.action == "complete":
                    await finish_action(self.session, card.id, CardStage.DONE, actor=ActorType.AI)
                elif change.action == "cancel":
                    await finish_action(
                        self.session, card.id, CardStage.CANCELLED, actor=ActorType.AI
                    )
                elif change.action == "reopen":
                    await move_card(
                        self.session,
                        card.id,
                        CardStage(change.values.get("stage", CardStage.BACKLOG.value)),
                        actor=ActorType.AI,
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
                        target_stage = CardStage(change.values["stage"])
                        if target_stage in {CardStage.DONE, CardStage.CANCELLED}:
                            await finish_action(
                                self.session, card.id, target_stage, actor=ActorType.AI
                            )
                        else:
                            await move_card(self.session, card.id, target_stage, actor=ActorType.AI)
                    await self._replace_card_sets(
                        card,
                        change.values,
                        created_tag_ids=created_tag_ids,
                        created_value_ids=created_value_ids,
                    )
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
                        created_tag_ids=created_tag_ids,
                        created_value_ids=created_value_ids,
                    )
                else:
                    raise DomainError(f"Unsupported approved Card action: {change.action}")
                affected.append(card.id)
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
                    created_tag_ids[name.casefold()] = tag.id
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
                    created_value_ids[name.casefold()] = value.id
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
            elif change.entity == "sprint":
                if change.action == "start":
                    sprint = await start_sprint(self.session)
                elif change.action == "finish":
                    sprint = await finish_sprint(self.session, reason="ai_approved")
                else:
                    raise DomainError(f"Unsupported Sprint action: {change.action}")
                affected.append(sprint.id)
            elif change.entity == "settings" and change.action == "update":
                profile = await self.session.get(UserProfile, 1)
                for name in {
                    "about_me",
                    "advisor_instructions",
                    "capacity_effort_points",
                    "reminders_enabled",
                    "weekend_enabled",
                    "proactive_limit",
                    "reminder_cooldown_minutes",
                }:
                    if name in change.values:
                        setattr(profile, name, change.values[name])
                workspace.revision += 1
                affected.append("settings")
            else:
                raise DomainError(f"Unsupported approved change: {change.entity}.{change.action}")
        proposal.status = ProposalStatus.APPROVED.value
        return affected

    async def reject(self, proposal_id: int) -> None:
        proposal = await self.session.get(ChangeProposal, proposal_id)
        if proposal and proposal.status == ProposalStatus.PENDING.value:
            proposal.status = ProposalStatus.REJECTED.value
