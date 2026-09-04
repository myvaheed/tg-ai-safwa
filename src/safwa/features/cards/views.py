"""The `ai_*` views over Cards and their history."""

from __future__ import annotations

from tg_agent_shell.ai.sql import SqlView

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
                 || CASE WHEN c.repeatable AND c.effective_stage IN ({_TERMINAL_STAGE_SQL})
                         THEN printf('{MARKER_FORMAT}',
                              (SELECT count(*) FROM cards p
                               WHERE COALESCE(p.repeat_series_id, p.id)
                                     = COALESCE(c.repeat_series_id, c.id)
                                 AND p.id <= c.id),
                              {_LIVE_TAIL})
                         ELSE '' END
                 || CASE WHEN c.archived_at IS NULL THEN '' ELSE '{ARCHIVE_MARKER}' END AS title,
               c.note, c.kind, c.effective_stage AS stage, c.priority,
               c.hard_time, c.blocked, c.blocked_description,
               c.effort_points, c.repeatable, c.parent_id,
               COALESCE(c.repeat_series_id, c.id) AS series_id,
               (SELECT group_concat(cc.category, ',') FROM card_categories cc
                WHERE cc.card_id=c.id) AS categories,
               (SELECT group_concat(ce.energy_type, ',') FROM card_energy_types ce
                WHERE ce.card_id=c.id) AS energy_types,
               (SELECT group_concat(v.name, ',') FROM card_values cv
                JOIN "values" v ON v.id=cv.value_id WHERE cv.card_id=c.id) AS direct_values,
               (SELECT group_concat(t.name, ',') FROM card_tags ct
                JOIN tags t ON t.id=ct.tag_id WHERE ct.card_id=c.id) AS direct_tags,
               c.created_at, c.updated_at
        FROM cards c""",
    doc="""- `ai_cards(id, title, note, kind, stage, priority, hard_time, blocked, blocked_description, effort_points, repeatable, parent_id, series_id, categories, energy_types, direct_values, direct_tags, created_at, updated_at)`
  - `kind` goal | idea | action
  - `stage` backlog | sprint | today | done | cancelled
  - `priority` critical | medium | low
  - `effort_points` 1 | 2 | 3 | 5 | 8 | 13, the size of one action
  - on a goal or an idea, `stage`, `blocked` and `effort_points` are what the cards under it add up to
  - to total effort always add `WHERE kind = 'action'`, or each action is counted again inside every parent
  - `categories` self | contribution | work | rest
  - `energy_types` physical | cognitive | social | values
  - `hard_time`, `blocked`, `repeatable` 0 | 1
  - `categories`, `energy_types`, `direct_values` and `direct_tags` are comma-joined names, so match one with `LIKE '%Health%'`
  - `series_id` is the whole repeat series of one card; a card that never repeated is its own series
  - the checks on a card are `ai_checks WHERE card_id = <id>`""",
)


# The log the heavy analyzer reads and nobody else: one row per change, which answers a
# question about a stretch of time and nothing a screen or a proposal ever asks.
AI_CARD_EVENTS = SqlView(
    "ai_card_events",
    """SELECT id, card_id, sprint_id, actor, operation, created_at FROM card_events""",
    doc="""- `ai_card_events(id, card_id, sprint_id, actor, operation, created_at)`
  - one row per change to one card, oldest first by `id`
  - `actor` user_ui | ai — the owner on a screen, or a proposal the owner approved
  - `operation` create | update | edit_<field> | set_parent | move | done | cancelled | archive | restore | link_<kind> | unlink_<kind>, where kind is value | tag | check | category | energy
  - `sprint_id` is the Sprint that was running then, or NULL
  - archiving that happened on its own writes no row, so `archive` is always the owner's own""",
)


VIEWS = (AI_CARDS, AI_CARD_EVENTS)
