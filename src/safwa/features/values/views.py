"""The `ai_values` view."""

from __future__ import annotations

from ...ai.sql import SqlView

AI_VALUES = SqlView(
    "ai_values",
    """SELECT id, name, description, active, created_at, updated_at
        FROM "values" WHERE archived_at IS NULL""",
)


VIEWS = (AI_VALUES,)
