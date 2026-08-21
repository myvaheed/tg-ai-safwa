"""Turn one validated mutation call into the exact values a proposal stores.

Preparation checks a change against live data — the parent, named references, Pending
Checks, reminder timing, Request SQL — and normalises what survives.  It runs for
whoever authored the change and writes nothing, so a failure is one model-visible
retryable tool error rather than the end of a turn.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date as calendar_date
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from llm_gateway import LlmProvider

from ..domain import (
    CARD_REFERENCE_SPECS,
    DomainError,
    ReferenceSpec,
    diary_entry_for,
    is_closed_repeat,
    live_repeat_instance_id,
    pending_checks,
    resolve_references,
    utcnow,
)
from ..enums import CardKind, CardStage
from ..models import (
    Card,
    Check,
    DiaryEntry,
    Reminder,
    SavedRequest,
    Tag,
    Value,
    Workspace,
)
from ..reminders import ScheduleError, describe, schedule_payload
from ..saved_requests import RequestQueryError, normalize_request_sql
from .contracts import AgentChange
from .reminder_sessions import resolve_schedule
from .sql import ReadOnlyQueryRunner, UnsafeQueryError

ENTITY_MODELS: dict[str, Any] = {
    "card": Card,
    "check": Check,
    "tag": Tag,
    "value": Value,
    "request": SavedRequest,
    "reminder": Reminder,
    "diary": DiaryEntry,
}


async def _live_instance_hint(session: AsyncSession, entity: Card | Check) -> str:
    live_id = await live_repeat_instance_id(session, entity)
    if live_id is None:
        return "The series has ended. Tell the owner instead of proposing again."
    return f"Retry this call with #{live_id}, the open one in its series."


def allows_parent(child_kind: str | None, parent_kind: str | None) -> bool:
    if child_kind == CardKind.IDEA.value:
        return parent_kind == CardKind.GOAL.value
    if child_kind == CardKind.ACTION.value:
        return parent_kind in {CardKind.GOAL.value, CardKind.IDEA.value}
    return False


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


@dataclass(frozen=True)
class PreparedChange:
    """What a proposal row needs: the resolved values and the version they assume."""

    values: dict[str, Any]
    expected_version: int | None


class ChangePreparer:
    def __init__(
        self,
        provider: LlmProvider,
        query_runner: ReadOnlyQueryRunner,
    ) -> None:
        self.provider = provider
        self.query_runner = query_runner

    async def prepare(self, session: AsyncSession, change: AgentChange) -> PreparedChange:
        workspace = await session.get(Workspace, 1)
        if workspace is None:
            raise DomainError("Workspace is missing")
        if change.entity == "diary":
            await self._resolve_diary_change(session, change)
        entity: Card | Check | DiaryEntry | Reminder | Tag | Value | SavedRequest | None = None
        expected_version = None
        if change.id and change.entity in ENTITY_MODELS:
            entity = await session.get(ENTITY_MODELS[change.entity], change.id)
            expected_version = entity.version if entity else None
        if change.id is not None and (
            entity is None or getattr(entity, "archived_at", None) is not None
        ):
            raise ToolPreparationError(
                "target_not_found",
                f"{change.entity.title()} #{change.id} does not exist or is archived.",
                "Find the current numeric ID with query_safwa and retry. If nothing matches, say so "
                "instead of proposing again.",
            )
        if isinstance(entity, Card | Check) and is_closed_repeat(entity):
            raise ToolPreparationError(
                "closed_repeat",
                f"{change.entity.title()} #{change.id} is a closed repeat and cannot be changed.",
                await _live_instance_hint(session, entity),
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
            values = await self._prepare_reminder_values(workspace, values)
        if change.entity == "request" and "sql" in values:
            try:
                values["query_sql"] = normalize_request_sql(values.pop("sql"))
            except RequestQueryError as error:
                raise ToolPreparationError(
                    "unsafe_query",
                    f"Invalid Request SQL: {error}",
                    "Use one read-only SELECT over ai_cards that returns an id column.",
                ) from error
        return PreparedChange(values=values, expected_version=expected_version)

    async def _validate_named_references(
        self,
        session: AsyncSession,
        values: dict[str, Any],
        spec: ReferenceSpec,
    ) -> None:
        """Reject a relationship the owner could not act on, with a retryable hint."""
        reference_hint = (
            "Find the item with query_safwa and retry this call with its numeric ID. If you "
            "proposed it earlier in this same turn, wait for that result and use the ID it returns."
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
        if spec.model is not Check:
            return
        for check_id in sorted(resolved.ids):
            check = await session.get(Check, check_id)
            if check is not None and is_closed_repeat(check):
                raise ToolPreparationError(
                    "closed_repeat",
                    f"Check #{check_id} is a closed repeat and cannot be linked.",
                    await _live_instance_hint(session, check),
                )

    async def _resolve_parent_reference(
        self,
        session: AsyncSession,
        values: dict[str, Any],
        child_kind: str | None,
    ) -> None:
        reference_hint = (
            "Find the parent with query_safwa and retry with its numeric parent_id, or drop the "
            "parent. If you proposed it earlier in this same turn, wait for that result first."
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
                        "Fix the SELECT, or give parent_id or an exact Card title instead.",
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
        if not allows_parent(child_kind, parent.kind):
            raise ToolPreparationError(
                "invalid_parent_kind",
                f"A {child_kind or 'Card'} cannot have a {parent.kind} parent.",
                "Choose a parent this Card may hang under, or drop the parent and leave it root-level.",
            )

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

    async def _resolve_diary_change(
        self, session: AsyncSession, change: AgentChange
    ) -> None:
        """Point the change at the day it settles, and say whether that day exists yet."""
        try:
            entry_date = calendar_date.fromisoformat(str(change.values.get("date") or ""))
        except ValueError as error:
            raise ToolPreparationError(
                "invalid_arguments",
                "date must be a calendar date written as YYYY-MM-DD.",
                "Retry the diary call with the day you meant, written as YYYY-MM-DD.",
            ) from error
        saved = await diary_entry_for(session, entry_date)
        if change.action == "delete":
            if saved is None:
                raise ToolPreparationError(
                    "target_not_found",
                    f"There is no Diary entry for {entry_date.isoformat()}.",
                    "Tell the user that day has nothing written. Propose nothing.",
                )
            change.id = saved.id
            change.values = {"entry_date": entry_date.isoformat()}
            return
        change.action = "update" if saved is not None else "create"
        change.id = saved.id if saved is not None else None
        values = dict(change.values)
        change.values = {
            "entry_date": entry_date.isoformat(),
            "body": str(values.get("pov") or ""),
            "feeling_score": values.get("feeling_score"),
            "ai_comment": str(values.get("ai_comment") or ""),
        }

    async def _prepare_reminder_values(
        self, workspace: Workspace, values: dict[str, Any]
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
                "Ask the user this exact question, then call reminder again with their "
                "answer in when. Never invent a time.",
            ) from error
        prepared["schedule"] = schedule_payload(schedule)
        prepared["schedule_text"] = describe(schedule, tz=tz, now=now)
        return prepared
