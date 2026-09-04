"""The Tag mutation tool. The workspace mutator owns the turn that calls it."""

from __future__ import annotations

from tg_agent_shell.ai.autoapproval import SCALAR_UPDATE, AutoApprovalRule
from tg_agent_shell.ai.contracts import RecordToolInput
from tg_agent_shell.proposals.api import MutationToolSpec, entity_change


class TagToolInput(RecordToolInput):
    content_fields = frozenset({"name", "description"})
    create_requires = ("name",)

    name: str | None = None
    description: str | None = None


TAG_AUTOAPPROVALS = {
    "update": AutoApprovalRule(SCALAR_UPDATE, frozenset({"name", "description"}))
}

TAG_TOOL = MutationToolSpec(
    name="tag",
    input_model=TagToolInput,
    description="Propose one Tag — a free label for finding Cards.",
    to_change=entity_change("tag"),
)
