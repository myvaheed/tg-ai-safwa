"""The Check's Telegram adapter: its screens, its buttons and its review screen."""

from __future__ import annotations

from .handlers import CHECK_CALLBACK_ACTIONS, CHECK_TEXT_INPUTS
from .review import CheckProposalPresenter, check_citation_label
from .screens import (
    answer_button_label,
    check_line,
    render_check,
    render_check_values,
    render_checks,
)

__all__ = [
    "CHECK_CALLBACK_ACTIONS",
    "CHECK_TEXT_INPUTS",
    "CheckProposalPresenter",
    "answer_button_label",
    "check_citation_label",
    "check_line",
    "render_check",
    "render_check_values",
    "render_checks",
]
