from __future__ import annotations

import json
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, field_validator, model_validator

_NULLISH_STRINGS = frozenset({"null", "none", "nil", "undefined"})
_CONTENT_STRING_FIELDS = frozenset(
    {"title", "note", "blocked_description", "name", "description", "sql"}
)
_COLLECTION_FIELDS = frozenset(
    {"categories", "energy_types", "value_ids", "tag_ids", "check_ids"}
)
_QUERY_FIELDS = frozenset({"value_query", "tag_query", "check_query", "parent_query"})


def _is_nullish_string(value: Any) -> bool:
    return isinstance(value, str) and value.strip().casefold() in _NULLISH_STRINGS


def _decode_collection(value: Any) -> Any:
    """Recover a JSON array accidentally double-encoded by a model/provider."""
    if not isinstance(value, str) or not value.strip().startswith("["):
        return value
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return value
    return decoded if isinstance(decoded, list) else value


def _normalized_tool_payload(model: type[BaseModel], value: Any) -> Any:
    """Remove harmless LLM placeholders without guessing at real user content.

    Unknown and required fields are deliberately left alone so Pydantic can reject them.
    Explicit empty strings/lists are preserved because they may mean "clear this text/set".
    """
    if not isinstance(value, dict):
        return value

    payload = dict(value)
    semantic_null_fields = getattr(model, "semantic_null_fields", frozenset())
    mode = payload.get("mode")
    for name, raw_value in list(payload.items()):
        field = model.model_fields.get(name)
        if field is None or field.is_required():
            continue

        if (name == "id" or name.endswith("_id")) and (
            raw_value == 0 or raw_value == "0"
        ):
            payload.pop(name)
            continue

        if name in semantic_null_fields and mode == "edit" and (
            raw_value is None or _is_nullish_string(raw_value)
        ):
            payload[name] = None
            continue

        if raw_value is None:
            payload.pop(name)
            continue

        if name not in _CONTENT_STRING_FIELDS and (
            _is_nullish_string(raw_value)
            or (isinstance(raw_value, str) and not raw_value.strip())
        ):
            payload.pop(name)
            continue

        if name in _COLLECTION_FIELDS or name in _QUERY_FIELDS:
            decoded = _decode_collection(raw_value)
            if isinstance(decoded, list):
                cleaned = [
                    item
                    for item in decoded
                    if item is not None
                    and not _is_nullish_string(item)
                    and not (isinstance(item, str) and not item.strip())
                ]
                # [] is an explicit set clear; [null]/["none"] is placeholder noise.
                if name.endswith("_ids"):
                    cleaned = [item for item in cleaned if item != 0 and item != "0"]
                if (decoded and not cleaned) or (
                    mode == "create" and name.endswith("_ids") and not cleaned
                ):
                    payload.pop(name)
                else:
                    payload[name] = cleaned
            elif name in _COLLECTION_FIELDS:
                payload[name] = [decoded]
    return payload


def tool_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Return a schema that lets constrained decoders choose null for omitted options.

    Some providers materialize every property. Keeping the nullable branch prevents them from
    inventing placeholder IDs such as 0 or 1; the input normalizer then removes those nulls.
    """
    return model.model_json_schema()


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    semantic_null_fields: ClassVar[frozenset[str]] = frozenset()

    @model_validator(mode="before")
    @classmethod
    def normalize_llm_placeholders(cls, value: Any) -> Any:
        return _normalized_tool_payload(cls, value)


class QueryToolInput(ToolInput):
    sql: str = Field(
        description="One SELECT or WITH ... SELECT over the allowlisted ai_* views."
    )

    @field_validator("sql")
    @classmethod
    def validate_sql_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("sql must not be empty")
        return value


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
    ]
    id: PositiveInt | None = None
    values: dict[str, Any] = Field(default_factory=dict)


class CardToolInput(ToolInput):
    semantic_null_fields = frozenset({"parent_id"})

    mode: Literal["create", "edit", "move", "complete", "cancel", "reopen", "link", "unlink"]
    id: PositiveInt | None = None
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
    value_id: PositiveInt | None = None
    value_ids: list[PositiveInt] | None = None
    value_query: str | list[str] | None = Field(
        default=None, description="One or more exact Value names; this is not SQL."
    )
    tag_id: PositiveInt | None = None
    tag_ids: list[PositiveInt] | None = None
    tag_query: str | list[str] | None = Field(
        default=None, description="One or more exact Tag names; this is not SQL."
    )
    check_id: PositiveInt | None = None
    check_ids: list[PositiveInt] | None = None
    check_query: str | list[str] | None = Field(
        default=None, description="One or more exact Check titles; this is not SQL."
    )
    parent_id: PositiveInt | None = Field(
        default=None,
        description=(
            "Parent Card ID. On create, omit this when there is no parent. On edit, send null "
            "to remove the current parent and make the Card root-level."
        ),
    )
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
            if self.parent_id is not None and self.parent_query is not None:
                raise ValueError("use either parent_id or parent_query, not both")
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
            "check_id",
            "check_ids",
            "check_query",
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
            if self.parent_id is not None and self.parent_query is not None:
                raise ValueError("use either parent_id or parent_query, not both")
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
                supplied & {"check_id", "check_ids", "check_query"},
            ]
            selected = [group for group in groups if group]
            if len(selected) != 1:
                raise ValueError(f"Card {self.mode} needs exactly one relationship type")
            allowed = selected[0]
            if supplied - allowed:
                raise ValueError(f"Card {self.mode} mixes unrelated fields")
            if not any(getattr(self, field_name) for field_name in allowed):
                raise ValueError(f"Card {self.mode} needs at least one relationship reference")
        return self


class CheckToolInput(ToolInput):
    mode: Literal["create", "edit", "complete", "cancel"]
    id: PositiveInt | None = None
    title: str | None = None
    repeatable: bool | None = None

    @model_validator(mode="after")
    def validate_target(self) -> CheckToolInput:
        supplied = set(self.model_fields_set) - {"mode", "id"}
        if self.mode == "create":
            if self.id is not None:
                raise ValueError("a new Check must not include an id")
            if not (self.title or "").strip():
                raise ValueError("a new Check needs a title")
            return self
        if self.id is None:
            raise ValueError(f"check mode '{self.mode}' needs an id")
        if self.mode == "edit":
            editable = {"title", "repeatable"}
            if not supplied:
                raise ValueError("an edited Check needs at least one proposed field")
            if unsupported := supplied - editable:
                raise ValueError("Check edit does not accept: " + ", ".join(sorted(unsupported)))
        elif supplied:
            raise ValueError(f"Check {self.mode} does not accept fields")
        return self


class ValueToolInput(ToolInput):
    mode: Literal["create", "edit"]
    id: PositiveInt | None = None
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


class TagToolInput(ToolInput):
    mode: Literal["create", "edit"]
    id: PositiveInt | None = None
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


class RequestToolInput(ToolInput):
    mode: Literal["create", "edit"]
    id: PositiveInt | None = None
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


class RemoveToolInput(ToolInput):
    type: Literal["card", "check", "tag", "value", "request"]
    id: PositiveInt
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
