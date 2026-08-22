"""The Value mutation tool. The board subagent owns the turn that calls it."""

from __future__ import annotations

from ...ai.contracts import ValueToolInput
from ..proposals.api import MutationToolSpec, entity_change

VALUE_TOOL = MutationToolSpec(
    name="value",
    input_model=ValueToolInput,
    description="Propose one Value — a focus the user names and links Cards to.",
    to_change=entity_change("value"),
)
