"""The Reminder mutation tool. The board subagent owns the turn that calls it."""

from __future__ import annotations

from ...ai.contracts import ReminderToolInput
from ..proposals.api import MutationToolSpec, entity_change

REMINDER_TOOL = MutationToolSpec(
    name="reminder",
    input_model=ReminderToolInput,
    description=(
        "Propose one Reminder — instruction text plus timing. The text comes back as a request "
        "when it fires."
    ),
    to_change=entity_change("reminder"),
)
