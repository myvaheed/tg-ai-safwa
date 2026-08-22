"""The `ai_*` views over the planning data. One source: the allowlist is derived here."""

from __future__ import annotations

from ...ai.sql import SqlView
from ...constants import REPEAT_MARKER
from ...enums import TERMINAL_STAGES

# The marker `domain.repeat_marker` renders, as a SQLite format string: one wording, so a
# closed repeat reads the same whether the model queried it or the owner tapped a citation.
_MARKER_FORMAT = REPEAT_MARKER.replace("{index}", "%d")
_TERMINAL_STAGE_SQL = ", ".join(f"'{stage.value}'" for stage in TERMINAL_STAGES)

AI_CARDS = SqlView(
    "ai_cards",
    f"""SELECT c.id,
               CASE WHEN c.repeatable AND c.effective_stage IN ({_TERMINAL_STAGE_SQL})
                    THEN c.title || printf('{_MARKER_FORMAT}',
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
               (SELECT count(*) FROM card_checks cc
                JOIN checks k ON k.id=cc.check_id
                WHERE cc.card_id=c.id AND k.archived_at IS NULL
                  AND k.outcome IS NULL) AS pending_checks,
               c.created_at, c.updated_at
        FROM cards c
        WHERE c.archived_at IS NULL""",
)

# `status` exposes the derived Pending state so a query never has to know that Pending is
# stored as a null outcome.
AI_CHECKS = SqlView(
    "ai_checks",
    f"""SELECT k.id,
               CASE WHEN k.repeatable AND k.outcome IS NOT NULL
                    THEN k.title || printf('{_MARKER_FORMAT}',
                         (SELECT count(*) FROM checks p
                          WHERE p.series_id = k.series_id AND p.id <= k.id))
                    ELSE k.title END AS title,
               k.repeatable,
               COALESCE(k.outcome, 'pending') AS status,
               k.resolved_at, k.series_id,
               (SELECT group_concat(cc.card_id, ',') FROM card_checks cc
                WHERE cc.check_id=k.id) AS card_ids,
               (SELECT group_concat(v.name, ',') FROM check_values cv
                JOIN "values" v ON v.id=cv.value_id WHERE cv.check_id=k.id) AS direct_values,
               k.created_at, k.updated_at
        FROM checks k
        WHERE k.archived_at IS NULL""",
)

AI_TAGS = SqlView(
    "ai_tags",
    """SELECT id, name, description, created_at, updated_at
        FROM tags WHERE archived_at IS NULL""",
)

AI_VALUES = SqlView(
    "ai_values",
    """SELECT id, name, description, active, created_at, updated_at
        FROM "values" WHERE archived_at IS NULL""",
)

AI_CURRENT_SPRINT = SqlView(
    "ai_current_sprint",
    """SELECT s.id, s.number, s.planned_start_date, s.planned_end_date, s.actual_started_at,
               s.success_criteria
        FROM sprints s JOIN workspace w ON w.active_sprint_id = s.id""",
)

AI_CURRENT_SPRINT_METRICS = SqlView(
    "ai_current_sprint_metrics",
    """SELECT sc.sprint_id,
          SUM(CASE WHEN sc.scope_kind='initial' THEN sc.effort_snapshot ELSE 0 END) committed,
          SUM(CASE WHEN sc.scope_kind='added' THEN sc.effort_snapshot ELSE 0 END) added,
          SUM(CASE WHEN sc.removed_at IS NOT NULL THEN sc.effort_snapshot ELSE 0 END) removed,
          SUM(CASE WHEN sc.result='done' THEN sc.effort_snapshot ELSE 0 END) completed,
          SUM(CASE WHEN sc.result='cancelled' THEN sc.effort_snapshot ELSE 0 END) cancelled
        FROM sprint_commitments sc JOIN workspace w ON w.active_sprint_id=sc.sprint_id
        GROUP BY sc.sprint_id""",
)

AI_CARD_EVENTS = SqlView(
    "ai_card_events",
    """SELECT id, card_id, sprint_id, actor, operation, created_at FROM card_events""",
)

VIEWS = (
    AI_TAGS,
    AI_CARDS,
    AI_CHECKS,
    AI_VALUES,
    AI_CURRENT_SPRINT,
    AI_CURRENT_SPRINT_METRICS,
    AI_CARD_EVENTS,
)
