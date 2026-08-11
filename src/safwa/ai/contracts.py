from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AgentChange(BaseModel):
    entity: Literal["card", "check", "tag", "value", "request"]
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
        "resolve",
        "resolve_for_card",
    ]
    id: int | None = None
    values: dict[str, Any] = Field(default_factory=dict)


class CardToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["create", "edit", "move", "complete", "cancel", "reopen", "link", "unlink"]
    id: int | None = None
    kind: Literal["goal", "idea", "action"] | None = None
    title: str | None = None
    note: str | None = None
    stage: Literal["backlog", "sprint", "today", "done", "cancelled"] | None = None
    priority: Literal["critical", "medium", "low"] | None = None
    hard_time: bool | None = None
    blocked: bool | None = None
    blocked_description: str | None = None
    effort_points: Literal[1, 2, 3, 5, 8, 13] | None = None
    repeatable: bool | None = None
    categories: list[Literal["self", "contribution", "work", "rest"]] | None = None
    energy_types: list[Literal["physical", "cognitive", "social", "values"]] | None = None
    value_id: int | None = None
    value_ids: list[int] | None = None
    value_query: str | list[str] | None = Field(
        default=None, description="One or more exact Value names; this is not SQL."
    )
    tag_id: int | None = None
    tag_ids: list[int] | None = None
    tag_query: str | list[str] | None = Field(
        default=None, description="One or more exact Tag names; this is not SQL."
    )
    parent_id: int | None = None
    parent_query: str | None = Field(
        default=None,
        description=(
            "A safe read-only SELECT over ai_cards that returns exactly one id, for example "
            "SELECT id FROM ai_cards WHERE title = 'My Goal'. An exact Card title is also accepted."
        ),
    )
    @model_validator(mode="after")
    def validate_target(self) -> CardToolInput:
        supplied = set(self.model_fields_set) - {"mode", "id"}
        if self.mode == "create":
            if self.id is not None:
                raise ValueError("a new Card must not include an id")
            if self.kind is None or not (self.title or "").strip():
                raise ValueError("a new Card needs kind and title")
            if self.kind == "action" and self.effort_points is None:
                raise ValueError("a new Action needs effort_points")
            if self.blocked and not (self.blocked_description or "").strip():
                raise ValueError("a blocked Card needs blocked_description")
            return self
        if self.id is None:
            raise ValueError(f"card mode '{self.mode}' needs an id")
        editable = {
            "title",
            "note",
            "stage",
            "priority",
            "hard_time",
            "blocked",
            "blocked_description",
            "effort_points",
            "repeatable",
            "categories",
            "energy_types",
            "value_id",
            "value_ids",
            "value_query",
            "tag_id",
            "tag_ids",
            "tag_query",
            "parent_id",
            "parent_query",
        }
        if self.mode == "edit":
            if not supplied:
                raise ValueError("an edited Card needs at least one proposed field")
            if unsupported := supplied - editable:
                raise ValueError("Card edit does not accept: " + ", ".join(sorted(unsupported)))
            if self.stage in {"done", "cancelled"}:
                raise ValueError("use complete or cancel mode for a terminal Card stage")
            if self.blocked and not (self.blocked_description or "").strip():
                raise ValueError("a blocked Card needs blocked_description")
        elif self.mode == "move":
            if supplied != {"stage"} or self.stage is None:
                raise ValueError("Card move needs only a stage")
            if self.stage in {"done", "cancelled"}:
                raise ValueError("use complete or cancel mode for a terminal Card stage")
        elif self.mode in {"complete", "cancel"}:
            if supplied:
                raise ValueError(f"Card {self.mode} does not accept fields")
        elif self.mode == "reopen":
            if supplied - {"stage"}:
                raise ValueError("Card reopen accepts only an optional stage")
            if self.stage in {"done", "cancelled"}:
                raise ValueError("a reopened Card returns to a live stage")
        elif self.mode in {"link", "unlink"}:
            groups = [
                supplied & {"value_id", "value_ids", "value_query"},
                supplied & {"tag_id", "tag_ids", "tag_query"},
            ]
            selected = [group for group in groups if group]
            if len(selected) != 1:
                raise ValueError(f"Card {self.mode} needs exactly one relationship type")
            allowed = selected[0]
            if supplied - allowed:
                raise ValueError(f"Card {self.mode} mixes unrelated fields")
        return self


class CheckToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["create", "edit", "resolve", "resolve_for_card", "link", "unlink"]
    id: int | None = None
    card_id: int | None = None
    title: str | None = None
    note: str | None = None
    repeatable: bool | None = None
    outcome: Literal["passed", "failed", "not_applicable"] | None = None
    value_id: int | None = None
    value_ids: list[int] | None = None
    value_query: str | list[str] | None = Field(
        default=None, description="One or more exact Value names; this is not SQL."
    )
    tag_id: int | None = None
    tag_ids: list[int] | None = None
    tag_query: str | list[str] | None = Field(
        default=None, description="One or more exact Tag names; this is not SQL."
    )

    @model_validator(mode="after")
    def validate_target(self) -> CheckToolInput:
        supplied = set(self.model_fields_set) - {"mode", "id"}
        relationships = {
            "value_id",
            "value_ids",
            "value_query",
            "tag_id",
            "tag_ids",
            "tag_query",
        }
        if self.mode == "create":
            if self.id is not None:
                raise ValueError("a new Check must not include an id")
            if not (self.title or "").strip():
                raise ValueError("a new Check needs a title")
            if self.outcome is not None:
                raise ValueError("a new Check starts Pending and takes no outcome")
            return self
        if self.mode == "resolve_for_card":
            if self.card_id is None:
                raise ValueError("resolve_for_card needs a card_id")
            if supplied - {"card_id"}:
                raise ValueError("resolve_for_card accepts only a card_id")
            return self
        if self.id is None:
            raise ValueError(f"check mode '{self.mode}' needs an id")
        if self.mode == "edit":
            editable = {"title", "note", "repeatable", "card_id", *relationships}
            if not supplied:
                raise ValueError("an edited Check needs at least one proposed field")
            if unsupported := supplied - editable:
                raise ValueError("Check edit does not accept: " + ", ".join(sorted(unsupported)))
        elif self.mode == "resolve":
            if self.outcome is None:
                raise ValueError("resolve needs an outcome")
            if supplied - {"outcome"}:
                raise ValueError("Check resolve accepts only an outcome")
        elif self.mode in {"link", "unlink"}:
            groups = [
                supplied & {"value_id", "value_ids", "value_query"},
                supplied & {"tag_id", "tag_ids", "tag_query"},
            ]
            selected = [group for group in groups if group]
            if len(selected) != 1:
                raise ValueError(f"Check {self.mode} needs exactly one relationship type")
            if supplied - selected[0]:
                raise ValueError(f"Check {self.mode} mixes unrelated fields")
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
        if self.mode == "edit" and not (self.model_fields_set - {"mode", "id"}):
            raise ValueError("an edited Value needs at least one proposed field")
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
        if self.mode == "edit" and not (self.model_fields_set - {"mode", "id"}):
            raise ValueError("an edited Tag needs at least one proposed field")
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
        if self.mode == "edit" and not (self.model_fields_set - {"mode", "id"}):
            raise ValueError("an edited Request needs at least one proposed field")
        return self


class RemoveToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["card", "check", "tag", "value", "request"]
    id: int
    permanent: bool = False

    @model_validator(mode="after")
    def validate_permanent(self) -> RemoveToolInput:
        if self.permanent and self.type != "card":
            raise ValueError("only Cards support permanent deletion")
        return self


MUTATION_TOOL_MODELS: dict[str, type[BaseModel]] = {
    "card": CardToolInput,
    "check": CheckToolInput,
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
    payload = model.model_validate(arguments).model_dump(exclude_unset=True)
    if name == "remove":
        return AgentChange(
            entity=payload["type"],
            action="delete" if payload.get("permanent", False) else "archive",
            id=payload["id"],
        )
    mode = payload.pop("mode")
    entity = name
    action = "update" if mode == "edit" else mode
    identifier = payload.pop("id", None)
    return AgentChange(entity=entity, action=action, id=identifier, values=payload)
