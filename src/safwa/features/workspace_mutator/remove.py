"""`remove` — the one tool that deletes, and the one that archives.

It owns no entity of its own: the change it produces is handled by the feature that owns
the target, which is why it registers as a plain mutation tool.

Archiving exists so the workspace does not fill up, so only what the owner makes many of
and reaches a state where it is over may be archived: a Card and a Check. Everything else
is deleted, and deleting is never quietly turned into archiving.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, PositiveInt, model_validator

from tg_agent_shell.ai.contracts import AgentChange, ToolInput
from tg_agent_shell.proposals.api import MutationToolSpec

ARCHIVABLE = {"card", "check"}


class RemoveToolInput(ToolInput):
    mode: Literal["delete", "archive"] = Field(
        default="delete",
        description=(
            "delete erases it. archive only hides a Card or a Check that is already "
            "closed, and that happens on its own two Sprints later."
        ),
    )
    entity: Literal["card", "check", "tag", "value", "request", "reminder"]
    id: PositiveInt

    @model_validator(mode="after")
    def validate_target(self) -> RemoveToolInput:
        if self.mode == "archive" and self.entity not in ARCHIVABLE:
            raise ValueError(f"a {self.entity} is deleted, never archived")
        return self


def _remove_change(call: BaseModel) -> AgentChange:
    return AgentChange(entity=call.entity, action=call.mode, id=call.id)


REMOVE_TOOL = MutationToolSpec(
    name="remove",
    input_model=RemoveToolInput,
    description="Delete one item of any kind, or archive a closed Card or Check.",
    to_change=_remove_change,
)
