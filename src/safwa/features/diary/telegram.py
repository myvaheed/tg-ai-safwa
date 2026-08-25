"""How a proposed Diary day reads to the owner."""

from __future__ import annotations

import html
from datetime import date
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from ...ai.contracts import AgentChange
from ...enums import MessageKind
from ...foundation.errors import DomainError
from ...models import ProposalChange
from ...telegram._core import Services
from ...telegram._messaging import send_registered
from ..proposals.api import (
    ACTION_VERBS,
    ProposalScreen,
    detail_lines,
)
from .model import DiaryEntry

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
    extra_rows: list[list[InlineKeyboardButton]] | None = None,
    replace: bool | None = None,
) -> None:
    """One Diary day, in full and read-only: the Diary is written through proposals alone."""
    async with services.sessions() as session:
        entry = await session.get(DiaryEntry, entry_id)
        if entry is None:
            raise DomainError("Diary entry does not exist")
        label = diary_label(entry.entry_date, entry.feeling_score)
        body = entry.body
    rows = list(extra_rows or [])
    await send_registered(
        message,
        services,
        f"<b>📔 {html.escape(label)}</b>\n\n{html.escape(body)}",
        kind=MessageKind.DASHBOARD,
        markup=InlineKeyboardMarkup(inline_keyboard=rows) if rows else None,
        related_id=entry_id,
        replace=replace,
    )


def _day_lines(change: ProposalChange) -> list[str]:
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
        self, session: AsyncSession, change: ProposalChange
    ) -> ProposalScreen | None:
        current: dict[str, Any] = {}
        if change.entity_id:
            entry = await session.get(DiaryEntry, change.entity_id)
            if entry is not None:
                current = {"body": entry.body, "feeling_score": entry.feeling_score}
        proposed = {**current, **dict(change.values)}
        shown = current if change.action == "delete" else proposed
        heading = f"Date: {html.escape(str(proposed.get('entry_date') or ''))}"
        if shown.get("feeling_score") is not None:
            score = int(shown["feeling_score"])
            heading += f"\nFeeling: {score} {FEELING_SCORE_EMOJI[score]}"
        if change.action == "update":
            heading += "\nThis replaces the entry already saved for that day."
        elif change.action == "delete":
            heading += "\nThis removes that day's entry for good."
        blocks = [heading]
        if change.action == "delete":
            blocks.append(html.escape(str(current.get("body") or "")))
        else:
            blocks.append(html.escape(str(proposed.get("body") or "")))
            if proposed.get("remark"):
                blocks.append(f"<i>{html.escape(str(proposed['remark']))}</i>")
        return ProposalScreen(
            mode=(
                "Create"
                if change.action == "create"
                else "Remove"
                if change.action == "delete"
                else "Edit"
            ),
            item="Diary entry",
            blocks=tuple(blocks),
            # The Diary screen is the entry itself; a field diff would only repeat it.
            diffs=(),
        )
