"""The `ai_*` view over Reminders."""

from __future__ import annotations

from tg_agent_shell.ai.sql import Searchable, SqlView

# The raw schedule columns, not a rendered `schedule`: `describe()` is the one wording,
# and duplicating it in SQL would give the model a second one to disagree with.  The next
# fire is the exception, because the model quotes it back to the owner: `local_time` is
# the only local column in the catalogue, and its name is what says so.
AI_REMINDERS = SqlView(
    "ai_reminders",
    """SELECT id, instruction, schedule_kind, weekdays, at_time, interval_minutes,
               quiet_windows, run_series_id, run_outcome, run_length,
               local_time(next_fire_at) AS next_fire_at_local,
               created_at, updated_at, relevance('reminder', id) AS relevance
        FROM reminders""",
    doc="""- `ai_reminders(id, instruction, schedule_kind, weekdays, at_time, interval_minutes, quiet_windows, run_series_id, run_outcome, run_length, next_fire_at_local, created_at, updated_at, relevance)`
  - `schedule_kind` once | interval | daily | weekly | run
  - `weekdays` and `quiet_windows` are JSON arrays; `at_time` is local `HH:MM:SS`
  - run fires each time Check series `run_series_id` reaches a multiple of `run_length` `run_outcome` (passed | missed) answers in a row; its `next_fire_at_local` is null""",
    searchable=Searchable("reminder", "reminders", ("instruction",)),
)

VIEWS = (AI_REMINDERS,)
