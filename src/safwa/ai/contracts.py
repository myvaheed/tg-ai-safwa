from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AgentChange(BaseModel):
    entity: Literal["card", "tag", "value", "request", "sprint", "settings"]
    action: Literal[
        "create",
        "update",
        "move",
        "complete",
        "cancel",
        "reopen",
        "archive",
        "delete",
        "link",
        "unlink",
        "start",
        "finish",
    ]
    id: int | None = None
    values: dict[str, Any] = Field(default_factory=dict)


class AgentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["answer", "clarification", "proposal"]
    message: str
    changes: list[AgentChange] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_shape(self) -> AgentResponse:
        if self.kind == "answer" and self.changes:
            raise ValueError("answer cannot contain changes")
        if self.kind == "clarification" and self.changes:
            raise ValueError("clarification cannot contain operations")
        if self.kind == "proposal" and not self.changes:
            raise ValueError("proposal needs changes")
        return self


AGENT_RESPONSE_SCHEMA = AgentResponse.model_json_schema()
