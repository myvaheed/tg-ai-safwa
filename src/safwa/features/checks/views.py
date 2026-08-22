"""The `ai_checks` view."""

from __future__ import annotations

from ...ai.sql import MARKER_FORMAT, SqlView

# `status` exposes the derived Pending state so a query never has to know that Pending is
# stored as a null outcome.
AI_CHECKS = SqlView(
    "ai_checks",
    f"""SELECT k.id,
               CASE WHEN k.repeatable AND k.outcome IS NOT NULL
                    THEN k.title || printf('{MARKER_FORMAT}',
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


VIEWS = (AI_CHECKS,)
