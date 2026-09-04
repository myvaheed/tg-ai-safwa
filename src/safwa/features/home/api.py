"""What another feature may ask of Home: the menu, drawn the way this screen draws it.

A screen says what it is called. Where that name is offered as a button is the menu's own
business, so the layout is here and no feature knows a row number.
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from tg_agent_shell.foundation.screens import ScreenCommand

# One row per line, each naming the `nav` action of a screen that declared a title.
MENU_LAYOUT: tuple[tuple[str, ...], ...] = (
    ("today", "sprint"),
    ("backlog", "add"),
    ("values", "tags"),
    ("settings", "reminders", "requests"),
)


def menu_markup(
    commands: tuple[ScreenCommand, ...], *, sprint_active: bool
) -> InlineKeyboardMarkup:
    """The menu: every title the screens declared, in the order this layout names them.

    Today belongs to a running Sprint, so it is left out while there is none.
    """
    titles = {
        screen.nav: screen.title
        for screen in commands
        if screen.title is not None and (sprint_active or not screen.needs_sprint)
    }
    rows = [
        [
            InlineKeyboardButton(text=titles[nav], callback_data=f"nav:{nav}")
            for nav in row
            if nav in titles
        ]
        for row in MENU_LAYOUT
    ]
    return InlineKeyboardMarkup(inline_keyboard=[row for row in rows if row])
