"""The Card mutation tool. The workspace mutator owns the turn that calls it."""

from __future__ import annotations

from typing import Any

from tg_agent_shell.ai.contracts import CardToolInput
from tg_agent_shell.proposals.api import MutationToolSpec, entity_change


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


# One line each: what this tool owns, because seven of them compete.  Mode semantics live in
# the schema, field rules in the field descriptions, and policy in the subagent's prompt.
CARD_TOOL = MutationToolSpec(
    name="card",
    input_model=CardToolInput,
    description=(
        "Propose one Card — a Goal, an Idea or an Action. Also the only tool that attaches a "
        "Value, a Tag or a Check to a Card."
    ),
    to_change=entity_change("card"),
    repair=_card_repair,
)
