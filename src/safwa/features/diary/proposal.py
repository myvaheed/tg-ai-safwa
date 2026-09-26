"""How a proposed Diary day is checked and then written.

A Diary change names its date, never an id: preparation resolves the day and settles
whether the change creates it, replaces it or removes it, and whether the photos it puts
on the day and takes off it fit there.
"""

from __future__ import annotations

from datetime import date as calendar_date
from typing import Any

from tg_agent_shell.foundation.errors import DomainError, StaleStateError
from tg_agent_shell.media.library import ChatMedia
from tg_agent_shell.proposals.api import (
    ApplyContext,
    ChangeAction,
    PreparationContext,
    PreparedChange,
    ProposalChange,
    ToolPreparationError,
    require_target,
)

from .model import DiaryEntry
from .use_cases import (
    DIARY_DAY_PHOTOS,
    create_diary_entry,
    day_media,
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
        if change.action is ChangeAction.DELETE:
            if saved is None:
                raise ToolPreparationError(
                    "target_not_found",
                    f"There is no Diary entry for {entry_date.isoformat()}.",
                    "Tell the user that day has nothing written. Propose nothing.",
                )
            change.id = saved.id
            change.values = {"entry_date": entry_date.isoformat()}
            return
        change.action = ChangeAction.UPDATE if saved is not None else ChangeAction.CREATE
        change.id = saved.id if saved is not None else None
        values = dict(change.values)
        score = values.get("feeling_score")
        if score is None and saved is not None:
            # The day is replaced whole, the score is not: the model omits it whenever the
            # day left no sign of how it felt, and that is not the user asking to erase the
            # score they already have.  Resolved here so the review screen shows what Save
            # will actually store.
            score = saved.feeling_score
        added, removed = await self._resolve_media(context, saved, entry_date, values)
        change.values = {
            "entry_date": entry_date.isoformat(),
            # None leaves the words already saved for that day.
            "body": values.get("pov"),
            "feeling_score": score,
            "remark": str(values.get("remark") or ""),
            **({"add_media": added} if added else {}),
            **({"remove_media": removed} if removed else {}),
        }

    async def _resolve_media(
        self,
        context: PreparationContext,
        saved: DiaryEntry | None,
        entry_date: calendar_date,
        values: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """The photos a change puts on the day and takes off it, each by number and words."""
        day = entry_date.isoformat()
        on_day = (
            {row.media_id: row.meta for row in await day_media(context.session, saved.id)}
            if saved is not None
            else {}
        )
        removed: list[dict[str, Any]] = []
        for media_id in dict.fromkeys(int(number) for number in values.get("remove_media") or ()):
            if media_id not in on_day:
                raise ToolPreparationError(
                    "media_not_on_day",
                    f"Photo {media_id} is not on {day}.",
                    "Take off only a photo read_day lists for that day.",
                )
            removed.append({"media_id": media_id, "meta": on_day[media_id]})
        added: dict[int, dict[str, Any]] = {}
        for item in values.get("add_media") or ():
            media_id = int(item["media_id"])
            if await context.session.get(ChatMedia, media_id) is None:
                raise ToolPreparationError(
                    "media_not_found",
                    f"No photo has the number {media_id}.",
                    "Use N from a photo's [words](media:N) label in the conversation.",
                )
            if media_id in on_day and media_id not in {item["media_id"] for item in removed}:
                raise ToolPreparationError(
                    "media_already_on_day",
                    f"Photo {media_id} is already on {day}.",
                    "Leave it out of add_media.",
                )
            added.setdefault(media_id, {"media_id": media_id, "meta": str(item["meta"]).strip()})
        if len(on_day) - len(removed) + len(added) > DIARY_DAY_PHOTOS:
            raise ToolPreparationError(
                "day_full",
                f"{day} holds {len(on_day)} photos, and a day holds at most {DIARY_DAY_PHOTOS}.",
                "Tell the user that day is full. Put the photo on it only once they take one off.",
            )
        return list(added.values()), removed

    async def apply(self, context: ApplyContext, change: ProposalChange) -> list[int]:
        session = context.session
        values = dict(change.values)
        entry_date = calendar_date.fromisoformat(str(values["entry_date"]))
        feeling_score = values.get("feeling_score")
        added = [(item["media_id"], item["meta"]) for item in values.get("add_media") or ()]
        if change.action is ChangeAction.CREATE:
            entry = await create_diary_entry(
                session,
                entry_date=entry_date,
                body=values.get("body"),
                feeling_score=feeling_score,
                media=added,
            )
            return [entry.id]
        if change.action not in {ChangeAction.UPDATE, ChangeAction.DELETE}:
            raise DomainError(f"Unsupported approved Diary action: {change.action}")
        entry = await session.get(DiaryEntry, change.entity_id) if change.entity_id else None
        if entry is None or entry.version != change.expected_version:
            raise StaleStateError("The Diary entry changed; refresh this proposal")
        entry_id = entry.id
        if change.action is ChangeAction.DELETE:
            await delete_diary_entry(session, entry_id)
        else:
            await update_diary_entry(
                session,
                entry_id,
                values.get("body"),
                feeling_score,
                add_media=added,
                remove_media=[item["media_id"] for item in values.get("remove_media") or ()],
            )
        return [entry_id]
