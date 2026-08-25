"""The `ai_*` views over Cards and their history."""

from __future__ import annotations

from ...ai.sql import MARKER_FORMAT, SqlView
from .model import TERMINAL_STAGES

_TERMINAL_STAGE_SQL = ", ".join(f"'{stage.value}'" for stage in TERMINAL_STAGES)


AI_CARDS = SqlView(
    "ai_cards",
    f"""SELECT c.id,
               CASE WHEN c.repeatable AND c.effective_stage IN ({_TERMINAL_STAGE_SQL})
                    THEN c.title || printf('{MARKER_FORMAT}',
                         (SELECT count(*) FROM cards p
                          WHERE p.repeat_series_id = c.repeat_series_id AND p.id <= c.id))
                    ELSE c.title END AS title,
               c.note, c.kind, c.effective_stage AS stage, c.priority,
               c.hard_time, c.blocked, c.blocked_description,
               c.effort_points, c.repeatable, c.parent_id,
               (SELECT group_concat(cc.category, ',') FROM card_categories cc
                WHERE cc.card_id=c.id) AS categories,
               (SELECT group_concat(ce.energy_type, ',') FROM card_energy_types ce
                WHERE ce.card_id=c.id) AS energy_types,
               (SELECT group_concat(v.name, ',') FROM card_values cv
                JOIN "values" v ON v.id=cv.value_id WHERE cv.card_id=c.id) AS direct_values,
               (SELECT group_concat(t.name, ',') FROM card_tags ct
                JOIN tags t ON t.id=ct.tag_id WHERE ct.card_id=c.id) AS direct_tags,
               (SELECT group_concat(k.title, ',') FROM card_checks cc
                JOIN checks k ON k.id=cc.check_id
                WHERE cc.card_id=c.id AND k.archived_at IS NULL) AS direct_checks,
               c.created_at, c.updated_at
        FROM cards c
        WHERE c.archived_at IS NULL""",
)


AI_CARD_EVENTS = SqlView(
    "ai_card_events",
    """SELECT id, card_id, sprint_id, actor, operation, created_at FROM card_events""",
)


VIEWS = (AI_CARDS, AI_CARD_EVENTS)
