"""The `ai_checks` view."""

from __future__ import annotations

from tg_agent_shell.ai.sql import SqlView

from ...foundation.marks import ARCHIVE_MARKER, LIVE_FORMAT, MARKER_FORMAT

# The open instance of this row's series, already rendered as the marker's tail. See the
# note in `features/cards/views.py` for why this is a plain SELECT and not a derived table.
_LIVE_TAIL = f"""COALESCE((SELECT printf('{LIVE_FORMAT}', p.id) FROM checks p
                  WHERE COALESCE(p.series_id, p.id) = COALESCE(k.series_id, k.id)
                    AND p.outcome IS NULL AND p.archived_at IS NULL
                  ORDER BY p.id DESC LIMIT 1), '')"""

# `status` exposes the derived Pending state so a query never has to know that Pending is
# stored as a null outcome. `card_series_id` is the series of the Card this Check hangs on,
# so counting every answer across every copy of a repeating Action joins nothing.
AI_CHECKS = SqlView(
    "ai_checks",
    f"""SELECT k.id,
               k.title
                 || CASE WHEN k.repeatable AND k.outcome IS NOT NULL
                         THEN printf('{MARKER_FORMAT}',
                              (SELECT count(*) FROM checks p
                               WHERE COALESCE(p.series_id, p.id) = COALESCE(k.series_id, k.id)
                                 AND p.id <= k.id),
                              {_LIVE_TAIL})
                         ELSE '' END
                 || CASE WHEN k.archived_at IS NULL THEN '' ELSE '{ARCHIVE_MARKER}' END AS title,
               k.repeatable,
               COALESCE(k.outcome, 'pending') AS status,
               k.resolved_at,
               COALESCE(k.series_id, k.id) AS series_id,
               (SELECT cc.card_id FROM card_checks cc
                WHERE cc.check_id=k.id) AS card_id,
               (SELECT COALESCE(cd.repeat_series_id, cd.id) FROM card_checks cc
                JOIN cards cd ON cd.id=cc.card_id WHERE cc.check_id=k.id) AS card_series_id,
               (SELECT group_concat(v.name, ',') FROM check_values cv
                JOIN "values" v ON v.id=cv.value_id WHERE cv.check_id=k.id) AS direct_values,
               k.created_at, k.updated_at
        FROM checks k""",
    doc="""- `ai_checks(id, title, repeatable, status, resolved_at, series_id, card_id, card_series_id, direct_values, created_at, updated_at)`
  - `status` pending | passed | missed
  - `repeatable` 0 | 1; `card_id` is the one Card it hangs on, or NULL; `direct_values` is comma-joined
  - `series_id` is the whole series of this check; `card_series_id` is the series of its card, so one query counts every answer across every copy of a repeating action
  - `direct_values` are the Values this Check measures; they are its own, not the Values of its Cards""",
)


VIEWS = (AI_CHECKS,)
