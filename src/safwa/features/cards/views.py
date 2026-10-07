"""The `ai_*` views over Cards and their history."""

from __future__ import annotations

from tg_agent_shell.ai.sql import Searchable, SqlView

from ...foundation.marks import ARCHIVE_MARKER, LIVE_FORMAT, MARKER_FORMAT
from .model import TERMINAL_STAGES

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


# An archived Card is read here like any other: the workspace is what keeps one out of a
# list by stage, and the marks on the title are what say which row this is.
AI_CARDS = SqlView(
    "ai_cards",
    f"""SELECT c.id,
               c.title
                 || CASE WHEN c.repeat_series_id IS NOT NULL AND c.schedule IS NOT NULL
                              AND COALESCE(json_extract(s.rule, '$.timing.schedule_kind'), '') != 'once'
                              AND c.effective_stage IN ({_TERMINAL_STAGE_SQL})
                         THEN printf('{MARKER_FORMAT}',
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
               c.tracked_mins, c.parent_id,
               COALESCE(c.repeat_series_id, c.id) AS series_id,
               (SELECT group_concat(cc.category, ',') FROM card_categories cc
                WHERE cc.card_id=c.id) AS categories,
               (SELECT group_concat(ce.energy_type, ',') FROM card_energy_types ce
                WHERE ce.card_id=c.id) AS energy_types,
               (SELECT group_concat(v.name, ',') FROM card_values cv
                JOIN "values" v ON v.id=cv.value_id WHERE cv.card_id=c.id) AS direct_values,
               (SELECT group_concat(t.name, ',') FROM card_tags ct
                JOIN tags t ON t.id=ct.tag_id WHERE ct.card_id=c.id) AS direct_tags,
               c.created_at, c.updated_at, relevance('card', c.id) AS relevance
        FROM cards c LEFT JOIN schedules s ON s.id=c.schedule_id""",
    doc="""- `ai_cards(id, title, note, kind, stage, priority, schedule, blocked_description, effort_points, tracked_mins, parent_id, series_id, categories, energy_types, direct_values, direct_tags, created_at, updated_at, relevance)`
  - `kind` goal | subgoal | action
  - `stage` backlog | sprint | today | done
  - `priority` critical | medium | low
  - `effort_points` 0.5 | 1 | 2 | 3 | 5 | 8 | 13, cost of one execution; NULL when unestimated or Effort Points are off
  - `tracked_mins` is the minutes the user spent on an action, NULL when not recorded
  - on a goal or a subgoal, `stage`, `effort_points` and `tracked_mins` are what the cards under it add up to
  - to total effort or time, add `WHERE kind = 'action'`
  - open Card rows are not planned execution counts; use `ai_current_sprint_metrics` for Sprint load and `get_scheduled` for calendar quantities
  - `categories` growth | people | work | chores | rest
  - `energy_types` physical | cognitive | emotional | spiritual
  - `schedule` is the original timing text, not computed dates or counts; on a goal or a subgoal it is the deadline
  - `blocked_description` NULL = unblocked; non-NULL = blocked; filter with `IS NOT NULL`
  - `categories`, `energy_types`, `direct_values` and `direct_tags` are comma-joined names, so match one with `LIKE '%Health%'`
  - `series_id` is the whole repeat series of one card; a card that never repeated is its own series
  - the checks on a card are `ai_checks WHERE card_id = <id>`""",
    searchable=Searchable("card", "cards", ("title", "note", "blocked_description")),
)


VIEWS = (AI_CARDS,)
