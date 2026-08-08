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


class CardToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["draft", "edit", "move", "complete", "cancel", "reopen", "link", "unlink"]
    id: int | None = None
    kind: Literal["goal", "idea", "action"] | None = None
    title: str | None = None
    note: str | None = None
    stage: Literal["backlog", "sprint", "today", "done", "cancelled"] | None = None
    priority: Literal["critical", "medium", "low"] | None = None
    hard_time: bool | None = None
    effort_points: Literal[1, 2, 3, 5, 8, 13] | None = None
    repeatable: bool | None = None
    categories: list[Literal["self", "contribution", "work", "rest"]] | None = None
    energy_types: list[Literal["physical", "cognitive", "social", "values"]] | None = None
    value_id: int | None = None
    value_ids: list[int] | None = None
    value_query: str | list[str] | None = None
    tag_id: int | None = None
    tag_ids: list[int] | None = None
    tag_query: str | list[str] | None = None
    parent_id: int | None = None
    parent_query: str | None = None
    draft_ref: str | None = None
    parent_draft_ref: str | None = None
    blocker_id: int | None = None
    blocker_ids: list[int] | None = None
    copy_to_repeat: bool | None = None

    @model_validator(mode="after")
    def validate_target(self) -> CardToolInput:
        if self.mode == "draft":
            if self.id is not None:
                raise ValueError("a draft must not include an id")
            if self.kind is None or not (self.title or "").strip():
                raise ValueError("a draft needs kind and title")
        elif self.id is None:
            raise ValueError(f"card mode '{self.mode}' needs an id")
        return self


class ValueToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["create", "edit"]
    id: int | None = None
    name: str | None = None
    description: str | None = None
    active: bool | None = None

    @model_validator(mode="after")
    def validate_target(self) -> ValueToolInput:
        if self.mode == "create" and not (self.name or "").strip():
            raise ValueError("a new Value needs a name")
        if self.mode == "edit" and self.id is None:
            raise ValueError("an edited Value needs an id")
        return self


class TagToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["create", "edit"]
    id: int | None = None
    name: str | None = None
    description: str | None = None

    @model_validator(mode="after")
    def validate_target(self) -> TagToolInput:
        if self.mode == "create" and not (self.name or "").strip():
            raise ValueError("a new Tag needs a name")
        if self.mode == "edit" and self.id is None:
            raise ValueError("an edited Tag needs an id")
        return self


class RequestToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["create", "edit"]
    id: int | None = None
    name: str | None = None
    description: str | None = None
    sql: str | None = Field(
        default=None,
        description=(
            "One read-only SELECT or WITH ... SELECT over ai_* views. It must query ai_cards "
            "and return a column named id; for example: SELECT id FROM ai_cards WHERE kind = 'action'."
        ),
    )

    @model_validator(mode="after")
    def validate_target(self) -> RequestToolInput:
        if self.mode == "create" and (
            not (self.name or "").strip() or not (self.sql or "").strip()
        ):
            raise ValueError("a new Request needs name and sql")
        if self.mode == "edit" and self.id is None:
            raise ValueError("an edited Request needs an id")
        return self


class RemoveToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["card", "tag", "value", "request"]
    id: int
    permanent: bool = False

    @model_validator(mode="after")
    def validate_permanent(self) -> RemoveToolInput:
        if self.permanent and self.type != "card":
            raise ValueError("only Cards support permanent deletion")
        return self


MUTATION_TOOL_MODELS: dict[str, type[BaseModel]] = {
    "card": CardToolInput,
    "value": ValueToolInput,
    "tag": TagToolInput,
    "request": RequestToolInput,
    "remove": RemoveToolInput,
}


def mutation_change_from_tool(name: str, arguments: dict[str, Any]) -> AgentChange:
    """Validate a model tool call and convert it into an application command intent."""
    model = MUTATION_TOOL_MODELS.get(name)
    if model is None:
        raise ValueError(f"Unknown mutation tool: {name}")
    payload = model.model_validate(arguments).model_dump(exclude_none=True)
    if name == "remove":
        return AgentChange(
            entity=payload["type"],
            action="delete" if payload["permanent"] else "archive",
            id=payload["id"],
        )
    mode = payload.pop("mode")
    entity = name
    action = "create" if mode == "draft" else "update" if mode == "edit" else mode
    identifier = payload.pop("id", None)
    return AgentChange(entity=entity, action=action, id=identifier, values=payload)
