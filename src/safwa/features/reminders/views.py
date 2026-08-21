"""The `ai_*` view over Reminders."""

from __future__ import annotations

from ...ai.sql import SqlView

# The raw schedule columns, not a rendered `schedule`: `describe()` is the one wording,
# and duplicating it in SQL would give the model a second one to disagree with.
AI_REMINDERS = SqlView(
    "ai_reminders",
    """SELECT id, instruction, schedule_kind, weekdays, at_time, interval_minutes,
               quiet_windows, next_fire_at, last_fired_at, fire_count,
               created_at, updated_at
        FROM reminders WHERE system = 0""",
)

VIEWS = (AI_REMINDERS,)
