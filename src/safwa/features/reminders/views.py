"""The `ai_*` view over Reminders."""

from __future__ import annotations

from tg_agent_shell.ai.sql import Searchable, SqlView

# The raw schedule columns, not a rendered `schedule`: `describe()` is the one wording,
# and duplicating it in SQL would give the model a second one to disagree with.  The next
# fire is the exception, because the model quotes it back to the owner: `local_time` is
# the only local column in the catalogue, and its name is what says so.
AI_REMINDERS = SqlView(
    "ai_reminders",
    """SELECT id, instruction, schedule_kind, weekdays, month_days, at_time, interval_minutes,
               quiet_windows, condition, until, total, fired,
               local_time(next_fire_at) AS next_fire_at_local,
               created_at, updated_at, relevance('reminder', id) AS relevance
        FROM reminders""",
    doc="""- `ai_reminders(id, instruction, schedule_kind, weekdays, month_days, at_time, interval_minutes, quiet_windows, condition, until, total, fired, next_fire_at_local, created_at, updated_at, relevance)`
  - `schedule_kind` once | interval | daily | weekly | monthly | condition
  - `weekdays`, `month_days` and `quiet_windows` are JSON arrays; `at_time` is local `HH:MM:SS`
  - `month_days` are days of the month, 1 to 31; 31 is the last day of every month
  - `until` is the last local day it fires on, `total` how many times it fires in all and `fired` how many it has; null runs on
  - `condition` is JSON `{items, sql, answer, count, in_row}`: it fires each time the answers (passed | missed | answered | done) of the ids `sql` finds reach a multiple of `count`, in a row when `in_row`; its `next_fire_at_local` is null""",
    searchable=Searchable("reminder", "reminders", ("instruction",)),
)

VIEWS = (AI_REMINDERS,)
