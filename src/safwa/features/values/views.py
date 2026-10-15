"""The `ai_values` view."""

from __future__ import annotations

from tg_agent_shell.ai.sql import Searchable, SqlView

AI_VALUES = SqlView(
    "ai_values",
    """SELECT id, name, description, "order", created_at, updated_at,
               relevance('value', id) AS relevance
        FROM "values\"""",
    doc="""- `ai_values(id, name, description, "order", created_at, updated_at, relevance)`
  - Quote `"order"` in SQL; 1 is highest.""",
    searchable=Searchable("value", "values", ("name", "description")),
)


VIEWS = (AI_VALUES,)
