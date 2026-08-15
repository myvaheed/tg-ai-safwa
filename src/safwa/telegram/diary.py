from __future__ import annotations

import html

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from ..domain import DomainError
from ..enums import MessageKind
from ..models import DiaryEntry
from ._core import Services
from ._messaging import send_registered
from ._presentation import diary_label


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
