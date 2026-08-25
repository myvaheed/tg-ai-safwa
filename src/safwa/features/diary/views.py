"""The `ai_*` view over the Diary."""

from __future__ import annotations

from ...ai.sql import SqlView

AI_DIARY = SqlView(
    "ai_diary",
    """SELECT id, entry_date, body, feeling_score, created_at, updated_at FROM diary_entries""",
    doc="""- `ai_diary(id, entry_date, body, feeling_score, created_at, updated_at)`
  - `entry_date` is `YYYY-MM-DD`; `feeling_score` is 0-10 and NULL for a day that said nothing""",
)

VIEWS = (AI_DIARY,)
