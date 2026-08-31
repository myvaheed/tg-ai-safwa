"""What a screen looks like before it says anything: paging, menu, notices, titles."""

from __future__ import annotations

import html
from dataclasses import dataclass
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from ..constants import PAGE_SIZE

CITATION_TITLE_LIMIT = 25


def short_citation_title(value: str) -> str:
    """Keep a citation recognisable without letting it consume an advisor reply."""
    title = value.strip()
    return f"{title[: CITATION_TITLE_LIMIT - 1]}…" if len(title) > CITATION_TITLE_LIMIT else title


def with_citation_fields(leading: str, fields: list[str]) -> str:
    return f"{leading} · {'·'.join(fields)}" if fields else leading


def with_notice(body: str, notice: str | None) -> str:
    """Carry a warning into the destination screen.

    A callback response replaces the current message, so a warning sent as its own
    message is overwritten by the next render.  It has to be part of that render.
    """
    return f"{html.escape(notice)}\n\n{body}" if notice else body


@dataclass(frozen=True)
class Page:
    items: list[Any]
    index: int
    count: int

    @property
    def label(self) -> str:
        return f"page {self.index + 1}/{self.count}"


def paginate(items: list[Any], page: int, size: int = PAGE_SIZE) -> Page:
    last = max(0, (len(items) - 1) // size)
    index = min(max(page, 0), last)
    return Page(items[index * size : (index + 1) * size], index, last + 1)


def menu_markup(*, sprint_active: bool) -> InlineKeyboardMarkup:
    """The menu. Today belongs to a running Sprint, so Planning does not offer it."""
    sprint_row = [InlineKeyboardButton(text="🏃 Sprint", callback_data="nav:sprint")]
    if sprint_active:
        sprint_row.insert(0, InlineKeyboardButton(text="☀️ Today", callback_data="nav:today"))
    return InlineKeyboardMarkup(
        inline_keyboard=[
            sprint_row,
            [
                InlineKeyboardButton(text="📚 Backlog", callback_data="nav:backlog"),
                InlineKeyboardButton(text="➕ Add", callback_data="nav:add"),
            ],
            [
                InlineKeyboardButton(text="💎 Values", callback_data="nav:values"),
                InlineKeyboardButton(text="🏷 Tags", callback_data="nav:tags"),
            ],
            [
                InlineKeyboardButton(text="🔎 Requests", callback_data="nav:requests"),
                InlineKeyboardButton(text="⏰ Reminders", callback_data="nav:reminders"),
                InlineKeyboardButton(text="⚙️ Settings", callback_data="nav:settings"),
            ],
        ]
    )


def menu_row() -> list[InlineKeyboardButton]:
    """A consistent escape hatch for a screen reached through quick actions."""
    return [InlineKeyboardButton(text="↩️ Menu", callback_data="nav:home")]


def start_payload(text: str | None) -> str | None:
    """The deep-link payload of a `/start <payload>` message, if this is one.

    `command_start` also serves the menu's Home button, where it is handed the bot's own
    screen — only a real command line may be read as a payload.
    """
    parts = (text or "").strip().split(maxsplit=1)
    if len(parts) != 2 or parts[0].split("@", 1)[0].casefold() != "/start":
        return None
    return parts[1].strip() or None
