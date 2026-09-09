"""The Card's Telegram adapter: its screens, its editors, its buttons and its review screen.

Big enough to be a package; a feature with one screen keeps it one file, and either way the
rest of Safwa writes `from .telegram import ...`.
"""

from __future__ import annotations

from .creation import render_card_creation, start_manual_card_creation
from .done_gate import render_check_resolution
from .draft import (
    card_creation_errors,
    card_editor_back_state,
    require_card_draft,
    sanitize_card_creation_state,
)
from .handlers import CARD_CALLBACK_ACTIONS
from .idea import IDEA_CALLBACK_ACTIONS, command_ideas, render_ideas
from .lists import (
    STAGE_QUICK_MOVE,
    card_list_rows,
    card_list_text,
    command_backlog,
    command_today,
    render_children,
    render_dashboard,
    stage_list_block,
)
from .presentation import (
    card_citation_label,
    card_overview_text,
    category_expression,
    energy_expression,
    kind_label,
)
from .review import CardProposalPresenter
from .screens import render_card
from .selectors import render_card_choices
from .text_input import CARD_TEXT_INPUTS

__all__ = [
    "CARD_CALLBACK_ACTIONS",
    "CARD_TEXT_INPUTS",
    "IDEA_CALLBACK_ACTIONS",
    "CardProposalPresenter",
    "card_citation_label",
    "card_creation_errors",
    "card_editor_back_state",
    "STAGE_QUICK_MOVE",
    "card_list_rows",
    "card_list_text",
    "card_overview_text",
    "category_expression",
    "command_backlog",
    "command_ideas",
    "command_today",
    "energy_expression",
    "kind_label",
    "render_card",
    "render_card_choices",
    "render_card_creation",
    "render_check_resolution",
    "render_children",
    "render_dashboard",
    "render_ideas",
    "stage_list_block",
    "require_card_draft",
    "sanitize_card_creation_state",
    "start_manual_card_creation",
]
