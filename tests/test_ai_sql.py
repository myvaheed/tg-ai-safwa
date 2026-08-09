from __future__ import annotations

import pytest

from safwa.ai.contracts import CardToolInput, mutation_change_from_tool
from safwa.ai.sql import UnsafeQueryError, validate_read_sql


def test_native_mutation_tools_become_typed_change_intents():
    draft = CardToolInput(mode="draft", kind="action", title="Read one page", effort_points=1)
    assert draft.kind == "action"
    change = mutation_change_from_tool("card", {"mode": "edit", "id": 42, "priority": "critical"})
    assert (change.entity, change.action, change.id, change.values) == (
        "card",
        "update",
        42,
        {"priority": "critical"},
    )
    remove = mutation_change_from_tool("remove", {"type": "card", "id": 42, "permanent": True})
    assert (remove.entity, remove.action, remove.id) == ("card", "delete", 42)
    with pytest.raises(ValueError):
        CardToolInput(mode="draft", kind="action")
    with pytest.raises(ValueError):
        mutation_change_from_tool("remove", {"type": "tag", "id": 42, "permanent": True})


def test_card_tool_modes_reject_ambiguous_mutations():
    change = mutation_change_from_tool(
        "card",
        {
            "mode": "edit",
            "id": 42,
            "categories": ["contribution", "rest"],
            "energy_types": ["physical", "social"],
        },
    )
    assert change.values == {
        "categories": ["contribution", "rest"],
        "energy_types": ["physical", "social"],
    }
    root = mutation_change_from_tool("card", {"mode": "edit", "id": 42, "parent_id": None})
    assert root.values == {"parent_id": None}
    with pytest.raises(ValueError):
        CardToolInput(mode="move", id=42, stage="today", categories=["work"])
    with pytest.raises(ValueError):
        CardToolInput(mode="complete", id=42, note="also change this")
    with pytest.raises(ValueError):
        CardToolInput(mode="link", id=42, tag_id=3, value_id=4)


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM ai_cards",
        "SELECT * FROM cards",
        'SELECT * FROM "cards"',
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
