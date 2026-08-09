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
    create_saved_request,
    create_tag,
    create_value,
    delete_subtree,
    finish_action,
    finish_sprint,
    move_card,
    start_sprint,
    toggle_card_dependency,
    toggle_card_tag,
    toggle_card_value,
    update_card_fields,
    update_saved_request,
    update_tag_fields,
    update_value_fields,
    utcnow,
)
from ..drafts import DraftService
from ..enums import ActorType, CardStage, ProposalStatus
from ..memory import MemoryFileStore
from ..models import (
    AgentRun,
    AgentStep,
    Card,
    CardDependency,
    CardDraftBundle,
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
    "card": "Prepare a Card draft or a proposed Card change.",
    "value": "Prepare a Value creation or edit proposal.",
    "tag": "Prepare a Tag creation or edit proposal.",
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
MAX_TOOL_CALLS = 16


@dataclass
class AIOutcome:
    kind: str
    message: str
    draft_bundle_ids: list[int] = field(default_factory=list)
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


def _log_preview(content: str, limit: int = 500) -> str:
    compact = " ".join(content.split())
    return compact if len(compact) <= limit else compact[: limit - 3] + "..."


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _result_value(value: Any) -> str:
    return " ".join(str(value).split())[:100]


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


def _approval_results_summary(tools: list[dict[str, Any]]) -> str:
    lines = ["Proposal results:"]
    for tool in tools:
        if not tool.get("target"):
            continue
        result = dict(tool.get("result") or {})
        status = str(result.get("status", "failed"))
        prefix = {
            "approved": "✅ Saved",
            "discarded": "🗑 Discarded",
            "failed": "⚠️ Failed",
        }.get(status, f"⚠️ {status.title()}")
        line = f"{prefix} — {_approval_change_label(tool)}"
        error = result.get("error")
        if error:
            line += f": {_result_value(error)}"
        lines.append(line)
    return "\n".join(lines) if len(lines) > 1 else ""


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
        pending_draft_id: int | None = None,
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
            messages = await self._context_messages(dialogue or [], pending_draft_id)
            if not dialogue:
                messages.append({"role": "user", "content": text})
            result = await self._run_agent_loop(messages, run.id)
            outcome = await self._materialize(result, run.id)
            status = "awaiting_approval" if result.pending_tools else "completed"
            await self._finish_run(run.id, status, started)
            return outcome
        except Exception as error:
            logger.exception("AI advisor run failed")
            await self._finish_run(run.id, "failed", started, type(error).__name__)
            raise

    async def _context_messages(
        self,
        dialogue: list[DialogueMessage],
        pending_draft_id: int | None = None,
    ) -> list[dict[str, Any]]:
        memory = await self.memory.sync()
        async with self.sessions() as session:
            state = await planning_context(session)
            from ..models import CardDraft

            pending = await session.get(CardDraft, pending_draft_id) if pending_draft_id else None
        system_sections = [
            SYSTEM_PROMPT,
            f"Current planning state:\n{state}\n\nPersistent memory:\n{memory.text}",
        ]
        if pending:
            system_sections.append(f"Pending draft reference: {pending.id}")
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
                        message = "I prepared the card draft for your review."
                    elif card_creates:
                        message = "I prepared card drafts and proposed changes for your review."
                    else:
                        message = "I prepared the proposed changes for your approval."
                    return AgentLoopResult(
                        message,
                        pending_tools,
                        turn.content or None,
                        tool_count,
                    )
                continue

            if not turn.content:
                raise DomainError("The advisor finished without a response")
            return AgentLoopResult(turn.content, tool_count=tool_count)

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
            return None, {"status": "rejected", "error": str(error)}
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

    async def _create_proposal(
        self,
        session: AsyncSession,
        message: str,
        tools: list[PendingTool],
    ) -> ChangeProposal:
        workspace = await session.get(Workspace, 1)
        if workspace is None:
            raise DomainError("Workspace is missing")
        proposal = ChangeProposal(
            message=message,
            workspace_revision=workspace.revision,
            expires_at=utcnow() + timedelta(hours=24),
        )
        session.add(proposal)
        await session.flush()
        for index, tool in enumerate(tools):
            change = tool.change
            if change is None:
                continue
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
            values = dict(change.values)
            if change.entity == "request" and "sql" in values:
                try:
                    values["query_sql"] = normalize_request_sql(values.pop("sql"))
                except RequestQueryError as error:
                    raise DomainError(f"Invalid Request SQL: {error}") from error
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

    @staticmethod
    def _target_outcome(message: str, target: dict[str, Any]) -> AIOutcome:
        if target["type"] == "draft_bundle":
            return AIOutcome("proposal", message, [int(target["id"])])
        return AIOutcome("proposal", message, proposal_id=int(target["id"]))

    async def _materialize(self, result: AgentLoopResult, run_id: int) -> AIOutcome:
        if not result.pending_tools:
            return AIOutcome("answer", result.message)
        mutation_tools = [tool for tool in result.pending_tools if tool.change is not None]
        creates = [
            tool
            for tool in mutation_tools
            if tool.change is not None
            and tool.change.entity == "card"
            and tool.change.action == "create"
        ]
        other = [tool for tool in mutation_tools if tool not in creates]
        targets: list[tuple[int, dict[str, Any], list[PendingTool]]] = []
        async with self.sessions() as session:
            if creates:
                payloads = [
                    await self._resolve_card_draft(session, tool.change)
                    for tool in creates
                    if tool.change is not None
                ]
                bundle = await DraftService(session).create_bundle("ai", payloads)
                first_index = min(result.pending_tools.index(tool) for tool in creates)
                targets.append((first_index, {"type": "draft_bundle", "id": bundle.id}, creates))
            for tool in other:
                proposal = await self._create_proposal(session, result.message, [tool])
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
                        "result": None if target else _json_safe(tool.result),
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
                        "tool_calls": tool_results,
                        "queue": queue,
                    },
                )
            )
            await session.commit()
        if not targets:
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
            await session.commit()

        try:
            messages = await self._context_messages(dialogue)
            messages.append(
                {
                    "role": "assistant",
                    "content": metadata.get("assistant_content"),
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
            )
            result_summary = _approval_results_summary(tools)
            if result_summary:
                loop_result.message = f"{result_summary}\n\n{loop_result.message}".strip()
            outcome = await self._materialize(loop_result, run_id)
            async with self.sessions() as session:
                stored_batch = await session.get(AgentStep, batch.id)
                if stored_batch is not None:
                    final_metadata = dict(stored_batch.metadata_json or {})
                    final_metadata["status"] = "completed"
                    stored_batch.metadata_json = final_metadata
                    await session.commit()
            run_status = "awaiting_approval" if loop_result.pending_tools else "completed"
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
                elif item_type == "draft_bundle":
                    bundle = await session.get(CardDraftBundle, item_id)
                    if bundle is not None and bundle.status not in {"committed", "discarded"}:
                        await DraftService(session).discard_bundle(item_id)
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

    async def _resolve_card_draft(
        self, session: AsyncSession, change: AgentChange
    ) -> dict[str, Any]:
        values = dict(change.values)
        provenance = values.setdefault("field_provenance", {})
        unresolved: list[str] = provenance.setdefault("unresolved", [])
        parent: Card | None = None
        parent_query = values.pop("parent_query", None)
        parent_was_requested = bool(parent_query or values.get("parent_id"))
        if values.get("parent_id"):
            parent = await session.get(Card, values["parent_id"])
            if parent is None:
                unresolved.append(f"Parent Card #{values['parent_id']} does not exist")
        elif parent_query:
            query_text = str(parent_query).strip()
            is_sql = query_text.casefold().startswith(("select", "with"))
            if is_sql:
                query_error_recorded = False
                try:
                    statement = normalize_request_sql(query_text)
                    rows = await self.query_runner.run(statement)
                except (RequestQueryError, UnsafeQueryError, sqlite3.Error, TimeoutError) as error:
                    logger.warning("Parent query was rejected: %s", error)
                    rows = []
                    unresolved.append("Parent query was invalid or unsafe")
                    query_error_recorded = True
                if len(rows) == 1 and set(rows[0]) == {"id"}:
                    parent_id = rows[0]["id"]
                    parent = (
                        await session.get(Card, parent_id) if isinstance(parent_id, int) else None
                    )
                    if parent is None:
                        unresolved.append("Parent query did not return an existing Card id")
                elif not query_error_recorded:
                    unresolved.append(
                        "Parent query returned no Cards"
                        if not rows
                        else "Parent query must return exactly one Card id"
                    )
            else:
                matches = list(
                    await session.scalars(
                        select(Card).where(
                            Card.title.collate("NOCASE") == query_text,
                            Card.archived_at.is_(None),
                        )
                    )
                )
                parent = matches[0] if len(matches) == 1 else None
                if len(matches) != 1:
                    unresolved.append(
                        f"Parent '{query_text}' was not found"
                        if not matches
                        else f"Parent '{query_text}' matched multiple Cards"
                    )
            if parent is None:
                provenance["parent_query"] = query_text
        if parent:
            values["parent_id"] = parent.id
            values["expected_parent_version"] = parent.version
            values["root_confirmed"] = False
        if not parent_was_requested:
            values["root_confirmed"] = True
        requested_values = values.pop("value_query", None)
        if isinstance(requested_values, str):
            requested_values = [requested_values]
        value_ids = list(values.get("value_ids", []))
        for query in requested_values or []:
            matches = list(
                await session.scalars(
                    select(Value).where(
                        Value.name.collate("NOCASE") == str(query),
                        Value.archived_at.is_(None),
                    )
                )
            )
            if len(matches) == 1:
                value_ids.append(matches[0].id)
            else:
                unresolved.append(f"Value '{query}'")
        values["value_ids"] = list(dict.fromkeys(value_ids))
        requested_tags = values.pop("tag_query", None)
        if isinstance(requested_tags, str):
            requested_tags = [requested_tags]
        tag_ids = list(values.get("tag_ids", []))
        for query in requested_tags or []:
            matches = list(
                await session.scalars(
                    select(Tag).where(
                        Tag.name.collate("NOCASE") == str(query),
                        Tag.archived_at.is_(None),
                    )
                )
            )
            if len(matches) == 1:
                tag_ids.append(matches[0].id)
            else:
                unresolved.append(f"Tag '{query}'")
        values["tag_ids"] = list(dict.fromkeys(tag_ids))
        provenance["origin"] = "ai"
        return values

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
                    await update_card_fields(
                        self.session,
                        card.id,
                        {
                            name: value
                            for name, value in change.values.items()
                            if name
                            in {
                                "title",
                                "note",
                                "priority",
                                "hard_time",
                                "effort_points",
                                "repeatable",
                            }
                        },
                        actor=ActorType.AI,
                    )
                elif change.action == "archive":
                    await archive_subtree(self.session, card.id)
                elif change.action == "delete":
                    if not allow_destructive:
                        raise DomainError("Permanent deletion needs a second confirmation")
                    await delete_subtree(self.session, card.id)
                elif change.action in {"link", "unlink"} and (
                    change.values.get("value_id") or change.values.get("value_query")
                ):
                    value_id = change.values.get("value_id")
                    value_query = str(change.values.get("value_query", "")).strip()
                    if not value_id and value_query:
                        value_id = created_value_ids.get(value_query.casefold())
                    if not value_id and value_query:
                        matching_values = list(
                            await self.session.scalars(
                                select(Value).where(
                                    Value.name.collate("NOCASE") == value_query,
                                    Value.archived_at.is_(None),
                                )
                            )
                        )
                        if len(matching_values) == 1:
                            value_id = matching_values[0].id
                    if not value_id:
                        raise DomainError(
                            f"Value '{value_query}' is not available for this approved link"
                        )
                    currently_linked = await self.session.get(
                        CardValue, {"card_id": card.id, "value_id": value_id}
                    )
                    if (change.action == "link") != (currently_linked is not None):
                        await toggle_card_value(self.session, card.id, value_id, actor=ActorType.AI)
                elif change.action in {"link", "unlink"} and (
                    change.values.get("tag_id") or change.values.get("tag_query")
                ):
                    tag_id = change.values.get("tag_id")
                    tag_query = str(change.values.get("tag_query", "")).strip()
                    if not tag_id and tag_query:
                        tag_id = created_tag_ids.get(tag_query.casefold())
                    if not tag_id and tag_query:
                        matching_tags = list(
                            await self.session.scalars(
                                select(Tag).where(
                                    Tag.name.collate("NOCASE") == tag_query,
                                    Tag.archived_at.is_(None),
                                )
                            )
                        )
                        if len(matching_tags) == 1:
                            tag_id = matching_tags[0].id
                    if not tag_id:
                        raise DomainError(
                            f"Tag '{tag_query}' is not available for this approved link"
                        )
                    currently_linked = await self.session.get(
                        CardTag, {"card_id": card.id, "tag_id": tag_id}
                    )
                    if (change.action == "link") != (currently_linked is not None):
                        await toggle_card_tag(self.session, card.id, tag_id, actor=ActorType.AI)
                elif change.action in {"link", "unlink"} and change.values.get("blocker_id"):
                    blocker_id = change.values["blocker_id"]
                    link = await self.session.get(
                        CardDependency,
                        {"blocked_card_id": card.id, "blocker_card_id": blocker_id},
                    )
                    if (change.action == "link") != (link is not None):
                        await toggle_card_dependency(
                            self.session,
                            card.id,
                            blocker_id,
                            copy_to_repeat=bool(change.values.get("copy_to_repeat", False)),
                            actor=ActorType.AI,
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
                        tag.archived_at = utcnow()
                        tag.version += 1
                        workspace.revision += 1
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
                        value.archived_at = utcnow()
                        value.version += 1
                        workspace.revision += 1
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
