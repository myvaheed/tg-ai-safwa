"""The Value mutation tool. The workspace mutator owns the turn that calls it."""

from __future__ import annotations

from tg_agent_shell.ai.autoapproval import SCALAR_UPDATE, AutoApprovalRule
from tg_agent_shell.ai.contracts import RecordToolInput
from tg_agent_shell.proposals.api import MutationToolSpec, entity_change


class ValueToolInput(RecordToolInput):
    content_fields = frozenset({"name", "description"})
    create_requires = ("name",)

    name: str | None = None
    description: str | None = None
    active: bool | None = None


VALUE_AUTOAPPROVALS = {
    "update": AutoApprovalRule(SCALAR_UPDATE, frozenset({"name", "description", "active"}))
}

VALUE_TOOL = MutationToolSpec(
    name="value",
    input_model=ValueToolInput,
    description="Propose one Value — a focus the user names and links Cards to.",
    to_change=entity_change("value"),
)
