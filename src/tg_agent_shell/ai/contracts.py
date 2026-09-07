from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from types import NoneType, UnionType
from typing import Any, ClassVar, Literal, Union, get_args, get_origin

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PositiveInt,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic.fields import FieldInfo

from ..foundation.screens import ScreenCatalogue


class ChangeAction(StrEnum):
    """What one change does. Every mutation tool's `mode` is one of these, spelled the same.

    The tools declare their own subset, so a tool offers only the actions its entity has.
    """

    CREATE = "create"
    UPDATE = "update"
    MOVE = "move"
    COMPLETE = "complete"
    CANCEL = "cancel"
    REOPEN = "reopen"
    ARCHIVE = "archive"
    DELETE = "delete"
    LINK = "link"
    UNLINK = "unlink"


_NULLISH_STRINGS = frozenset({"null", "none", "nil", "undefined"})


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


def _list_shape(field: FieldInfo) -> tuple[bool, bool]:
    """Whether the field takes a list at all, and whether a list is all it takes.

    A field that takes only a list is one a bare value belongs inside.  One that also takes
    a string is a lookup key, where a bare value is already the whole of it.
    """
    options = [field.annotation]
    if get_origin(field.annotation) in (Union, UnionType):
        options = list(get_args(field.annotation))
    options = [option for option in options if option is not NoneType]
    lists = [option for option in options if get_origin(option) is list]
    return bool(lists), bool(lists) and len(lists) == len(options)


def _normalized_tool_payload(model: type[BaseModel], value: Any) -> Any:
    """Remove harmless LLM placeholders without guessing at real user content.

    Unknown and required fields are deliberately left alone so Pydantic can reject them.
    Explicit empty strings/lists are preserved because they may mean "clear this text/set".
    """
    if not isinstance(value, dict):
        return value

    payload = dict(value)
    semantic_null_fields = getattr(model, "semantic_null_fields", frozenset())
    content_fields = getattr(model, "content_fields", frozenset())
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

        if name not in content_fields and (
            _is_nullish_string(raw_value)
            or (isinstance(raw_value, str) and not raw_value.strip())
        ):
            payload.pop(name)
            continue

        takes_list, only_list = _list_shape(field)
        if not takes_list:
            continue
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
        elif only_list:
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
    # Fields carrying the owner's own text, where "" and "null" are what they wrote rather
    # than a decoder filling a slot. Every other field loses both.
    content_fields: ClassVar[frozenset[str]] = frozenset()
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


QUERY_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "query_data",
        "description": (
            "Read the current data with one read-only SELECT over the ai_* views listed "
            "in your instructions. Use it before you answer or propose anything."
        ),
        "parameters": tool_json_schema(QueryToolInput),
    },
}


class AgentChange(BaseModel):
    """One command intent, before it becomes a proposal."""

    # The entity name is whatever feature owns it; the registry is what rejects an
    # unknown one, so this stays a plain string.
    entity: str
    action: ChangeAction
    id: PositiveInt | None = None
    values: dict[str, Any] = Field(default_factory=dict)


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


class NotClearEnoughInput(ToolInput):
    reason: str = Field(
        description="The one question the user must answer, in their words."
    )


class RouteInput(ToolInput):
    name: str = Field(description="The subagent to hand the turn to, spelled as listed.")


ROUTE_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "route",
        "description": (
            "Hand this turn to a subagent. It reads this same conversation, does the work, "
            "and comes back with a receipt of what it did. You write the message the user "
            "sees. You just pass the name of the subagent."
        ),
        "parameters": tool_json_schema(RouteInput),
    },
}


class CallHelperInput(ToolInput):
    name: str = Field(description="The helper to ask, spelled as the notice gave it.")
    request: str = Field(
        description="Your question in words. Say exactly what to count and over what."
    )


CALL_HELPER_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "call_helper",
        "description": (
            "Ask a helper a question one simple read could not answer. It writes the query "
            "and hands back its result. You keep the turn and you write the answer."
        ),
        "parameters": tool_json_schema(CallHelperInput),
    },
}


class OpenInput(ToolInput):
    """The one item to put on the screen.

    `item_type` carries no enum here: which types exist is the screen catalogue's,
    and `open_tool` fills the enum in from it.
    """

    item_type: str = Field(description="What kind of item it is.")
    id: int = Field(description="Its numeric id.")


def open_tool(screens: ScreenCatalogue) -> dict[str, Any]:
    """`open` as the model reads it, over the item types the features publish.

    A tool's enum is prompt text, and this one is built from the same catalogue the
    call is resolved against, so a feature that publishes a screen is offered by
    name and one that publishes none is spelled out nowhere.
    """
    schema = tool_json_schema(OpenInput)
    schema["properties"]["item_type"]["enum"] = list(screens.types)
    return {
        "type": "function",
        "function": {
            "name": "open",
            "description": (
                "Put one item on the screen, exactly as the user opening it by hand. Call it "
                "only when the user asked to see or open one single item. Otherwise cite the "
                "item in your answer instead."
            ),
            "parameters": schema,
        },
    }


@dataclass(frozen=True, slots=True)
class MutationToolSpec:
    """One mutation tool as the model sees it."""

    name: str
    input_model: type[BaseModel]
    description: str
    to_change: Callable[[BaseModel], AgentChange]
    # Extra repair detail when the model sent arguments this tool could not validate.
    repair: Callable[[dict[str, Any]], dict[str, Any]] | None = None

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": tool_json_schema(self.input_model),
            },
        }

    def change_from(self, arguments: dict[str, Any]) -> AgentChange:
        return self.to_change(self.input_model.model_validate(arguments))
