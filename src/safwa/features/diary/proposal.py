"""How a proposed Diary day is checked and then written.

A Diary change names its date, never an id: preparation resolves the day and settles
whether the change creates it, replaces it or removes it.
"""

from __future__ import annotations

from datetime import date as calendar_date
from typing import Any

from ...foundation.errors import DomainError, StaleStateError
from ...models import ProposalChange
from ..proposals.api import (
    ApplyContext,
    PreparationContext,
    PreparedChange,
    ToolPreparationError,
    require_target,
)
from .model import DiaryEntry
from .use_cases import (
    create_diary_entry,
    delete_diary_entry,
    diary_entry_for,
    update_diary_entry,
)


class DiaryProposalHandler:
    entity = "diary"
    # A Diary proposal keeps the version it was prepared against.
    version_model: type[Any] | None = None

    async def prepare(self, context: PreparationContext, change: Any) -> PreparedChange:
        await self._resolve_day(context, change)
        _entry, expected_version = await require_target(context, change, DiaryEntry)
        return PreparedChange(values=dict(change.values), expected_version=expected_version)

    async def _resolve_day(self, context: PreparationContext, change: Any) -> None:
        """Point the change at the day it settles, and say whether that day exists yet."""
        try:
            entry_date = calendar_date.fromisoformat(str(change.values.get("date") or ""))
        except ValueError as error:
            raise ToolPreparationError(
                "invalid_arguments",
                "date must be a calendar date written as YYYY-MM-DD.",
                "Retry the diary call with the day you meant, written as YYYY-MM-DD.",
            ) from error
        saved = await diary_entry_for(context.session, entry_date)
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
        score = values.get("feeling_score")
        if score is None and saved is not None:
            # The day is replaced whole, the score is not: the model omits it whenever the
            # day left no sign of how it felt, and that is not the user asking to erase the
            # score they already have.  Resolved here so the review screen shows what Save
            # will actually store.
            score = saved.feeling_score
        change.values = {
            "entry_date": entry_date.isoformat(),
            "body": str(values.get("pov") or ""),
            "feeling_score": score,
            "remark": str(values.get("remark") or ""),
        }

    async def apply(self, context: ApplyContext, change: ProposalChange) -> list[int]:
        session = context.session
        values = dict(change.values)
        entry_date = calendar_date.fromisoformat(str(values["entry_date"]))
        feeling_score = values.get("feeling_score")
        if change.action == "create":
            entry = await create_diary_entry(
                session,
                entry_date=entry_date,
                body=str(values.get("body", "")),
                feeling_score=feeling_score,
            )
            return [entry.id]
        if change.action not in {"update", "delete"}:
            raise DomainError(f"Unsupported approved Diary action: {change.action}")
        entry = await session.get(DiaryEntry, change.entity_id) if change.entity_id else None
        if entry is None or entry.version != change.expected_version:
            raise StaleStateError("The Diary entry changed; refresh this proposal")
        entry_id = entry.id
        if change.action == "delete":
            await delete_diary_entry(session, entry_id)
        else:
            await update_diary_entry(
                session, entry_id, str(values.get("body", "")), feeling_score
            )
        return [entry_id]
