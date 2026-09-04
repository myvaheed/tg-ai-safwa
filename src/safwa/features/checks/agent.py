"""The Check mutation tool. The workspace mutator owns the turn that calls it."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, PositiveInt, model_validator

from tg_agent_shell.ai.autoapproval import SCALAR_UPDATE, AutoApprovalRule
from tg_agent_shell.ai.contracts import ToolInput
from tg_agent_shell.proposals.api import MutationToolSpec, entity_change


class CheckToolInput(ToolInput):
    content_fields = frozenset({"title"})

    mode: Literal["create", "update", "complete", "cancel", "link", "unlink"] = Field(
        description=(
            "complete answers the Check Passed and cancel answers it Missed; update renames it "
            "or changes repeatable; link and unlink put a Value on this Check or take it off. "
            "Archiving is the remove tool."
        )
    )
    id: PositiveInt | None = None
    title: str | None = None
    repeatable: bool | None = None
    value_id: PositiveInt | None = None
    value_ids: list[PositiveInt] | None = None
    value_query: str | list[str] | None = Field(
        default=None, description="One or more exact Value names; this is not SQL."
    )

    @model_validator(mode="after")
    def validate_target(self) -> CheckToolInput:
        supplied = set(self.model_fields_set) - {"mode", "id"}
        if self.mode == "create":
            if self.id is not None:
                raise ValueError("a new Check must not include an id")
            if not (self.title or "").strip():
                raise ValueError("a new Check needs a title")
            if supplied - {"title", "repeatable"}:
                raise ValueError("a new Check accepts only title and repeatable")
            return self
        if self.id is None:
            raise ValueError(f"check mode '{self.mode}' needs an id")
        if self.mode == "update":
            editable = {"title", "repeatable"}
            if not supplied:
                raise ValueError("an updated Check needs at least one proposed field")
            if unsupported := supplied - editable:
                raise ValueError("Check update does not accept: " + ", ".join(sorted(unsupported)))
        elif self.mode in {"link", "unlink"}:
            if not supplied & {"value_id", "value_ids", "value_query"}:
                raise ValueError(f"Check {self.mode} needs a Value")
            if unsupported := supplied - {"value_id", "value_ids", "value_query"}:
                raise ValueError(
                    f"Check {self.mode} does not accept: " + ", ".join(sorted(unsupported))
                )
        elif supplied:
            raise ValueError(f"Check {self.mode} does not accept fields")
        return self


CHECK_AUTOAPPROVALS = {
    "update": AutoApprovalRule(SCALAR_UPDATE, frozenset({"title", "repeatable"}))
}

CHECK_TOOL = MutationToolSpec(
    name="check",
    input_model=CheckToolInput,
    description=(
        "Propose one Check — a state observation on a Card. Answer one only when the user "
        "already said how it went; otherwise cite it and let them. Use mode=link or "
        "mode=unlink to put a Value on this Check or take it off."
    ),
    to_change=entity_change("check"),
)
