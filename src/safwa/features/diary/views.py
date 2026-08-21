"""The `ai_*` view over the Diary."""

from __future__ import annotations

from ...ai.sql import SqlView

AI_DIARY = SqlView(
    "ai_diary",
    """SELECT id, entry_date, body, feeling_score, created_at, updated_at FROM diary_entries""",
)

VIEWS = (AI_DIARY,)
