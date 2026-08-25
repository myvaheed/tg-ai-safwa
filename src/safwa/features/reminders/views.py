"""The `ai_*` view over Reminders."""

from __future__ import annotations

from ...ai.sql import SqlView

# The raw schedule columns, not a rendered `schedule`: `describe()` is the one wording,
# and duplicating it in SQL would give the model a second one to disagree with.  The next
# fire is the exception, because the model quotes it back to the owner: `local_time` is
# the only local column in the catalogue, and its name is what says so.
AI_REMINDERS = SqlView(
    "ai_reminders",
    """SELECT id, instruction, schedule_kind, weekdays, at_time, interval_minutes,
               quiet_windows, local_time(next_fire_at) AS next_fire_at_local,
               last_fired_at, fire_count, created_at, updated_at
        FROM reminders WHERE system = 0""",
    doc="""- `ai_reminders(id, instruction, schedule_kind, weekdays, at_time, interval_minutes, quiet_windows, next_fire_at_local, last_fired_at, fire_count, created_at, updated_at)`
  - `schedule_kind` once | interval | daily | weekly
  - `weekdays` and `quiet_windows` are JSON arrays; `at_time` is local `HH:MM:SS`""",
)

VIEWS = (AI_REMINDERS,)
