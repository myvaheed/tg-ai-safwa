"""The `ai_tags` view."""

from __future__ import annotations

from tg_agent_shell.ai.sql import SqlView

AI_TAGS = SqlView(
    "ai_tags",
    """SELECT id, name, description, created_at, updated_at
        FROM tags""",
    doc="- `ai_tags(id, name, description, created_at, updated_at)`",
)


VIEWS = (AI_TAGS,)
