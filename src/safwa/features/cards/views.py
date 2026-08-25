"""The `ai_*` views over Cards and their history."""

from __future__ import annotations

from ...ai.sql import LIVE_FORMAT, MARKER_FORMAT, SqlView
from ...constants import ARCHIVE_MARKER
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
)


AI_CARD_EVENTS = SqlView(
    "ai_card_events",
    """SELECT id, card_id, sprint_id, actor, operation, created_at FROM card_events""",
)


VIEWS = (AI_CARDS, AI_CARD_EVENTS)
