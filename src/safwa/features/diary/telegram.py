"""How a proposed Diary day reads to the owner."""

from __future__ import annotations

import html
from collections.abc import Sequence
from datetime import date
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.ai.contracts import AgentChange
from tg_agent_shell.foundation.errors import DomainError
from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.media.telegram import send_photo_screen
from tg_agent_shell.proposals.api import (
    ChangeAction,
    ProposalChange,
    ProposalScreen,
)
from tg_agent_shell.proposals.render import (
    ACTION_VERBS,
    detail_lines,
)
from tg_agent_shell.telegram import Services

from .model import DiaryEntry
from .use_cases import day_media

DIARY_MONTH_NAMES = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)
FEELING_SCORE_EMOJI = {
    0: "⚫",
    1: "😨",
    2: "😞",
    3: "🙁",
    4: "😕",
    5: "😐",
    6: "🙂",
    7: "😊",
    8: "😃",
    9: "🤩",
    10: "🌟",
}


def diary_label(entry_date: date, feeling_score: int | None) -> str:
    """How a Diary day is named everywhere: on its screen, and on the link that opens it."""
    written = f"{entry_date.day} {DIARY_MONTH_NAMES[entry_date.month - 1]}"
    emoji = FEELING_SCORE_EMOJI.get(feeling_score) if feeling_score is not None else None
    return f"{written} · {emoji}{feeling_score}" if emoji else written


async def render_diary(
    message: Message,
    services: Services,
    entry_id: int,
    *,
    replace: bool | None = None,
) -> None:
    """One Diary day, in full and read-only: its photos as one album, its words below.

    The Diary is written through proposals alone.
    """
    async with services.sessions() as session:
        entry = await session.get(DiaryEntry, entry_id)
        if entry is None:
            raise DomainError("Diary entry does not exist")
        label = diary_label(entry.entry_date, entry.feeling_score)
        body = entry.body
        media_ids = [row.media_id for row in await day_media(session, entry_id)]
    rows: list[list[InlineKeyboardButton]] = []
    await send_photo_screen(
        message,
        services,
        media_ids,
        "\n\n".join(
            filter(None, (f"<b>📔 {html.escape(label)}</b>", html.escape(body or "")))
        ),
        kind=MessageKind.DASHBOARD,
        markup=InlineKeyboardMarkup(inline_keyboard=rows) if rows else None,
        related_id=entry_id,
        replace=replace,
    )


def _day_lines(change: ProposalChange) -> list[str]:
    """The day's shape, never its text: a receipt stays in the conversation for good.

    A day printed here would be re-read on every later turn and would spend the history
    budget it costs.  The Diary session holds its own draft, and a saved day is in
    `ai_diary`, so the body never has to travel. A photo is named by its few words.
    """
    values = dict(change.values)
    lines = [f"Date: {values.get('entry_date', '')}"]
    if change.action is ChangeAction.DELETE:
        lines.append("Entry: removed")
        return lines
    if values.get("body") is not None:
        lines.append(f"Entry: {len(str(values['body']))} characters")
    if values.get("feeling_score") is not None:
        lines.append(f"Feeling: {values['feeling_score']}")
    lines += [f"Photo added: {item['meta']}" for item in values.get("add_media") or ()]
    lines += [f"Photo taken off: {item['meta']}" for item in values.get("remove_media") or ()]
    return lines


class DiaryProposalPresenter:
    entity = "diary"

    def raw_details(self, change: AgentChange) -> list[str]:
        return detail_lines(dict(change.values))

    async def details(
        self, session: AsyncSession, change: ProposalChange, fallback: AgentChange | None
    ) -> list[str]:
        return _day_lines(change)

    async def summary(
        self, session: AsyncSession, change: ProposalChange, details: list[str]
    ) -> str:
        values = dict(change.values)
        verb = ACTION_VERBS.get(change.action, change.action.title())
        label = f"{verb} Diary entry for {values.get('entry_date', '')}".strip()
        score = values.get("feeling_score")
        return label if score is None else f"{label} with feeling score {score}"

    async def screen(
        self, session: AsyncSession, changes: Sequence[ProposalChange]
    ) -> ProposalScreen | None:
        current: dict[str, Any] = {}
        on_day: list[str] = []
        if changes[0].entity_id:
            entry = await session.get(DiaryEntry, changes[0].entity_id)
            if entry is not None:
                current = {"body": entry.body, "feeling_score": entry.feeling_score}
                on_day = [row.meta for row in await day_media(session, entry.id)]
        proposed = dict(current)
        added: list[str] = []
        removed: list[str] = []
        for change in changes:
            values = dict(change.values)
            added += [item["meta"] for item in values.pop("add_media", None) or ()]
            removed += [item["meta"] for item in values.pop("remove_media", None) or ()]
            if values.get("body") is None:
                # No words in the change: the words already saved stay.
                values.pop("body", None)
            proposed.update(values)
        # One day is one entry: whatever the last change does is what Save leaves.
        change = changes[-1]
        shown = current if change.action is ChangeAction.DELETE else proposed
        heading = f"Date: {html.escape(str(proposed.get('entry_date') or ''))}"
        if shown.get("feeling_score") is not None:
            score = int(shown["feeling_score"])
            heading += f"\nFeeling: {score} {FEELING_SCORE_EMOJI[score]}"
        rewritten = any(item.values.get("body") is not None for item in changes)
        if change.action is ChangeAction.UPDATE and rewritten and current.get("body"):
            heading += "\nThis replaces the entry already saved for that day."
        elif change.action is ChangeAction.DELETE:
            heading += "\nThis removes that day's entry for good."
        blocks = [heading]
        if change.action is ChangeAction.DELETE:
            blocks.append(html.escape(str(current.get("body") or "")))
            removed = on_day
        else:
            blocks.append(html.escape(str(proposed.get("body") or "")))
            if proposed.get("remark"):
                blocks.append(f"<i>{html.escape(str(proposed['remark']))}</i>")
        photos = [f"📷 {html.escape(meta)}" for meta in added] + [
            f"📷 <s>{html.escape(meta)}</s>" for meta in removed
        ]
        if photos:
            blocks.append("\n".join(photos))
        return ProposalScreen(
            mode=(
                "Create"
                if change.action is ChangeAction.CREATE
                else "Remove"
                if change.action is ChangeAction.DELETE
                else "Edit"
            ),
            item="Diary entry",
            blocks=tuple(block for block in blocks if block),
            # The Diary screen is the entry itself; a field diff would only repeat it.
            diffs=(),
        )


async def diary_citation_label(session: AsyncSession, services: Any, entry: DiaryEntry) -> str:
    return diary_label(entry.entry_date, entry.feeling_score)
