from __future__ import annotations

import json
from datetime import date as calendar_date
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

        if name in semantic_null_fields and mode == "update" and (
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
    """One command intent. Every mutation tool's `mode` is one of these actions, spelled the same."""

    entity: Literal["card", "check", "tag", "value", "request", "reminder", "diary"]
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

    mode: Literal["create", "update", "move", "complete", "cancel", "reopen", "link", "unlink"]
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
            "Parent Card ID. On create, omit this when there is no parent. On update, send null "
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
        if self.mode == "update":
            if not supplied:
                raise ValueError("an updated Card needs at least one proposed field")
            if unsupported := supplied - editable:
                raise ValueError("Card update does not accept: " + ", ".join(sorted(unsupported)))
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
    mode: Literal["create", "update", "complete", "cancel"]
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
        if self.mode == "update":
            editable = {"title", "repeatable"}
            if not supplied:
                raise ValueError("an updated Check needs at least one proposed field")
            if unsupported := supplied - editable:
                raise ValueError("Check update does not accept: " + ", ".join(sorted(unsupported)))
        elif supplied:
            raise ValueError(f"Check {self.mode} does not accept fields")
        return self


class RecordToolInput(ToolInput):
    """A record with no lifecycle: create it, or update the fields named in the call."""

    create_requires: ClassVar[tuple[str, ...]] = ()

    mode: Literal["create", "update"]
    id: PositiveInt | None = None

    @model_validator(mode="after")
    def validate_target(self) -> RecordToolInput:
        label = type(self).__name__.removesuffix("ToolInput")
        if self.mode == "create":
            if missing := [
                name for name in self.create_requires if not str(getattr(self, name) or "").strip()
            ]:
                raise ValueError(f"a new {label} needs " + " and ".join(missing))
        elif self.id is None:
            raise ValueError(f"an updated {label} needs an id")
        elif not (self.model_fields_set - {"mode", "id"}):
            raise ValueError(f"an updated {label} needs at least one proposed field")
        return self


class ValueToolInput(RecordToolInput):
    create_requires = ("name",)

    name: str | None = None
    description: str | None = None
    active: bool | None = None


class TagToolInput(RecordToolInput):
    create_requires = ("name",)

    name: str | None = None
    description: str | None = None


class RequestToolInput(RecordToolInput):
    create_requires = ("name", "sql")

    name: str | None = None
    description: str | None = None
    sql: str | None = Field(
        default=None,
        description=(
            "One read-only SELECT or WITH ... SELECT over ai_* views. It must query ai_cards "
            "and return a column named id; for example: SELECT id FROM ai_cards WHERE kind = 'action'."
        ),
    )


class ReminderToolInput(RecordToolInput):
    create_requires = ("when",)

    instruction: str = Field(
        description=(
            "What Safwa should do when the time comes, handed to the advisor as a request. "
            "The bounded conversation is available then, but the instruction should remain clear "
            "after time has passed and must name every Safwa item it concerns by #id."
        )
    )
    when: str | None = Field(
        default=None,
        description=(
            "The timing in plain words, e.g. 'every weekday at 8am' or 'in 90 minutes'. "
            "Required to create. Omit it on update to leave the schedule untouched."
        ),
    )


class RemoveToolInput(ToolInput):
    mode: Literal["archive", "delete"] = Field(
        default="archive",
        description="archive hides it and keeps its history; delete erases it, and only a Card allows it.",
    )
    entity: Literal["card", "check", "tag", "value", "request", "reminder"]
    id: PositiveInt

    @model_validator(mode="after")
    def validate_target(self) -> RemoveToolInput:
        if self.mode == "delete" and self.entity != "card":
            raise ValueError(f"a {self.entity} is archived, never deleted")
        return self


class ReminderConfigInput(ToolInput):
    """The setup session's terminal call: free text resolved into parameters.

    `schedule_kind` is derived from which of these are present, so the model cannot name a
    shape that contradicts its own parameters.
    """

    days: list[Literal["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]] | None = Field(
        default=None, description="Weekdays to fire on; all seven means every day."
    )
    time: str | None = Field(
        default=None,
        description=(
            "Local wall clock HH:MM. With days it is the time it fires at; otherwise it is "
            "when the schedule starts."
        ),
    )
    date: str | None = Field(
        default=None,
        description=(
            "Local calendar date dd.mm.yyyy. Always the START date when the schedule "
            "repeats, and the date itself when it does not. Needs time as well."
        ),
    )
    interval_minutes: PositiveInt | None = Field(
        default=None, description="Repeat every N minutes."
    )
    quiet_windows: list[str] | None = Field(
        default=None,
        description=(
            "Local HH:MM-HH:MM ranges when it must not fire, e.g. ['22:00-09:00']. "
            "Interval schedules only. The end is exclusive."
        ),
    )


class NotClearEnoughInput(ToolInput):
    reason: str = Field(
        description="The one question the owner must answer, in their words."
    )


class RouteInput(ToolInput):
    name: str = Field(description="The subagent to hand the turn to, spelled as listed.")


class DiaryToolInput(ToolInput):
    """One day of the Diary: written in the owner's voice, or removed."""

    mode: Literal["update", "delete"] = Field(
        description="update writes that day, replacing what is saved; delete removes it."
    )
    date: str = Field(description="The day this settles, as YYYY-MM-DD.")
    pov: str | None = Field(
        default=None,
        description="With update: that whole day in the owner's voice. It replaces the saved entry.",
    )
    ai_comment: str | None = Field(
        default=None,
        description="With update: one sentence of your own about the day, addressed to the owner.",
    )
    feeling_score: int | None = Field(
        default=None,
        ge=0,
        le=10,
        description="With update: how the day felt, 0-10. Omit it when the day is silent.",
    )

    @field_validator("date")
    @classmethod
    def validate_calendar_date(cls, value: str) -> str:
        try:
            return calendar_date.fromisoformat(value.strip()).isoformat()
        except ValueError as error:
            raise ValueError("date must be a calendar date written as YYYY-MM-DD") from error

    @model_validator(mode="after")
    def entry_needs_its_text(self) -> DiaryToolInput:
        if self.mode == "update" and not (self.pov or "").strip():
            raise ValueError("pov is the day itself and is required to write one")
        if self.mode == "delete" and (self.pov or self.feeling_score is not None):
            raise ValueError("A deletion carries only mode and date")
        return self


MUTATION_TOOL_MODELS: dict[str, type[BaseModel]] = {
    "card": CardToolInput,
    "check": CheckToolInput,
    "value": ValueToolInput,
    "tag": TagToolInput,
    "request": RequestToolInput,
    "reminder": ReminderToolInput,
    "remove": RemoveToolInput,
    "diary": DiaryToolInput,
}


def mutation_change_from_tool(name: str, arguments: dict[str, Any]) -> AgentChange:
    """Validate a model tool call and convert it into an application command intent.

    `mode` is the action under its own name, and the tool names the entity — except
    `remove`, which archives or deletes whichever entity it is given.
    """
    model = MUTATION_TOOL_MODELS.get(name)
    if model is None:
        raise ValueError(f"Unknown mutation tool: {name}")
    call = model.model_validate(arguments)
    if name == "remove":
        return AgentChange(entity=call.entity, action=call.mode, id=call.id)
    values = call.model_dump(exclude_unset=True)
    values.pop("mode", None)
    # A Diary day carries no id: it targets its date, and preparation reads whether
    # that day exists yet, settling `update` on create or update.
    return AgentChange(
        entity=name, action=call.mode, id=values.pop("id", None), values=values
    )
