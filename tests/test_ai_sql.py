from __future__ import annotations

import pytest

from safwa.ai.contracts import AgentResponse
from safwa.ai.sql import UnsafeQueryError, validate_read_sql


def test_agent_response_shapes():
    assert AgentResponse(kind="answer", message="ok").kind == "answer"
    with pytest.raises(ValueError):
        AgentResponse(kind="answer", message="bad", sql="SELECT * FROM ai_cards")


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM ai_cards",
        "SELECT * FROM cards",
        "PRAGMA table_info(ai_cards)",
        "SELECT * FROM ai_cards; SELECT * FROM ai_values",
    ],
)
def test_read_sql_rejects_unsafe_queries(sql):
    with pytest.raises(UnsafeQueryError):
        validate_read_sql(sql)


def test_read_sql_accepts_views_and_ctes():
    assert validate_read_sql("SELECT title FROM ai_cards LIMIT 5")
    assert validate_read_sql("WITH x AS (SELECT * FROM ai_cards) SELECT count(*) FROM x")
