"""The Request mutation tool. The workspace mutator owns the turn that calls it."""

from __future__ import annotations

from pydantic import Field

from tg_agent_shell.ai.autoapproval import SCALAR_UPDATE, AutoApprovalRule
from tg_agent_shell.ai.contracts import RecordToolInput
from tg_agent_shell.proposals.api import MutationToolSpec, entity_change


class RequestToolInput(RecordToolInput):
    content_fields = frozenset({"name", "description", "sql"})
    create_requires = ("name", "sql")

    name: str | None = None
    description: str | None = None
    sql: str | None = Field(
        default=None,
        description=(
            "Use one read-only SELECT or WITH ... SELECT over ai_* views.\n"
            "Return item_type ('card', 'check' or 'value') and id from ai_cards, ai_checks or ai_values.\n"
            "Example: SELECT 'check' AS item_type, id FROM ai_checks WHERE status = 'pending'."
        ),
    )


# `sql` is deliberately absent: a rewritten query changes what the Request means, and the
# owner is the only one who can see that from the words they used.
REQUEST_AUTOAPPROVALS = {
    "update": AutoApprovalRule(SCALAR_UPDATE, frozenset({"name", "description"}))
}

REQUEST_TOOL = MutationToolSpec(
    name="request",
    input_model=RequestToolInput,
    description="Propose one Request — a saved query over Cards, Checks and Values the user reruns.",
    to_change=entity_change("request"),
)
