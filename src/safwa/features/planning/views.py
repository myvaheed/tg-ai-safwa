"""The `ai_*` views over the Sprint the owner committed to."""

from __future__ import annotations

from tg_agent_shell.ai.sql import SqlView

AI_CURRENT_SPRINT = SqlView(
    "ai_current_sprint",
    """SELECT s.id, s.number, s.planned_start_date, s.planned_end_date, s.actual_started_at,
               s.success_criteria
        FROM sprints s JOIN workspace w ON w.active_sprint_id = s.id""",
    doc="""- `ai_current_sprint(id, number, planned_start_date, planned_end_date, actual_started_at, success_criteria)`
  - `number` is `yy.MM-xx` text — the month the sprint started in, then its place in that month
  - `planned_start_date` and `planned_end_date` are `YYYY-MM-DD`; one row at most, none in Planning""",
)


AI_CURRENT_SPRINT_METRICS = SqlView(
    "ai_current_sprint_metrics",
    """SELECT sc.sprint_id,
          CASE WHEN (SELECT effort_tracking FROM user_profile WHERE id=1)
            THEN SUM(CASE WHEN sc.scope_kind='initial' THEN COALESCE(sc.effort_snapshot,0) ELSE 0 END) END committed,
          CASE WHEN (SELECT effort_tracking FROM user_profile WHERE id=1)
            THEN SUM(CASE WHEN sc.scope_kind='added' THEN COALESCE(sc.effort_snapshot,0) ELSE 0 END) END added,
          CASE WHEN (SELECT effort_tracking FROM user_profile WHERE id=1)
            THEN SUM(CASE WHEN sc.removed_at IS NOT NULL THEN COALESCE(sc.effort_snapshot,0) ELSE 0 END) END removed,
          CASE WHEN (SELECT effort_tracking FROM user_profile WHERE id=1)
            THEN SUM(CASE WHEN sc.result='done' THEN COALESCE(sc.effort_snapshot,0) ELSE 0 END) END completed,
          SUM(sc.scope_kind='initial') actions_committed,
          SUM(sc.scope_kind='added') actions_added,
          SUM(sc.removed_at IS NOT NULL) actions_removed,
          SUM(COALESCE(sc.result='done',0)) actions_completed,
          SUM(sc.effort_snapshot IS NULL) unestimated_actions
        FROM sprint_commitments sc JOIN workspace w ON w.active_sprint_id=sc.sprint_id
        GROUP BY sc.sprint_id""",
    doc="""- `ai_current_sprint_metrics(sprint_id, committed, added, removed, completed, actions_committed, actions_added, actions_removed, actions_completed, unestimated_actions)`
  - `committed`, `added`, `removed`, `completed` sum known effort points; NULL while Effort Points are off
  - `actions_*` count Actions regardless of estimates; `unestimated_actions` counts missing estimates
  - with missing estimates, effort sums are partial, never a completion percentage""",
)


VIEWS = (AI_CURRENT_SPRINT, AI_CURRENT_SPRINT_METRICS)
