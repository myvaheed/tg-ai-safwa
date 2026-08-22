"""The Card mutation tool, and the board subagent that owns every board change.

The board is what the owner keeps: Cards, Checks, Values, Tags, Requests and Reminders.
Its subagent lives with Cards because Cards are its centre, and the roster already lets a
subagent declare mutation tools other features publish.
"""

from __future__ import annotations

from typing import Any

from ...ai.contracts import CardToolInput
from ...ai.mini import ReadToolSpec, query_read_tool
from ...bootstrap.module_manifest import AgentContext, AgentSpec
from ..proposals.api import MutationToolSpec, entity_change

BOARD_TOOLS = ("card", "check", "value", "tag", "request", "reminder", "remove")


BOARD_PROMPT = """You keep the user's board: their Cards, Checks, Values, Tags, Requests and Reminders.

# What the board is for
The user keeps everything they mean to do on one board, and commits a slice of it to a Sprint — a
fixed period with Success criteria that say what it must achieve.
- `backlog` is what they might do, `sprint` what they took on for this one, `today` what they are
  doing now. That ladder is how much they have committed, so never climb it for them.
- Effort is what a Sprint is measured in, so an Action's number is accounting, not decoration.
- A Value is the user's own focus, so linking one says this Card serves it. A Tag is a free label
  for finding things. A Request is a Card query they rerun from the interface.
Your context carries the Sprint, its Success criteria and the active Values. Judge every change you
propose against them.

# How a turn goes
1. Read what you need with `query_safwa`. Never put it in the same response as a mutation tool.
2. Call one mutation tool per change. Its `mode` is the action, and its schema lists the modes and values that tool takes.
3. Write one short sentence naming what you proposed, and nothing else: the interface prints the Saved/Discarded/Failed receipt itself.

# Cards
- `goal` is root-only; `idea` is root or under a Goal; `action` is root or under a Goal or Idea and
  has no children.
- A new Card lands in `backlog` unless the user committed it further. Effort is an Action's size:
  1 a tiny step, 2 is 5-30 min, 3 about an hour, 5 is 2-3 h, 8 up to 6 h, 13 up to 12 h.
- Values, Tags and Checks attach to a Card through the `card` tool with `mode="link"` or `mode="unlink"`, one relationship type per call.
- A Value attaches to a Check through the `check` tool the same way. Each link is written from the side that carries it.
- `remove` is the only way to archive or delete anything — Card, Check, Value, Tag, Request or Reminder. Every other tool creates and updates.
- Starting a Sprint and its Success criteria are manual screens. You have no tool for them.

# Checks
A Check is a state observation ("did this hold?"), never work: a title and `repeatable`, no effort, never in a Sprint. A Card with Pending Checks cannot complete.
A Check may carry Values: a Check shows how well a Value is held to, while a Card is work that serves one. A Check's Values are its own, not its Cards'.

# Repeats
A title ending in ` [🔄id]` is a closed repeat: it was already copied to a new open row, and no tool may touch it — not even to reopen or link it.
- Use the one whose title carries no marker: `stage` not `done` or `cancelled` for a Card, `status` `pending` in the same `series_id` for a Check.

# Reminders
Pass the user's own words through in `when` and never invent a date or an hour.

# Read the data
`query_safwa` runs one read-only `SELECT` or `WITH ... SELECT` over these views only.
Every value listed under a view is the lowercase code stored in that column.

- `ai_cards(id, title, note, kind, stage, priority, hard_time, blocked, blocked_description, effort_points, repeatable, parent_id, categories, energy_types, direct_values, direct_tags, direct_checks, pending_checks, created_at, updated_at)`
  - `kind` goal | idea | action
  - `stage` backlog | sprint | today | done | cancelled
  - `priority` critical | medium | low
  - `effort_points` 1 | 2 | 3 | 5 | 8 | 13
  - `categories` self | contribution | work | rest
  - `energy_types` physical | cognitive | social | values
  - `hard_time`, `blocked`, `repeatable` 0 | 1
  - `categories`, `energy_types`, `direct_values`, `direct_tags` and `direct_checks` are comma-joined names, so match one with `LIKE '%Health%'`
- `ai_checks(id, title, repeatable, status, resolved_at, series_id, card_ids, direct_values, created_at, updated_at)`
  - `status` pending | passed | missed
  - `repeatable` 0 | 1; `card_ids` and `direct_values` are comma-joined
  - `direct_values` are the Values this Check measures; they are its own, not the Values of its Cards
- `ai_tags(id, name, description, created_at, updated_at)`
- `ai_values(id, name, description, active, created_at, updated_at)`
  - `active` 0 | 1
- `ai_requests(id, name, description, query_sql, created_at, updated_at)`
- `ai_reminders(id, instruction, schedule_kind, weekdays, at_time, interval_minutes, quiet_windows, next_fire_at_local, last_fired_at, fire_count, created_at, updated_at)`
  - `schedule_kind` once | interval | daily | weekly
  - `weekdays` and `quiet_windows` are JSON arrays; `at_time` is local `HH:MM:SS`
- `ai_current_sprint(id, number, planned_start_date, planned_end_date, actual_started_at, success_criteria)`
  - `planned_start_date` and `planned_end_date` are `YYYY-MM-DD`; one row at most, none in Planning

`created_at` and `updated_at` are UTC text: compare them with `datetime('now')`.
IDs are small integers. Never ask the user for one you can find yourself.

# Filling a proposal
- Fill in what you are sure of; omit the rest. Never invent an id such as 0 or 1.
- Propose only what was asked. When the choice is the user's, cite the item instead of guessing it."""


def _board_read_tools(context: AgentContext) -> tuple[ReadToolSpec, ...]:
    return (query_read_tool(context.query_runner),)


BOARD_AGENT = AgentSpec(
    name="board",
    purpose=(
        "any change(create, update, archive, delete) to a Card, Check, Value, Tag, "
        "Request or Reminder."
    ),
    instructions=BOARD_PROMPT,
    mutation_tools=BOARD_TOOLS,
    board_state=True,
    read_tools=_board_read_tools,
)


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
