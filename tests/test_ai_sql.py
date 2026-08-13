from __future__ import annotations

import pytest

from safwa.ai.contracts import (
    CardToolInput,
    CheckToolInput,
    QueryToolInput,
    mutation_change_from_tool,
    tool_json_schema,
)
from safwa.ai.sql import ReadOnlyQueryRunner, UnsafeQueryError, validate_read_sql


def test_native_mutation_tools_become_typed_change_intents():
    creation = CardToolInput(
        mode="create", kind="action", title="Read one page", effort_points=1
    )
    assert creation.kind == "action"
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
        CardToolInput(mode="create", kind="action")
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
    with pytest.raises(ValueError, match="at least one relationship reference"):
        CardToolInput(mode="link", id=42, tag_ids=[])


def test_tool_inputs_drop_incidental_null_placeholders_from_every_mutation():
    change = mutation_change_from_tool(
        "card",
        {
            "mode": "create",
            "id": None,
            "kind": "action",
            "title": "Do twenty pull-ups",
            "note": None,
            "stage": "backlog",
            "priority": "medium",
            "hard_time": False,
            "blocked": False,
            "blocked_description": None,
            "effort_points": 1,
            "repeatable": False,
            "categories": ["self"],
            "energy_types": ["physical"],
            "value_id": None,
            "value_ids": None,
            "value_query": None,
            "tag_id": None,
            "tag_ids": None,
            "tag_query": None,
            "check_id": None,
            "check_ids": None,
            "check_query": None,
            "parent_id": None,
            "parent_query": None,
        },
    )

    assert change.values == {
        "kind": "action",
        "title": "Do twenty pull-ups",
        "stage": "backlog",
        "priority": "medium",
        "hard_time": False,
        "blocked": False,
        "effort_points": 1,
        "repeatable": False,
        "categories": ["self"],
        "energy_types": ["physical"],
    }

    check = CheckToolInput.model_validate(
        {
            "mode": "create",
            "id": None,
            "title": "Form is safe",
            "repeatable": False,
        }
    )
    assert check.model_fields_set == {"mode", "title", "repeatable"}


def test_zero_id_placeholders_are_ignored_but_real_ids_must_be_positive():
    change = mutation_change_from_tool(
        "card",
        {
            "mode": "create",
            "id": 0,
            "kind": "action",
            "title": "Do twenty pull-ups",
            "effort_points": 1,
            "value_id": 0,
            "value_ids": [],
            "tag_id": "0",
            "tag_ids": [0, "0"],
            "check_id": 0,
            "check_ids": [],
        },
    )
    assert change.values == {
        "kind": "action",
        "title": "Do twenty pull-ups",
        "effort_points": 1,
    }

    with pytest.raises(ValueError):
        mutation_change_from_tool("remove", {"type": "card", "id": 0})


@pytest.mark.parametrize(
    ("tool_name", "arguments", "expected_values"),
    [
        (
            "check",
            {"mode": "create", "id": None, "title": "Form is safe", "repeatable": False},
            {"title": "Form is safe", "repeatable": False},
        ),
        (
            "value",
            {"mode": "create", "id": None, "name": "Health", "description": None, "active": False},
            {"name": "Health", "active": False},
        ),
        (
            "tag",
            {"mode": "create", "id": None, "name": "Training", "description": None},
            {"name": "Training"},
        ),
        (
            "request",
            {
                "mode": "create",
                "id": None,
                "name": "Open actions",
                "description": None,
                "sql": "SELECT id FROM ai_cards WHERE stage = 'backlog'",
            },
            {
                "name": "Open actions",
                "sql": "SELECT id FROM ai_cards WHERE stage = 'backlog'",
            },
        ),
    ],
)
def test_null_placeholders_are_ignored_across_mutation_tools(
    tool_name, arguments, expected_values
):
    change = mutation_change_from_tool(tool_name, arguments)
    assert change.values == expected_values

    remove = mutation_change_from_tool(
        "remove", {"type": "card", "id": 42, "permanent": None}
    )
    assert remove.action == "archive"


@pytest.mark.parametrize("placeholder", [None, "", "  ", "null", "None", "NIL", "undefined"])
def test_optional_reference_placeholders_are_omitted(placeholder):
    change = mutation_change_from_tool(
        "card",
        {
            "mode": "create",
            "kind": "action",
            "title": "Do twenty pull-ups",
            "effort_points": 1,
            "parent_query": placeholder,
            "value_query": [placeholder],
        },
    )
    assert "parent_query" not in change.values
    assert "value_query" not in change.values


def test_collection_arguments_recover_scalars_and_double_encoded_arrays():
    change = mutation_change_from_tool(
        "card",
        {
            "mode": "edit",
            "id": 42,
            "categories": "self",
            "energy_types": '["physical", null, "none"]',
            "value_ids": [3, None, "null"],
        },
    )
    assert change.values == {
        "categories": ["self"],
        "energy_types": ["physical"],
        "value_ids": [3],
    }


def test_parent_changes_are_explicit_and_unambiguous():
    remove_parent = mutation_change_from_tool(
        "card", {"mode": "edit", "id": 42, "title": "Renamed", "parent_id": None}
    )
    assert remove_parent.values == {"title": "Renamed", "parent_id": None}

    with pytest.raises(ValueError, match="either parent_id or parent_query"):
        mutation_change_from_tool(
            "card",
            {
                "mode": "edit",
                "id": 42,
                "parent_id": 7,
                "parent_query": "Fitness",
            },
        )



