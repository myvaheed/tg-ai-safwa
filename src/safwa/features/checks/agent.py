"""The Check mutation tool. The workspace mutator owns the turn that calls it."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, PositiveInt, model_validator

from tg_agent_shell.ai.autoapproval import SCALAR_UPDATE, AutoApprovalRule
from tg_agent_shell.ai.contracts import AgentChange, Reference, ToolInput
from tg_agent_shell.proposals.api import ChangeAction, MutationToolSpec

# The answers are the Card lifecycle verbs underneath: Passed completes, Missed cancels.
ANSWER_MODES = {"passed": ChangeAction.COMPLETE, "missed": ChangeAction.CANCEL}


class CheckToolInput(ToolInput):
    content_fields = frozenset({"title", "schedule"})
    semantic_null_fields = frozenset({"schedule"})

    mode: Literal["create", "update", "passed", "missed", "link", "unlink"] = Field(
        description=(
            "passed and missed answer it, only when the user said how it went. link and "
            "unlink take `values`. Deleting is the remove tool."
        )
    )
    id: PositiveInt | None = None
    title: str | None = None
    schedule: str | None = Field(
        default=None,
        description=(
            "When it is asked on its own, in the user's words: 'every evening'. On update, "
            "null removes it. A Check with a Schedule stays off Cards."
        ),
    )
    values: Reference | list[Reference] | None = Field(
        default=None,
        description=(
            "Values it shows how well the user holds, each an exact Value name or an id. "
            "They are its own, not its Cards'."
        ),
    )

    @model_validator(mode="after")
    def validate_target(self) -> CheckToolInput:
        supplied = set(self.model_fields_set) - {"mode", "id"}
        if self.mode == "create":
            if self.id is not None:
                raise ValueError("a new Check must not include an id")
            if not (self.title or "").strip():
                raise ValueError("a new Check needs a title")
            if supplied - {"title", "schedule"}:
                raise ValueError("a new Check accepts only title and schedule")
            return self
        if self.id is None:
            raise ValueError(f"check mode '{self.mode}' needs an id")
        if self.mode == "update":
            editable = {"title", "schedule"}
            if not supplied:
                raise ValueError("an updated Check needs at least one proposed field")
            if unsupported := supplied - editable:
                raise ValueError("Check update does not accept: " + ", ".join(sorted(unsupported)))
        elif self.mode in {"link", "unlink"}:
            if not self.values:
                raise ValueError(f"Check {self.mode} needs values")
            if unsupported := supplied - {"values"}:
                raise ValueError(
                    f"Check {self.mode} does not accept: " + ", ".join(sorted(unsupported))
                )
        elif supplied:
            raise ValueError(f"Check {self.mode} does not accept fields")
        return self


def _check_change(call: BaseModel) -> AgentChange:
    values = call.model_dump(exclude_unset=True)
    mode = str(values.pop("mode"))
    return AgentChange(
        entity="check",
        action=ANSWER_MODES.get(mode, mode),
        id=values.pop("id", None),
        values=values,
    )


CHECK_AUTOAPPROVALS = {
    "update": AutoApprovalRule(SCALAR_UPDATE, frozenset({"title", "schedule", "schedule_rule"}))
}

CHECK_TOOL = MutationToolSpec(
    name="check",
    input_model=CheckToolInput,
    description=(
        "Propose one Check: a yes/no observation with no duration. Put it on a Card with "
        "`checks` of the goal or action tool."
    ),
    to_change=_check_change,
)
