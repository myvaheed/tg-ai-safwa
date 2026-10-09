"""The Card mutation tools, `goal` and `action`. The workspace mutator owns the turn that calls them."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, Field, PositiveInt, model_validator

from tg_agent_shell.ai.autoapproval import RELATIONSHIP_LINK, SCALAR_UPDATE, AutoApprovalRule
from tg_agent_shell.ai.contracts import AgentChange, Reference, ToolInput
from tg_agent_shell.proposals.api import MutationToolSpec, entity_change

from .model import (
    CATEGORY_MEANINGS,
    ENERGY_MEANINGS,
    TRACKED_MINS_MAX,
    CardKind,
)

LINK_FIELDS = frozenset({"values", "tags", "checks"})

VALUES_DESCRIPTION = "Each an exact Value name or an id."
TAGS_DESCRIPTION = "Each an exact Tag name or an id."
CHECKS_DESCRIPTION = (
    "Each an exact Check title or an id. A Check with its own Schedule cannot be linked. "
    "A Check on another Card: unlink it there first."
)
LINK_MODES = (
    "link and unlink take `values`, `tags` or `checks`, one per call. "
    "Deleting is the remove tool."
)
REPEAT_INSTANCE = "For a finished repeat ` [✅2, 🔄#7]`, send it with that instance's id."


def _validate_call(
    call: GoalToolInput | ActionToolInput, *, label: str, only: dict[str, set[str]]
) -> None:
    """What every mode of a Card tool needs, with the fields `only` limits each mode to."""
    supplied = set(call.model_fields_set) - {"mode", "id"}
    if call.mode == "create":
        if call.id is not None:
            raise ValueError(f"a new {label} must not include an id")
        if not (call.title or "").strip():
            raise ValueError(f"a new {label} needs a title")
        return
    if call.id is None:
        raise ValueError(f"{label} mode '{call.mode}' needs an id")
    if call.mode == "update" and not supplied:
        raise ValueError(f"an updated {label} needs at least one proposed field")
    if call.mode in {"link", "unlink"}:
        linked = supplied & LINK_FIELDS
        if len(linked) != 1 or supplied - LINK_FIELDS or not getattr(call, linked.pop()):
            raise ValueError(f"{label} {call.mode} needs exactly one of values, tags or checks")
        return
    if call.mode in only and (unsupported := supplied - only[call.mode]):
        raise ValueError(
            f"{label} {call.mode} does not accept: " + ", ".join(sorted(unsupported))
        )


class GoalToolInput(ToolInput):
    content_fields = frozenset({"title", "note", "deadline"})
    semantic_null_fields = frozenset({"deadline", "parent"})

    mode: Literal["create", "update", "complete", "reopen", "link", "unlink"] = Field(
        description=(
            "complete only when the user asks and all its Actions are Done. " + LINK_MODES
        )
    )
    id: PositiveInt | None = None
    title: str | None = None
    note: str | None = None
    priority: Literal["critical", "medium", "low"] | None = None
    deadline: str | None = Field(
        default=None,
        description=(
            "When it must be done, in the user's words: 'by 20 October'. Never invent a date. "
            "On update, null removes it."
        ),
    )
    values: Reference | list[Reference] | None = Field(default=None, description=VALUES_DESCRIPTION)
    tags: Reference | list[Reference] | None = Field(default=None, description=TAGS_DESCRIPTION)
    checks: Reference | list[Reference] | None = Field(
        default=None, description=CHECKS_DESCRIPTION
    )
    parent: Reference | None = Field(
        default=None,
        description="A Goal, by exact title or id. On update, null makes it root-level.",
    )

    @model_validator(mode="after")
    def validate_target(self) -> GoalToolInput:
        _validate_call(self, label="Goal", only={"complete": set(), "reopen": set()})
        return self


class ActionToolInput(ToolInput):
    content_fields = frozenset({"title", "note", "blocked_description", "schedule"})
    semantic_null_fields = frozenset(
        {"parent", "schedule", "tracked_mins", "effort_points", "blocked_description"}
    )

    mode: Literal["create", "update", "move", "complete", "reopen", "link", "unlink"] = Field(
        description=(
            "move changes only `stage`. complete finishes it, with `tracked_mins` when the user "
            "said how long it took; its Goal stays open. reopen brings it back to `stage`, "
            "Backlog when omitted, and reopens its closed Goal and Subgoal. " + LINK_MODES
        )
    )
    id: PositiveInt | None = None
    title: str | None = None
    note: str | None = None
    stage: Literal["backlog", "sprint", "today"] | None = None
    priority: Literal["critical", "medium", "low"] | None = None
    schedule: str | None = Field(
        default=None,
        description=(
            "When it repeats or happens, in the user's words: 'once a week', "
            "'three times a day', 'Tuesday at 15:00', 'after each completion'. "
            "Never invent a time. On update, null removes it."
        ),
    )
    blocked_description: str | None = Field(
        default=None,
        description="What blocks it, in the user's words. On update, null unblocks it.",
    )
    effort_points: Literal[0.5, 1, 2, 3, 5, 8, 13] | None = Field(
        default=None,
        description=(
            "What one execution of the Action costs the user in their usual state. "
            "On update, null removes it. "
            "0.5 done in passing. 1 the day goes on as it was. 2 a little tired, no rest needed. "
            "3 carry on only after a break. 5 after a full rest, one more serious thing. "
            "8 only light work left today. 13 nothing else today. "
            "Work that does not fit one day is a Subgoal with Actions under it, never a 13. "
            + REPEAT_INSTANCE
        ),
    )
    tracked_mins: int | None = Field(
        default=None,
        ge=1,
        le=TRACKED_MINS_MAX,
        description=(
            "Minutes the user says the Action took, in total: 1.5 hours is 90. Only what the "
            "user said, never an estimate. On update, null removes it. " + REPEAT_INSTANCE
        ),
    )
    categories: list[Literal["growth", "people", "work", "chores", "rest"]] | None = Field(
        default=None,
        description="What the Action gives. "
        + " ".join(f"{name}: {meaning}." for name, meaning in CATEGORY_MEANINGS.items()),
    )
    energy_types: list[Literal["physical", "cognitive", "emotional", "spiritual"]] | None = Field(
        default=None,
        description="What the Action costs the user. "
        + " ".join(f"{name}: {meaning}." for name, meaning in ENERGY_MEANINGS.items()),
    )
    values: Reference | list[Reference] | None = Field(default=None, description=VALUES_DESCRIPTION)
    tags: Reference | list[Reference] | None = Field(default=None, description=TAGS_DESCRIPTION)
    checks: Reference | list[Reference] | None = Field(
        default=None, description=CHECKS_DESCRIPTION
    )
    parent: Reference | None = Field(
        default=None,
        description="A Goal, by exact title or id. On update, null makes it root-level.",
    )

    @model_validator(mode="after")
    def validate_target(self) -> ActionToolInput:
        if self.mode == "create" and "tracked_mins" in self.model_fields_set:
            raise ValueError("a new Action has no time spent yet; omit tracked_mins")
        _validate_call(
            self,
            label="Action",
            only={"move": {"stage"}, "complete": {"tracked_mins"}, "reopen": {"stage"}},
        )
        if self.mode == "move" and self.stage is None:
            raise ValueError("Action move needs a stage")
        return self


def _card_change(kind: CardKind) -> Callable[[BaseModel], AgentChange]:
    """The ordinary conversion, plus the kind of Card the call addresses.

    Preparation refuses a call on a Card of another kind before anything reaches review.
    """
    convert = entity_change("card")

    def to_change(call: BaseModel) -> AgentChange:
        change = convert(call)
        values = dict(change.values)
        # A Goal's Schedule is its Deadline.
        if "deadline" in values:
            values["schedule"] = values.pop("deadline")
        values["kind"] = kind.value
        return change.model_copy(update={"values": values})

    return to_change


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


def _repair(model: type[ToolInput]) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """A compact valid `mode="create"` shape, built from what the model already sent."""

    def repair(arguments: dict[str, Any]) -> dict[str, Any]:
        if arguments.get("mode") != "create":
            return {}
        expected: dict[str, Any] = {"mode": "create"}
        for field_name in model.model_fields:
            if field_name in {"mode", "id", "tracked_mins"}:
                continue
            if field_name in arguments and _has_explicit_tool_value(arguments[field_name]):
                expected[field_name] = arguments[field_name]
        return {
            "expected_arguments": expected,
            "argument_rules": [
                "For mode='create', omit id; it is assigned after Save.",
                "Omit unused fields; never fill an id with placeholder 0 or 1.",
                "Send only relationships that the user actually requested or that were resolved from data.",
            ],
        }

    return repair


CARD_AUTOAPPROVALS = {
    "link": AutoApprovalRule(RELATIONSHIP_LINK, LINK_FIELDS),
    "update": AutoApprovalRule(
        SCALAR_UPDATE,
        frozenset(
            {
                "title",
                "note",
                "priority",
                "schedule",
                # What preparation read `schedule` as; it travels with it.
                "schedule_rule",
                "blocked_description",
                "effort_points",
                "tracked_mins",
            }
        ),
    ),
}


# One line each: what this tool owns, because seven of them compete.  Mode semantics live in
# the schema, field rules in the field descriptions, and policy in the subagent's prompt.
GOAL_TOOL = MutationToolSpec(
    name="goal",
    input_model=GoalToolInput,
    description=(
        "Propose one Goal as defined under Safwa items. "
        "A Goal with a parent is displayed as a Subgoal."
    ),
    to_change=_card_change(CardKind.GOAL),
    repair=_repair(GoalToolInput),
)
ACTION_TOOL = MutationToolSpec(
    name="action",
    input_model=ActionToolInput,
    description="Propose one Action: work that fits in one day.",
    to_change=_card_change(CardKind.ACTION),
    repair=_repair(ActionToolInput),
)
