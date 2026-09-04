"""The `ai_*` view over saved Requests."""

from __future__ import annotations

from tg_agent_shell.ai.sql import SqlView

AI_REQUESTS = SqlView(
    "ai_requests",
    """SELECT id, name, description, query_sql, created_at, updated_at
        FROM saved_requests""",
    doc="- `ai_requests(id, name, description, query_sql, created_at, updated_at)`",
)

VIEWS = (AI_REQUESTS,)
