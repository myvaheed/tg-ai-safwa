from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class AgentChange(BaseModel):
    entity: Literal["card", "board", "value", "sprint", "settings"]
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
    id: str | None = None
    values: dict[str, Any] = Field(default_factory=dict)


class AgentResponse(BaseModel):
    kind: Literal["answer", "query", "clarification", "proposal"]
    message: str
    sql: str | None = None
    changes: list[AgentChange] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_shape(self) -> AgentResponse:
        if self.kind == "answer" and (self.sql or self.changes):
            raise ValueError("answer cannot contain SQL or changes")
        if self.kind == "query" and (not self.sql or self.changes):
            raise ValueError("query needs one SQL statement and no changes")
        if self.kind == "clarification" and (self.sql or self.changes):
            raise ValueError("clarification cannot contain operations")
        if self.kind == "proposal" and (self.sql or not self.changes):
            raise ValueError("proposal needs changes and no SQL")
        return self


AGENT_RESPONSE_SCHEMA = AgentResponse.model_json_schema()
