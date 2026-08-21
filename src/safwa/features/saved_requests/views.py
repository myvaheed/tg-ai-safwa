"""The `ai_*` view over saved Requests."""

from __future__ import annotations

from ...ai.sql import SqlView

AI_REQUESTS = SqlView(
    "ai_requests",
    """SELECT id, name, description, query_sql, created_at, updated_at
        FROM saved_requests WHERE archived_at IS NULL""",
)

VIEWS = (AI_REQUESTS,)
