"""The `ai_values` view."""

from __future__ import annotations

from tg_agent_shell.ai.sql import Searchable, SqlView

AI_VALUES = SqlView(
    "ai_values",
    """SELECT id, name, description, active, created_at, updated_at,
               relevance('value', id) AS relevance
        FROM "values\"""",
    doc="""- `ai_values(id, name, description, active, created_at, updated_at, relevance)`
  - `active` 0 | 1""",
    searchable=Searchable("value", "values", ("name", "description")),
)


VIEWS = (AI_VALUES,)
