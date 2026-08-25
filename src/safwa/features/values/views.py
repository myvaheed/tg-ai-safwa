"""The `ai_values` view."""

from __future__ import annotations

from ...ai.sql import SqlView

AI_VALUES = SqlView(
    "ai_values",
    """SELECT id, name, description, active, created_at, updated_at
        FROM "values\"""",
    doc="""- `ai_values(id, name, description, active, created_at, updated_at)`
  - `active` 0 | 1""",
)


VIEWS = (AI_VALUES,)
