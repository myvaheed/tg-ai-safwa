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
               condition, until, total, fired, lead_minutes,
               local_time(next_fire_at) AS next_fire_at_local,
               created_at, updated_at, relevance('reminder', id) AS relevance
        FROM reminders""",
    doc="""- `ai_reminders(id, instruction, schedule_kind, weekdays, month_days, at_time, interval_minutes, condition, until, total, fired, lead_minutes, next_fire_at_local, created_at, updated_at, relevance)`
  - `schedule_kind` once | interval | daily | weekly | monthly | condition
  - `weekdays` and `month_days` are JSON arrays; `at_time` is local `HH:MM:SS`
  - `month_days` are days of the month, 1 to 31; 31 is the last day of every month
  - `until` is the last local day it fires on, `total` how many times it fires in all and `fired` how many it has; null runs on
  - `lead_minutes` is how many minutes before each moment it fires; `next_fire_at_local` is that earlier time
  - `condition` is JSON `{items, series, kind, answer, count, in_row, once}`: `kind` is check or action; it fires each time the answers (yes | no | any; an action's Done is yes) of every copy of the `series` reach a multiple of `count`, or once at `count` when `once`; in a row when `in_row`; its `next_fire_at_local` is null""",
    searchable=Searchable("reminder", "reminders", ("instruction",)),
)

VIEWS = (AI_REMINDERS,)
