"""The `ai_values` view."""

from __future__ import annotations

from tg_agent_shell.ai.sql import Searchable, SqlView

AI_VALUES = SqlView(
    "ai_values",
    """SELECT v.id, v.name, v.description, v."order", v.created_at, v.updated_at,
               (SELECT group_concat(cv.card_id, ',') FROM card_values cv
                WHERE cv.value_id=v.id) AS card_ids,
               relevance('value', v.id) AS relevance
        FROM "values" v ORDER BY v."order\"""",
    doc="""- `ai_values(id, name, description, "order", created_at, updated_at, card_ids, relevance)`
  - Quote `"order"` in SQL; 1 is highest.
  - `card_ids` are directly linked Card ids, comma-joined, NULL with none; match one with `',' || card_ids || ',' LIKE '%,<id>,%'`.""",
    searchable=Searchable("value", "values", ("name", "description")),
)


VIEWS = (AI_VALUES,)
