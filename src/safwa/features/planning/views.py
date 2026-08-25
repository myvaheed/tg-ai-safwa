"""The `ai_*` views over the Sprint the owner committed to."""

from __future__ import annotations

from ...ai.sql import SqlView

AI_CURRENT_SPRINT = SqlView(
    "ai_current_sprint",
    """SELECT s.id, s.number, s.planned_start_date, s.planned_end_date, s.actual_started_at,
               s.success_criteria
        FROM sprints s JOIN workspace w ON w.active_sprint_id = s.id""",
    doc="""- `ai_current_sprint(id, number, planned_start_date, planned_end_date, actual_started_at, success_criteria)`
  - `planned_start_date` and `planned_end_date` are `YYYY-MM-DD`; one row at most, none in Planning""",
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
    doc="""- `ai_current_sprint_metrics(sprint_id, committed, added, removed, completed, cancelled)`
  - every column is a sum of effort points, not a count of Cards""",
)


VIEWS = (AI_CURRENT_SPRINT, AI_CURRENT_SPRINT_METRICS)
