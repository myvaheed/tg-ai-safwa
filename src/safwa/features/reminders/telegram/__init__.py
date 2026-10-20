"""A Reminder's Telegram adapter: the list, one Reminder, its editor and its review screen."""

from __future__ import annotations

from .review import TEXT_INPUT, ReminderProposalPresenter, reminder_citation_label
from .screens import (
    REMINDER_CALLBACK_ACTIONS,
    REMINDER_REVIEW_FIELDS,
    render_reminder,
    render_reminders,
)

__all__ = [
    "REMINDER_CALLBACK_ACTIONS",
    "REMINDER_REVIEW_FIELDS",
    "TEXT_INPUT",
    "ReminderProposalPresenter",
    "reminder_citation_label",
    "render_reminder",
    "render_reminders",
]
