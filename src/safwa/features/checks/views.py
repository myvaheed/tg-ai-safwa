"""The `ai_checks` view."""

from __future__ import annotations

from tg_agent_shell.ai.sql import Searchable, SqlView

from ...foundation.marks import (
    ARCHIVE_MARKER,
    LIVE_FORMAT,
    MARKER_FORMAT,
    REPEAT_DONE,
    REPEAT_MISSED,
)
from .model import PENDING, CheckOutcome

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
                 || CASE WHEN k.series_id IS NOT NULL AND k.outcome IS NOT NULL
                         THEN printf('{MARKER_FORMAT}',
                              CASE k.outcome WHEN '{CheckOutcome.MISSED.value}'
                                   THEN '{REPEAT_MISSED}' ELSE '{REPEAT_DONE}' END,
                              (SELECT count(*) FROM checks p
                               WHERE COALESCE(p.series_id, p.id) = COALESCE(k.series_id, k.id)
                                 AND p.id <= k.id),
                              {_LIVE_TAIL})
                         ELSE '' END
                 || CASE WHEN k.archived_at IS NULL THEN '' ELSE '{ARCHIVE_MARKER}' END AS title,
               k.schedule,
               COALESCE(k.outcome, '{PENDING}') AS status,
               k.resolved_at,
               COALESCE(k.series_id, k.id) AS series_id,
               (SELECT cc.card_id FROM card_checks cc
                WHERE cc.check_id=k.id) AS card_id,
               (SELECT COALESCE(cd.repeat_series_id, cd.id) FROM card_checks cc
                JOIN cards cd ON cd.id=cc.card_id WHERE cc.check_id=k.id) AS card_series_id,
               (SELECT group_concat(v.name, ',') FROM check_values cv
                JOIN "values" v ON v.id=cv.value_id WHERE cv.check_id=k.id) AS direct_values,
               k.created_at, k.updated_at, relevance('check', k.id) AS relevance
        FROM checks k""",
    doc="""- `ai_checks(id, title, schedule, status, resolved_at, series_id, card_id, card_series_id, direct_values, created_at, updated_at, relevance)`
  - `status` pending | passed | missed
  - `schedule` is the original timing text, not computed dates or counts
  - a Check with its own Schedule is independent; `card_id` is the one Card a plain Check hangs on, or NULL; `direct_values` is comma-joined
  - `series_id` is the whole series of this check; `card_series_id` is the series of its card: count answers across all copies of a repeating action by it
  - `direct_values` are the Values this Check measures; they are its own, not the Values of its Cards""",
    searchable=Searchable("check", "checks", ("title",)),
)


VIEWS = (AI_CHECKS,)
