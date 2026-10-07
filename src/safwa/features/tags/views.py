"""The `ai_tags` view."""

from __future__ import annotations

from tg_agent_shell.ai.sql import Searchable, SqlView

AI_TAGS = SqlView(
    "ai_tags",
    """SELECT id, name, description, created_at, updated_at, relevance('tag', id) AS relevance
        FROM tags""",
    doc="- `ai_tags(id, name, description, created_at, updated_at, relevance)`",
    searchable=Searchable("tag", "tags", ("name", "description")),
)


VIEWS = (AI_TAGS,)
