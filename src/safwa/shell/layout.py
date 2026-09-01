"""What a screen looks like before it says anything: paging, menu, notices, titles."""

from __future__ import annotations

import html
from dataclasses import dataclass
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from ..constants import PAGE_SIZE
from ..foundation.screens import ScreenCommand

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


def menu_markup(
    commands: tuple[ScreenCommand, ...], *, sprint_active: bool
) -> InlineKeyboardMarkup:
    """The menu, as the screens themselves declared it.

    Today belongs to a running Sprint, so Planning does not offer it while there is none.
    """
    rows: dict[int, list[InlineKeyboardButton]] = {}
    for screen in commands:
        if screen.menu is None or (screen.needs_sprint and not sprint_active):
            continue
        rows.setdefault(screen.menu.row, []).append(
            InlineKeyboardButton(text=screen.menu.label, callback_data=f"nav:{screen.nav}")
        )
    return InlineKeyboardMarkup(inline_keyboard=[rows[row] for row in sorted(rows)])


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
