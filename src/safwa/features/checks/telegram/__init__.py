"""The Check's Telegram adapter: its screens, its buttons and its review screen."""

from __future__ import annotations

from .handlers import CHECK_CALLBACK_ACTIONS
from .review import CheckProposalPresenter, check_citation_label
from .screens import (
    CHECK_OUTCOME_LABELS,
    CHECK_STATUS_EMOJIS,
    SETTABLE_OUTCOMES,
    outcome_button_label,
    render_check,
    render_check_values,
    render_checks,
)

__all__ = [
    "CHECK_CALLBACK_ACTIONS",
    "CHECK_OUTCOME_LABELS",
    "CHECK_STATUS_EMOJIS",
    "SETTABLE_OUTCOMES",
    "CheckProposalPresenter",
    "check_citation_label",
    "outcome_button_label",
    "render_check",
    "render_check_values",
    "render_checks",
]
