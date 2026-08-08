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
    archive_subtree,
    delete_subtree,
    finish_action,
    finish_sprint,
    move_card,
    start_sprint,
    toggle_card_dependency,
    toggle_card_tag,
    toggle_card_value,
    update_card_fields,
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
    Tag,
    UserProfile,
    Value,
    Workspace,
)
from .context import SYSTEM_PROMPT, DialogueMessage, lexical_candidates, planning_context
from .contracts import AGENT_RESPONSE_SCHEMA, AgentChange, AgentResponse
from .provider import OpenAICompatibleProvider
from .sql import ReadOnlyQueryRunner, UnsafeQueryError

logger = logging.getLogger(__name__)

SUBSESSION_RESULT_PROMPT = """Compress this isolated Safwa planning/advisory branch into one concise
context message for the parent conversation. Preserve concrete outcomes, decisions, personal insights,
unresolved issues, and any planning implications. Write in the conversation's language. Do not mention
summaries, sessions, prompts, tools, SQL, or AI. Do not claim unapproved changes happened."""


@dataclass
class AIOutcome:
    kind: str
    message: str
    draft_bundle_ids: list[str] = field(default_factory=list)
    proposal_id: str | None = None


def parse_response(raw: str) -> AgentResponse:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1])
    return AgentResponse.model_validate_json(text)


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
        pending_draft_id: str | None = None,
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
                candidates = await lexical_candidates(session, text)
                from ..models import CardDraft

                pending = (
                    await session.get(CardDraft, pending_draft_id) if pending_draft_id else None
                )
            messages: list[dict[str, str]] = [
                {
                    "role": "system",
                    "content": SYSTEM_PROMPT
                    + "\n\nResponse JSON Schema:\n"
                    + json.dumps(AGENT_RESPONSE_SCHEMA, ensure_ascii=False),
                },
                {
                    "role": "system",
                    "content": f"Current planning state:\n{state}\n\nPersistent memory:\n{memory.text}",
                },
            ]
            if candidates:
                messages.append(
                    {"role": "system", "content": "Lexical card candidates:\n" + candidates}
                )
            # The history source has already applied the real Telegram session or
            # Summary boundary and the 20-message summary context policy.
            for item in dialogue or []:
                messages.append({"role": item.role, "content": item.content})
            if pending:
                messages.append(
                    {"role": "system", "content": f"Pending draft reference: {pending.id}"}
                )
            messages.append({"role": "user", "content": text})
            response = await self._complete_validated(messages)
            for query_index in range(2):
                if response.kind != "query":
                    break
                try:
                    rows = await self.query_runner.run(response.sql or "")
                except (UnsafeQueryError, TimeoutError, OSError) as error:
                    rows = [{"error": str(error)}]
                async with self.sessions() as session:
                    session.add(
                        AgentStep(
                            run_id=run.id,
                            position=query_index + 1,
                            kind="read_query",
                            metadata_json={
                                "sql": response.sql,
                                "row_count": len(rows),
                                "columns": list(rows[0]) if rows else [],
                            },
                        )
                    )
                    await session.commit()
                messages.extend(
                    [
                        {"role": "assistant", "content": response.model_dump_json()},
                        {
                            "role": "system",
                            "content": "Read-query result:\n"
                            + json.dumps(rows, ensure_ascii=False, default=str),
                        },
                    ]
                )
                response = await self._complete_validated(messages)
            if response.kind == "query":
                raise DomainError("The advisor exceeded the read-query limit")
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

    async def _complete_validated(self, messages: list[dict[str, str]]) -> AgentResponse:
        raw = await self.provider.complete(messages, json_schema=AGENT_RESPONSE_SCHEMA)
        try:
            return parse_response(raw)
        except (ValidationError, json.JSONDecodeError, ValueError) as first_error:
            repaired = await self.provider.complete(
                [
                    *messages,
                    {"role": "assistant", "content": raw},
                    {
                        "role": "system",
                        "content": f"The response was invalid ({first_error}). Return corrected JSON only.",
                    },
                ],
                json_schema=AGENT_RESPONSE_SCHEMA,
                temperature=0,
            )
            return parse_response(repaired)

    async def _materialize(self, response: AgentResponse) -> AIOutcome:
        if response.kind in {"answer", "clarification"}:
            return AIOutcome(response.kind, response.message)
        creates = [
            change
            for change in response.changes
            if change.entity == "card" and change.action == "create"
        ]
        other = [change for change in response.changes if change not in creates]
        draft_ids: list[str] = []
        proposal_id: str | None = None
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
                    elif change.id and change.entity == "value":
                        entity = await session.get(Value, change.id)
                        expected_version = entity.version if entity else None
                    session.add(
                        ProposalChange(
                            proposal_id=proposal.id,
                            position=index,
                            entity=change.entity,
                            action=change.action,
                            entity_id=change.id,
                            expected_version=expected_version,
                            values=change.values,
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
        self, run_id: str, status: str, started: float, error_code: str | None = None
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

    async def apply(self, proposal_id: str, *, allow_destructive: bool = False) -> list[str]:
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
        affected: list[str] = []
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
                elif change.action in {"link", "unlink"} and change.values.get("value_id"):
                    value_id = change.values["value_id"]
                    currently_linked = await self.session.get(
                        CardValue, {"card_id": card.id, "value_id": value_id}
                    )
                    if (change.action == "link") != (currently_linked is not None):
                        await toggle_card_value(self.session, card.id, value_id, actor=ActorType.AI)
                elif change.action in {"link", "unlink"} and change.values.get("tag_id"):
                    tag_id = change.values["tag_id"]
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
                tag = (
                    await self.session.get(Tag, change.entity_id) if change.entity_id else None
                )
                if change.action == "create":
                    tag = Tag(
                        name=str(change.values["name"]).strip(),
                        description=str(change.values.get("description", "")).strip(),
                    )
                    self.session.add(tag)
                    await self.session.flush()
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
                    value = Value(
                        name=str(change.values["name"]).strip(),
                        description=str(change.values.get("description", "")).strip(),
                        active=bool(change.values.get("active", False)),
                    )
                    self.session.add(value)
                    await self.session.flush()
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

    async def reject(self, proposal_id: str) -> None:
        proposal = await self.session.get(ChangeProposal, proposal_id)
        if proposal and proposal.status == ProposalStatus.PENDING.value:
            proposal.status = ProposalStatus.REJECTED.value
