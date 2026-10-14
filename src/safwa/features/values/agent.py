"""The Value mutation tool. The workspace mutator owns the turn that calls it."""

from __future__ import annotations

from pydantic import Field

from tg_agent_shell.ai.autoapproval import SCALAR_UPDATE, AutoApprovalRule
from tg_agent_shell.ai.contracts import RecordToolInput
from tg_agent_shell.proposals.api import MutationToolSpec, entity_change

from ...constants import DESCRIPTION_MAX_CHARS


class ValueToolInput(RecordToolInput):
    content_fields = frozenset({"name", "description"})
    create_requires = ("name", "description")

    name: str | None = None
    description: str | None = Field(
        default=None,
        description=(
            "Why it matters and what the user does for it. "
            f"At most {DESCRIPTION_MAX_CHARS} characters."
        ),
    )


VALUE_AUTOAPPROVALS = {
    "update": AutoApprovalRule(SCALAR_UPDATE, frozenset({"name", "description"}))
}

VALUE_TOOL = MutationToolSpec(
    name="value",
    input_model=ValueToolInput,
    description="Propose one Value — what the user cares about, carried by the Cards that serve it.",
    to_change=entity_change("value"),
)
