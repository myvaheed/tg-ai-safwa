"""The Card's Telegram adapter: its screens, its editors and its review screen.

Big enough to be a package; a feature whose adapter is one screen keeps it one file, and
either way the rest of Safwa writes `from .telegram import ...`.
"""

from __future__ import annotations

from .creation import (
    card_creation_errors,
    card_creation_markup,
    card_editor_back_state,
    render_card_creation,
    require_card_draft,
    sanitize_card_creation_state,
    start_manual_card_creation,
)
from .lists import (
    card_list_rows,
    card_list_text,
    command_backlog,
    render_children,
    render_dashboard,
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
from .selectors import (
    CARD_CHOICE_FIELDS,
    CARD_DRAFT_CHOICE_FIELDS,
    CARD_DRAFT_RELATIONS,
    CARD_RELATION_TOGGLES,
    RELATION_CHOICES,
    handle_card_creation_chooser,
    render_card_choices,
)
from .text_input import CARD_TEXT_INPUTS

__all__ = [
    "CARD_CHOICE_FIELDS",
    "CARD_DRAFT_CHOICE_FIELDS",
    "CARD_DRAFT_RELATIONS",
    "CARD_RELATION_TOGGLES",
    "CARD_TEXT_INPUTS",
    "RELATION_CHOICES",
    "CardProposalPresenter",
    "card_citation_label",
    "card_creation_errors",
    "card_creation_markup",
    "card_editor_back_state",
    "card_list_rows",
    "card_list_text",
    "card_overview_text",
    "category_expression",
    "command_backlog",
    "energy_expression",
    "handle_card_creation_chooser",
    "kind_label",
    "render_card",
    "render_card_choices",
    "render_card_creation",
    "render_children",
    "render_dashboard",
    "require_card_draft",
    "sanitize_card_creation_state",
    "start_manual_card_creation",
]
