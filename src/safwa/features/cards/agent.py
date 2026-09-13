"""The Card mutation tool. The workspace mutator owns the turn that calls it."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, PositiveInt, field_validator, model_validator

from tg_agent_shell.ai.autoapproval import RELATIONSHIP_LINK, SCALAR_UPDATE, AutoApprovalRule
from tg_agent_shell.ai.contracts import ToolInput
from tg_agent_shell.proposals.api import MutationToolSpec, entity_change


class CardToolInput(ToolInput):
    content_fields = frozenset({"title", "note", "blocked_description", "hard_time_description"})
    semantic_null_fields = frozenset({"parent_id", "hard_time"})

    mode: Literal["create", "update", "move", "complete", "reopen", "link", "unlink"] = Field(
        description=(
            "move changes only the stage; update changes every other field. complete is how a "
            "Card reaches Done, and reopen brings it back. link and unlink attach one "
            "relationship type. Archiving is the remove tool."
        )
    )
    id: PositiveInt | None = None
    kind: Literal["goal", "subgoal", "action"] | None = None
    title: str | None = None
    note: str | None = None
    stage: Literal["backlog", "sprint", "today", "done"] | None = None
    priority: Literal["critical", "medium", "low"] | None = None
    hard_time: str | None = Field(
        default=None,
        description=(
            "When the Card must happen, in plain words: 'Tuesday at 15:00', 'every weekday "
            "at 09:00'. Omit it when nothing fixes the time. On update, send null to remove it."
        ),
    )
    hard_time_description: str | None = Field(
        default=None,
        description="What fixes the time, in a few words: 'the clinic closes at 18:00'.",
    )
    blocked: bool | None = None
    blocked_description: str | None = None
    effort_points: Literal[0.5, 1, 2, 3, 5, 8, 13] | None = Field(
        default=None,
        description=(
            "How much the whole Action takes in the user's usual state. "
            "0.5 done in passing. 1 the day goes on as it was. 2 a little tired, no rest needed. "
            "3 carry on only after a break. 5 after a full rest, one more serious thing. "
            "8 only light work left today. 13 nothing else today. "
            "On a repeating Action this is one occurrence, not the series. "
            "Work that does not fit one day is a Subgoal with Actions under it, never a 13."
        ),
    )
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

    @field_validator("hard_time", mode="before")
    @classmethod
    def a_flag_is_no_time(cls, value: Any) -> Any:
        # A slot-filling model sends `false` where it means nothing at all.
        return None if value is False else value

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
            "hard_time_description",
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
            if self.stage == "done":
                raise ValueError("use complete mode to finish a Card")
            if self.blocked and not (self.blocked_description or "").strip():
                raise ValueError("a blocked Card needs blocked_description")
            if self.parent_id is not None and self.parent_query is not None:
                raise ValueError("use either parent_id or parent_query, not both")
        elif self.mode == "move":
            if supplied != {"stage"} or self.stage is None:
                raise ValueError("Card move needs only a stage")
            if self.stage == "done":
                raise ValueError("use complete mode to finish a Card")
        elif self.mode == "complete":
            if supplied:
                raise ValueError("Card complete does not accept fields")
        elif self.mode == "reopen":
            if supplied - {"stage"}:
                raise ValueError("Card reopen accepts only an optional stage")
            if self.stage == "done":
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


def _has_explicit_tool_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip()) and value.strip().casefold() not in {
            "null",
            "none",
            "nil",
            "undefined",
        }
    if isinstance(value, list):
        return bool(value)
    return True


def _card_repair(arguments: dict[str, Any]) -> dict[str, Any]:
    """A compact valid `card(mode="create")` shape, built from what the model already sent."""
    if arguments.get("mode") != "create":
        return {}

    expected: dict[str, Any] = {"mode": "create"}
    core_fields = (
        "kind",
        "title",
        "note",
        "stage",
        "priority",
        "hard_time",
        "hard_time_description",
        "blocked",
        "blocked_description",
        "effort_points",
        "repeatable",
        "categories",
        "energy_types",
    )
    for field_name in core_fields:
        if field_name in arguments and _has_explicit_tool_value(arguments[field_name]):
            expected[field_name] = arguments[field_name]

    # Keep intentional, non-placeholder relationship forms. Singular IDs are omitted from the
    # repair example because constrained decoders commonly invent the minimum allowed integer.
    for field_name in (
        "value_ids",
        "value_query",
        "tag_ids",
        "tag_query",
        "check_ids",
        "check_query",
        "parent_id",
        "parent_query",
    ):
        if field_name in arguments and _has_explicit_tool_value(arguments[field_name]):
            expected[field_name] = arguments[field_name]

    return {
        "expected_arguments": expected,
        "argument_rules": [
            "For mode='create', omit id; it is assigned after Save.",
            "Omit unused relationship properties; never fill *_id with placeholder 0 or 1.",
            "Send only relationships that the user actually requested or that were resolved from data.",
        ],
    }


CARD_AUTOAPPROVALS = {
    "link": AutoApprovalRule(
        RELATIONSHIP_LINK,
        frozenset(
            {
                "value_id",
                "value_ids",
                "value_query",
                "tag_id",
                "tag_ids",
                "tag_query",
                "check_id",
                "check_ids",
                "check_query",
            }
        ),
    ),
    "update": AutoApprovalRule(
        SCALAR_UPDATE,
        frozenset(
            {
                "title",
                "note",
                "priority",
                "hard_time",
                "hard_time_description",
                "blocked",
                "blocked_description",
                "effort_points",
                "repeatable",
            }
        ),
    ),
}


# One line each: what this tool owns, because seven of them compete.  Mode semantics live in
# the schema, field rules in the field descriptions, and policy in the subagent's prompt.
CARD_TOOL = MutationToolSpec(
    name="card",
    input_model=CardToolInput,
    description=(
        "Propose one Card — a Goal, a Subgoal or an Action. Also the only tool that "
        "attaches a Value, a Tag or a Check to a Card."
    ),
    to_change=entity_change("card"),
    repair=_card_repair,
)