@pytest.mark.parametrize("placeholder", [None, "null", "None", "NIL", "undefined"])
def test_edit_parent_null_variants_remove_the_parent(placeholder):
    change = mutation_change_from_tool(
        "card", {"mode": "edit", "id": 42, "parent_id": placeholder}
    )
    assert change.values == {"parent_id": None}


def test_model_facing_tool_schemas_keep_optional_fields_nullable_for_constrained_decoders():
    schema = tool_json_schema(CardToolInput)

    def contains_null_type(value):
        if isinstance(value, dict):
            return value.get("type") == "null" or any(
                contains_null_type(item) for item in value.values()
            )
        if isinstance(value, list):
            return any(contains_null_type(item) for item in value)
        return False

    for field_name in ("id", "title", "value_id", "value_ids", "parent_id"):
        assert contains_null_type(schema["properties"][field_name])
    assert not contains_null_type(schema["properties"]["mode"])
    id_integer = next(
        variant
        for variant in schema["properties"]["id"]["anyOf"]
        if variant.get("type") == "integer"
    )
    assert id_integer["exclusiveMinimum"] == 0
    assert schema["additionalProperties"] is False
    assert "parent_id" not in schema.get("required", [])


def test_query_tool_rejects_null_empty_and_extra_arguments():
    for arguments in ({"sql": None}, {"sql": "  "}, {"sql": "SELECT 1", "unused": None}):
        with pytest.raises(ValueError):
            QueryToolInput.model_validate(arguments)

    assert QueryToolInput.model_validate({"sql": "  SELECT id FROM ai_cards  "}).sql == (
        "SELECT id FROM ai_cards"
    )


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


def test_read_sql_accepts_recursive_and_column_list_ctes():
    assert validate_read_sql(
        "WITH RECURSIVE tree AS (SELECT id FROM ai_cards WHERE id = 1 "
        "UNION ALL SELECT c.id FROM ai_cards c JOIN tree t ON c.parent_id = t.id) "
        "SELECT id FROM tree"
    )
    assert validate_read_sql("WITH t(card_id) AS (SELECT id FROM ai_cards) SELECT card_id FROM t")
    with pytest.raises(UnsafeQueryError):
        validate_read_sql("WITH RECURSIVE tree AS (SELECT id FROM cards) SELECT id FROM tree")


def _runner_over_cards(tmp_path, count: int, note: str = "", **caps):
    from sqlalchemy import create_engine

    from safwa.ai.sql import create_ai_views
    from safwa.models import Base

    path = tmp_path / "caps.db"
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        create_ai_views(connection)
        for index in range(count):
            connection.exec_driver_sql(
                "INSERT INTO cards(id,kind,title,note,manual_stage,effective_stage,priority,"
                "hard_time,repeatable,blocked,blocked_description,version,created_at,updated_at) "
                "VALUES (?,'action',?,?,'backlog','backlog','medium',0,0,0,'',1,"
                "CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                (index + 1, f"Card {index:03d}", note),
            )
    engine.dispose()
    return ReadOnlyQueryRunner(path, **caps)


async def test_query_result_reports_the_row_cap_and_asks_to_narrow(tmp_path):
    runner = _runner_over_cards(tmp_path, 8, row_limit=3, char_budget=10_000)

    outcome = await runner.run("SELECT id, title FROM ai_cards ORDER BY id")

    assert len(outcome.rows) == 3
    assert "Only the first 3 row(s) are shown" in outcome.notice
    assert "Narrow the query" in outcome.notice
    # The notice rides along as the last row, so the shape stays one JSON array.
    assert outcome.as_tool_result()[-1] == {"notice": outcome.notice}
    assert len(outcome.as_tool_result()) == 4


async def test_query_result_reports_the_character_budget(tmp_path):
    runner = _runner_over_cards(tmp_path, 40, note="x" * 400, row_limit=40, char_budget=1_000)

    outcome = await runner.run("SELECT id, title, note FROM ai_cards ORDER BY id")

    assert 0 < len(outcome.rows) < 40
    assert "character result budget" in outcome.notice
    assert "Narrow the query" in outcome.notice


async def test_query_result_reports_shortened_text_values(tmp_path):
    runner = _runner_over_cards(tmp_path, 1, note="y" * 500, cell_limit=100)

    outcome = await runner.run("SELECT note FROM ai_cards")

    assert len(outcome.rows[0]["note"]) == 100
    assert "cut to 100 characters" in outcome.notice


async def test_recursive_cte_walks_the_card_tree_under_the_authorizer(tmp_path):
    runner = _runner_over_cards(tmp_path, 3)

    outcome = await runner.run(
        "WITH RECURSIVE tree AS (SELECT id, parent_id FROM ai_cards WHERE id = 1 "
        "UNION ALL SELECT c.id, c.parent_id FROM ai_cards c JOIN tree t ON c.parent_id = t.id) "
        "SELECT id FROM tree"
    )

    assert outcome.rows == [{"id": 1}]


async def test_uncapped_query_carries_no_notice(tmp_path):
    runner = _runner_over_cards(tmp_path, 3)

    outcome = await runner.run("SELECT id FROM ai_cards ORDER BY id")

    assert outcome.notice is None
    assert outcome.as_tool_result() == [{"id": 1}, {"id": 2}, {"id": 3}]
