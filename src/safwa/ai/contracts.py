from __future__ import annotations

import json
from enum import StrEnum
from typing import Any, ClassVar, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PositiveInt,
    ValidationError,
    field_validator,
    model_validator,
)

from ..features.proposals.model import ChangeAction

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


def _without_titles(node: Any) -> Any:
    """Drop Pydantic's `title` labels, which repeat the property name they sit under.

    A property really called `title` is a dict, and the label is always a string, so the
    two are told apart by what the value is rather than by where it sits.
    """
    if isinstance(node, list):
        return [_without_titles(item) for item in node]
    if not isinstance(node, dict):
        return node
    return {
        key: _without_titles(value)
        for key, value in node.items()
        if not (key == "title" and isinstance(value, str))
    }


def tool_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Return a schema that lets constrained decoders choose null for omitted options.

    Some providers materialize every property. Keeping the nullable branch prevents them from
    inventing placeholder IDs such as 0 or 1; the input normalizer then removes those nulls.
    A `title` carries no such reason: it is the property name written a second way, so it goes.
    """
    return _without_titles(model.model_json_schema())


def validation_error_summary(error: ValidationError) -> str:
    """One line per rejected field, for a model that cannot read a validator traceback."""
    messages: list[str] = []
    for issue in error.errors(include_url=False, include_input=False):
        location = ".".join(str(item) for item in issue.get("loc", ()))
        message = str(issue.get("msg", "Invalid value"))
        messages.append(f"{location}: {message}" if location else message)
    return "; ".join(messages) or "Invalid tool arguments"


class ToolResultStatus(StrEnum):
    """How a tool call ended before any screen. A queued one ends as a `BatchDecision`."""

    OK = "ok"
    ERROR = "error"
    # Prepared and waiting: the owner has the screen, and the result comes with the answer.
    PREPARED = "prepared"


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
    """One command intent, before it becomes a proposal."""

    # The entity name is whatever feature owns it; the registry is what rejects an
    # unknown one, so this stays a plain string.
    entity: str
    action: ChangeAction
    id: PositiveInt | None = None
    values: dict[str, Any] = Field(default_factory=dict)


class CardToolInput(ToolInput):
    semantic_null_fields = frozenset({"parent_id"})

    mode: Literal["create", "update", "move", "complete", "cancel", "reopen", "link", "unlink"] = (
        Field(
            description=(
                "move changes only the stage; update changes every other field. complete and "
                "cancel are how a Card reaches Done and Cancelled, and reopen brings it back. "
                "link and unlink attach one relationship type. Archiving is the remove tool."
            )
        )
    )
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
        description="The one question the user must answer, in their words."
    )


class RouteInput(ToolInput):
    name: str = Field(description="The subagent to hand the turn to, spelled as listed.")


class CallHelperInput(ToolInput):
    name: str = Field(description="The helper to ask, spelled as the notice gave it.")
    request: str = Field(
        description="Your question in words. Say exactly what to count and over what."
    )


class OpenInput(ToolInput):
    """The one item to put on the screen."""

    item_type: Literal["card", "check", "tag", "value", "request", "diary"] = Field(
        description="What kind of item it is."
    )
    id: int = Field(description="Its numeric id.")
