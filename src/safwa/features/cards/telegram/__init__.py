"""The Card's Telegram adapter: its screens, its editors, its buttons and its review screen.

Big enough to be a package; a feature with one screen keeps it one file, and either way the
rest of Safwa writes `from .telegram import ...`.
"""

from __future__ import annotations

from .board import command_board, render_backlog, render_board
from .creation import render_card_creation, start_manual_card_creation
from .done_gate import render_check_resolution
from .draft import (
    card_creation_errors,
    require_card_draft,
    sanitize_card_creation_state,
)
from .handlers import CARD_CALLBACK_ACTIONS
from .lists import render_children
from .presentation import (
    CATEGORY_COLORS,
    CATEGORY_EMOJIS,
    ENERGY_COLORS,
    ENERGY_EMOJIS,
    card_citation_label,
    card_overview_text,
    category_expression,
    energy_expression,
    item_button_label,
    kind_label,
)
from .review import CardProposalPresenter
from .screens import render_card
from .selectors import render_card_choices
from .text_input import CARD_TEXT_INPUTS

__all__ = [
    "CARD_CALLBACK_ACTIONS",
    "CATEGORY_COLORS",
    "CATEGORY_EMOJIS",
    "ENERGY_COLORS",
    "ENERGY_EMOJIS",
    "CARD_TEXT_INPUTS",
    "CardProposalPresenter",
    "card_citation_label",
    "card_creation_errors",
    "card_overview_text",
    "category_expression",
    "command_board",
    "energy_expression",
    "item_button_label",
    "kind_label",
    "render_card",
    "render_card_choices",
    "render_card_creation",
    "render_check_resolution",
    "render_backlog",
    "render_board",
    "render_children",
    "require_card_draft",
    "sanitize_card_creation_state",
    "start_manual_card_creation",
]
