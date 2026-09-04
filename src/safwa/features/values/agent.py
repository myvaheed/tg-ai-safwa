"""The Value mutation tool. The workspace mutator owns the turn that calls it."""

from __future__ import annotations

from tg_agent_shell.ai.contracts import ValueToolInput
from tg_agent_shell.proposals.api import MutationToolSpec, entity_change

VALUE_TOOL = MutationToolSpec(
    name="value",
    input_model=ValueToolInput,
    description="Propose one Value — a focus the user names and links Cards to.",
    to_change=entity_change("value"),
)
