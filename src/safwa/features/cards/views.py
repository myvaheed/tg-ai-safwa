"""The `ai_*` views over Cards and their history."""

from __future__ import annotations

from tg_agent_shell.ai.sql import Searchable, SqlView

from ...foundation.marks import ARCHIVE_MARKER, LIVE_FORMAT, MARKER_FORMAT, REPEAT_DONE
from .model import CARD_TREE_DEPTH_MAX, TERMINAL_STAGES, CardKind

# What the search index calls a Card.
CARD_ITEM_TYPE = "card"

_TERMINAL_STAGE_SQL = ", ".join(f"'{stage.value}'" for stage in TERMINAL_STAGES)

# The open row of this row's series, already rendered as the marker's tail, so the whole
# marker needs one branch and one subquery instead of one of each per outcome.  A plain
# SELECT is also the shape the read authorizer can attribute: a derived table inside a
# view hides the view's name from it, and every query over that view is then denied.
_LIVE_TAIL = f"""COALESCE((SELECT printf('{LIVE_FORMAT}', p.id) FROM cards p
                  WHERE COALESCE(p.repeat_series_id, p.id)
                        = COALESCE(c.repeat_series_id, c.id)
                    AND p.effective_stage NOT IN ({_TERMINAL_STAGE_SQL})
                    AND p.archived_at IS NULL
                  ORDER BY p.id DESC LIMIT 1), '')"""

_GOAL = CardKind.GOAL.value


def _goal_chain_below(length: int) -> str:
    """True when `length` Goals hang under the row's Card, each a parent of the next."""
    joins = "".join(
        f" JOIN card_parents p{n} ON p{n}.parent_id = g{n - 1}.id"
        f" JOIN cards g{n} ON g{n}.id = p{n}.card_id AND g{n}.kind = '{_GOAL}'"
        for n in range(2, length + 1)
    )
    return (
        f"EXISTS (SELECT 1 FROM card_parents p1"
        f" JOIN cards g1 ON g1.id = p1.card_id AND g1.kind = '{_GOAL}'{joins}"
        f" WHERE p1.parent_id = c.id)"
    )


# A Goal's 🎯 index, the number `tree_level` counts for the screens. A recursive CTE is a
# derived table, which would hide this view's name from the read authorizer, so the chain
# is unrolled to the deepest tree there can be.
_TREE_LEVEL = (
    f"CASE WHEN c.kind = '{_GOAL}' THEN 1"
    + "".join(f" + {_goal_chain_below(length)}" for length in range(1, CARD_TREE_DEPTH_MAX))
    + " ELSE 0 END"
)


# An archived Card is read here like any other: the workspace is what keeps one out of a
# list by stage, and the marks on the title are what say which row this is.
AI_CARDS = SqlView(
    "ai_cards",
    f"""SELECT c.id,
               c.title
                 || CASE WHEN c.repeat_series_id IS NOT NULL AND c.schedule IS NOT NULL
                              AND COALESCE(json_extract(s.rule, '$.timing.schedule_kind'), '') != 'once'
                              AND c.effective_stage IN ({_TERMINAL_STAGE_SQL})
                         THEN printf('{MARKER_FORMAT}', '{REPEAT_DONE}',
                              (SELECT count(*) FROM cards p
                               WHERE COALESCE(p.repeat_series_id, p.id)
                                     = COALESCE(c.repeat_series_id, c.id)
                                 AND p.id <= c.id),
                              {_LIVE_TAIL})
                         ELSE '' END
                 || CASE WHEN c.archived_at IS NULL THEN '' ELSE '{ARCHIVE_MARKER}' END AS title,
               c.note, c.kind, c.effective_stage AS stage, c.priority,
               c.schedule,
               NULLIF(c.blocked_description, '') AS blocked_description,
               CASE WHEN (SELECT effort_tracking FROM user_profile WHERE id=1)
                    THEN c.effort_points END AS effort_points,
               c.tracked_mins,
               (SELECT group_concat(cp.parent_id, ',') FROM card_parents cp
                WHERE cp.card_id=c.id) AS parent_ids,
               {_TREE_LEVEL} AS tree_level,
               COALESCE(c.repeat_series_id, c.id) AS series_id,
               (SELECT group_concat(cc.category, ',') FROM card_categories cc
                WHERE cc.card_id=c.id) AS categories,
               (SELECT group_concat(ce.energy_type, ',') FROM card_energy_types ce
                WHERE ce.card_id=c.id) AS energy_types,
               (SELECT group_concat(v.name, ',' ORDER BY v."order") FROM card_values cv
                JOIN "values" v ON v.id=cv.value_id WHERE cv.card_id=c.id) AS direct_values,
               (SELECT group_concat(t.name, ',') FROM card_tags ct
                JOIN tags t ON t.id=ct.tag_id WHERE ct.card_id=c.id) AS direct_tags,
               c.created_at, c.updated_at, relevance('card', c.id) AS relevance
        FROM cards c LEFT JOIN schedules s ON s.id=c.schedule_id""",
    doc="""- `ai_cards(id, title, note, kind, stage, priority, schedule, blocked_description, effort_points, tracked_mins, parent_ids, tree_level, series_id, categories, energy_types, direct_values, direct_tags, created_at, updated_at, relevance)`
   - `kind` goal | action | thought; a goal with `parent_ids` is displayed as a Subgoal
  - `parent_ids` are the comma-joined ids of the goals a card stands under, NULL at the root; match one with `',' || parent_ids || ',' LIKE '%,<id>,%'`
  - `tree_level` counts up from the bottom: an action is 0; a goal is 1 + the highest `tree_level` of its child goals, 1 with none. Goal → subgoal → action reads 2, 1, 0
   - `stage` backlog | sprint | today | done; NULL on a thought
   - `priority` critical | medium | low; NULL on a thought
  - `effort_points` 0.5 | 1 | 2 | 3 | 5 | 8 | 13, cost of one execution; NULL when unestimated or Effort Points are off
  - `tracked_mins` is the minutes the user spent on an action, NULL when not recorded
  - on a goal or a subgoal, `stage`, `effort_points` and `tracked_mins` are what the cards under it add up to, each action counted once
  - to total effort or time, add `WHERE kind = 'action'`
  - open Card rows are not planned execution counts; use `ai_current_sprint_metrics` for Sprint load and `get_scheduled` for calendar quantities
  - `categories` growth | people | work | chores | rest
  - `energy_types` physical | cognitive | emotional | spiritual
  - `schedule` is the original timing text, not computed dates or counts; on a goal or a subgoal it is the deadline
  - `blocked_description` NULL = unblocked; non-NULL = blocked; filter with `IS NOT NULL`
  - `categories`, `energy_types`, `direct_values` and `direct_tags` are comma-joined names, so match one with `LIKE '%Health%'`
  - `series_id` is the whole repeat series of one card; a card that never repeated is its own series
  - the checks on a card are `ai_checks WHERE card_id = <id>`""",
    searchable=Searchable(CARD_ITEM_TYPE, "cards", ("title", "note", "blocked_description")),
)


VIEWS = (AI_CARDS,)
