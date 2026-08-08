from __future__ import annotations

import json
import logging
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
from ..saved_requests import RequestFilterError, normalize_filter_spec
from .context import SYSTEM_PROMPT, DialogueMessage, planning_context
from .contracts import AGENT_RESPONSE_SCHEMA, AgentChange, AgentResponse
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
MAX_READ_TOOL_CALLS = 8


@dataclass
class AIOutcome:
    kind: str
    message: str
    draft_bundle_ids: list[int] = field(default_factory=list)
    proposal_id: int | None = None


def parse_response(raw: str) -> AgentResponse:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1])
    return AgentResponse.model_validate_json(text)


def _log_preview(content: str, limit: int = 500) -> str:
    compact = " ".join(content.split())
    return compact if len(compact) <= limit else compact[: limit - 3] + "..."


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
            memory = await self.memory.sync()
            async with self.sessions() as session:
                state = await planning_context(session)
                from ..models import CardDraft

                pending = (
                    await session.get(CardDraft, pending_draft_id) if pending_draft_id else None
                )
            system_sections = [
                SYSTEM_PROMPT,
                "Response JSON Schema:\n" + json.dumps(AGENT_RESPONSE_SCHEMA, ensure_ascii=False),
                f"Current planning state:\n{state}\n\nPersistent memory:\n{memory.text}",
            ]
            if pending:
                system_sections.append(f"Pending draft reference: {pending.id}")
            messages: list[dict[str, Any]] = [
                {"role": "system", "content": "\n\n".join(system_sections)}
            ]
            # The history source has already applied the real Telegram session or
            # Summary boundary and the 20-message summary context policy.
            for item in dialogue or []:
                messages.append({"role": item.role, "content": item.content})
            if not dialogue:
                messages.append({"role": "user", "content": text})
            response = await self._run_agent_loop(messages, run.id)
            outcome = await self._materialize(response)
            await self._finish_run(run.id, "completed", started)
            return outcome
        except Exception as error:
            logger.exception("AI advisor run failed")
            await self._finish_run(run.id, "failed", started, type(error).__name__)
            raise

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
            raw = await self.provider.complete(messages, json_schema=AGENT_RESPONSE_SCHEMA)
            turn = ProviderTurn(content=raw)
        else:
            turn = await complete_turn(
                messages,
                tools=[QUERY_SAFWA_TOOL],
                json_schema=AGENT_RESPONSE_SCHEMA,
            )
        _log_provider_response(turn)
        return turn

    async def _run_agent_loop(self, messages: list[dict[str, Any]], run_id: int) -> AgentResponse:
        tool_count = 0
        repair_count = 0
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
                for call in turn.tool_calls:
                    tool_count += 1
                    if tool_count > MAX_READ_TOOL_CALLS:
                        raise DomainError("The advisor exceeded the read-tool call limit")
                    result = await self._execute_query_tool(call, run_id, tool_count)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.id,
                            "name": call.name,
                            "content": json.dumps(result, ensure_ascii=False, default=str),
                        }
                    )
                continue

            try:
                return parse_response(turn.content)
            except (ValidationError, json.JSONDecodeError, ValueError) as error:
                if repair_count >= 1:
                    raise
                repair_count += 1
                messages.extend(
                    [
                        {"role": "assistant", "content": turn.content},
                        {
                            "role": "user",
                            "content": f"[Invalid response]: {error}. Return corrected JSON only.",
                        },
                    ]
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
                        "sql": sql,
                        "row_count": len(rows),
                        "columns": list(rows[0]) if rows else [],
                    },
                )
            )
            await session.commit()
        return rows

    async def _materialize(self, response: AgentResponse) -> AIOutcome:
        if response.kind in {"answer", "clarification"}:
            return AIOutcome(response.kind, response.message)
        creates = [
            change
            for change in response.changes
            if change.entity == "card" and change.action == "create"
        ]
        other = [change for change in response.changes if change not in creates]
        draft_ids: list[int] = []
        proposal_id: int | None = None
        async with self.sessions() as session:
            if creates:
                payloads = [await self._resolve_card_draft(session, change) for change in creates]
                bundle = await DraftService(session).create_bundle("ai", payloads)
                draft_ids.append(bundle.id)
            if other:
                workspace = await session.get(Workspace, 1)
                if workspace is None:
                    raise DomainError("Workspace is missing")
                proposal = ChangeProposal(
                    message=response.message,
                    workspace_revision=workspace.revision,
                    expires_at=utcnow() + timedelta(hours=24),
                )
                session.add(proposal)
                await session.flush()
                for index, change in enumerate(other):
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
                    if change.entity == "request" and (
                        "filter" in values or "filter_spec" in values
                    ):
                        try:
                            values["filter_spec"] = normalize_filter_spec(
                                values.pop("filter", values.get("filter_spec"))
                            )
                        except RequestFilterError as error:
                            raise DomainError(f"Invalid Request filter: {error}") from error
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
                proposal_id = proposal.id
            await session.commit()
        return AIOutcome("proposal", response.message, draft_ids, proposal_id)

    async def _resolve_card_draft(
        self, session: AsyncSession, change: AgentChange
    ) -> dict[str, Any]:
        values = dict(change.values)
        provenance = values.setdefault("field_provenance", {})
        unresolved: list[str] = provenance.setdefault("unresolved", [])
        parent: Card | None = None
        if values.get("parent_id"):
            parent = await session.get(Card, values["parent_id"])
        elif values.get("parent_query"):
            matches = list(
                await session.scalars(
                    select(Card).where(
                        Card.title.collate("NOCASE") == str(values["parent_query"]),
                        Card.archived_at.is_(None),
                    )
                )
            )
            parent = matches[0] if len(matches) == 1 else None
            if len(matches) != 1:
                provenance["parent_query"] = values["parent_query"]
        if parent:
            values["parent_id"] = parent.id
            values["expected_parent_version"] = parent.version
            values["root_confirmed"] = False
        parent_was_requested = bool(values.get("parent_query") or values.get("parent_id"))
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
                    existing = await self.session.scalar(
                        select(Tag).where(
                            Tag.name.collate("NOCASE") == name,
                            Tag.archived_at.is_(None),
                        )
                    )
                    if existing is not None:
                        raise DomainError(f"Tag '{name}' already exists; refresh the proposal")
                    tag = Tag(
                        name=name,
                        description=str(change.values.get("description", "")).strip(),
                    )
                    self.session.add(tag)
                    await self.session.flush()
                    created_tag_ids[name.casefold()] = tag.id
                elif tag is None or tag.version != change.expected_version:
                    raise StaleStateError("A Tag changed; refresh this proposal")
                elif change.action == "update":
                    if "name" in change.values:
                        tag.name = str(change.values["name"]).strip()
                    if "description" in change.values:
                        tag.description = str(change.values["description"]).strip()
                    tag.version += 1
                elif change.action == "archive":
                    tag.archived_at = utcnow()
                    tag.version += 1
                else:
                    raise DomainError(f"Unsupported Tag action: {change.action}")
                workspace.revision += 1
                affected.append(tag.id)
            elif change.entity == "value":
                value = (
                    await self.session.get(Value, change.entity_id) if change.entity_id else None
                )
                if change.action == "create":
                    name = str(change.values["name"]).strip()
                    if not name:
                        raise DomainError("A new Value needs a name")
                    value = Value(
                        name=name,
                        description=str(change.values.get("description", "")).strip(),
                        active=bool(change.values.get("active", False)),
                    )
                    self.session.add(value)
                    await self.session.flush()
                    created_value_ids[name.casefold()] = value.id
                elif value is None or value.version != change.expected_version:
                    raise StaleStateError("A Value changed; refresh this proposal")
                elif change.action == "update":
                    if "name" in change.values:
                        value.name = str(change.values["name"]).strip()
                    if "description" in change.values:
                        value.description = str(change.values["description"]).strip()
                    if "active" in change.values:
                        value.active = bool(change.values["active"])
                    value.version += 1
                elif change.action == "archive":
                    value.archived_at = utcnow()
                    value.version += 1
                else:
                    raise DomainError(f"Unsupported Value action: {change.action}")
                workspace.revision += 1
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
                        change.values["filter_spec"],
                        str(change.values.get("description", "")),
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
                        filter_spec=change.values.get("filter_spec"),
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
