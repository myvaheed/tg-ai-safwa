"""The `ai_tags` view."""

from __future__ import annotations

from ...ai.sql import SqlView

AI_TAGS = SqlView(
    "ai_tags",
    """SELECT id, name, description, created_at, updated_at
        FROM tags WHERE archived_at IS NULL""",
)


VIEWS = (AI_TAGS,)
