"""Validated, composable saved-card filters.

The filter format is deliberately data-only.  It is safe for the advisor to
author, straightforward to persist as JSON, and compiles only from a small
allowlist of SQLAlchemy expressions.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import and_, exists, not_, or_, select
from sqlalchemy.sql.elements import ColumnElement

from .enums import CardKind, CardStage, Priority
from .models import Card, CardDependency, CardTag, CardValue


class RequestFilterError(ValueError):
    pass


_MAX_DEPTH = 4
_MAX_CLAUSES = 40
_SCALAR_FIELDS = {
    "kind": {"eq", "in"},
    "stage": {"eq", "in"},
    "priority": {"eq", "in"},
    "hard_time": {"eq"},
    "repeatable": {"eq"},
    "liked": {"eq"},
    "effort_points": {"eq", "in", "gte", "lte"},
    "parent_id": {"eq", "is_null"},
    "title": {"contains"},
    "note": {"contains"},
    "has_blockers": {"eq"},
}
_LINK_FIELDS = {"tag_id", "value_id"}


def normalize_filter_spec(raw: Any) -> dict[str, Any]:
    """Return a canonical filter or raise a user-safe validation error."""

    if not isinstance(raw, dict):
        raise RequestFilterError("Request filter must be an object")
    clauses_seen = 0

    def validate_group(group: Any, depth: int) -> dict[str, Any]:
        nonlocal clauses_seen
        if depth > _MAX_DEPTH:
            raise RequestFilterError("Request filter nesting is too deep")
        if not isinstance(group, dict) or set(group) - {"all", "any"}:
            raise RequestFilterError("A filter group may contain only all and any")
        if not group:
            raise RequestFilterError("A filter group cannot be empty")
        normalized: dict[str, Any] = {}
        for conjunction in ("all", "any"):
            if conjunction not in group:
                continue
            clauses = group[conjunction]
            if not isinstance(clauses, list) or not clauses:
                raise RequestFilterError(f"Filter {conjunction} must contain at least one clause")
            normalized[conjunction] = [validate_clause(clause, depth + 1) for clause in clauses]
        return normalized

    def scalar_values(value: Any, *, allow_many: bool) -> str | bool | int | list[str | int]:
        if allow_many:
            if not isinstance(value, list) or not value or len(value) > 20:
                raise RequestFilterError("Filter list values must contain 1 to 20 items")
            if not all(isinstance(item, (str, int)) and not isinstance(item, bool) for item in value):
                raise RequestFilterError("Filter list values must be text or numbers")
            return list(dict.fromkeys(value))
        if isinstance(value, bool | str | int):
            return value
        raise RequestFilterError("Filter value must be text, a number, or true/false")

    def validate_clause(clause: Any, depth: int) -> dict[str, Any]:
        nonlocal clauses_seen
        clauses_seen += 1
        if clauses_seen > _MAX_CLAUSES:
            raise RequestFilterError("Request filter has too many clauses")
        if isinstance(clause, dict) and ("all" in clause or "any" in clause):
            return validate_group(clause, depth)
        if not isinstance(clause, dict) or set(clause) != {"field", "op", "value"}:
            raise RequestFilterError("Each filter clause needs field, op, and value")
        field, operation, value = clause["field"], clause["op"], clause["value"]
        if not isinstance(field, str) or not isinstance(operation, str):
            raise RequestFilterError("Filter field and op must be text")
        if field in _LINK_FIELDS:
            if operation not in {"any_of", "all_of", "none_of"}:
                raise RequestFilterError(f"Unsupported operation for {field}")
            normalized_value = scalar_values(value, allow_many=True)
        elif field in _SCALAR_FIELDS:
            if operation not in _SCALAR_FIELDS[field]:
                raise RequestFilterError(f"Unsupported operation for {field}")
            if operation == "is_null":
                if not isinstance(value, bool):
                    raise RequestFilterError("is_null requires true or false")
                normalized_value = value
            elif operation == "in":
                normalized_value = scalar_values(value, allow_many=True)
            elif operation == "contains":
                if not isinstance(value, str) or not value.strip() or len(value) > 200:
                    raise RequestFilterError("contains requires 1 to 200 characters")
                normalized_value = value.strip()
            else:
                normalized_value = scalar_values(value, allow_many=False)
        else:
            raise RequestFilterError(f"Unknown filter field: {field}")
        _validate_domain_value(field, operation, normalized_value)
        return {"field": field, "op": operation, "value": normalized_value}

    return validate_group(raw, 1)


def _validate_domain_value(field: str, operation: str, value: Any) -> None:
    values: Iterable[Any] = value if operation in {"in", "any_of", "all_of", "none_of"} else [value]
    allowed = {
        "kind": {item.value for item in CardKind},
        "stage": {item.value for item in CardStage},
        "priority": {item.value for item in Priority},
        "effort_points": {1, 2, 3, 5, 8, 13},
    }.get(field)
    if allowed is not None and not set(values).issubset(allowed):
        raise RequestFilterError(f"Invalid {field} value")
    if field in {"hard_time", "repeatable", "liked", "has_blockers"} and not isinstance(value, bool):
        raise RequestFilterError(f"{field} requires true or false")


def compile_filter(filter_spec: dict[str, Any]) -> ColumnElement[bool]:
    """Compile a previously-normalized specification without raw SQL."""

    normalized = normalize_filter_spec(filter_spec)

    def compile_group(group: dict[str, Any]) -> ColumnElement[bool]:
        expressions: list[ColumnElement[bool]] = []
        if "all" in group:
            expressions.append(and_(*(compile_clause(clause) for clause in group["all"])))
        if "any" in group:
            expressions.append(or_(*(compile_clause(clause) for clause in group["any"])))
        return and_(*expressions)

    def compile_clause(clause: dict[str, Any]) -> ColumnElement[bool]:
        if "all" in clause or "any" in clause:
            return compile_group(clause)
        field, operation, value = clause["field"], clause["op"], clause["value"]
        if field in _LINK_FIELDS:
            link = CardTag if field == "tag_id" else CardValue
            identifier = link.tag_id if field == "tag_id" else link.value_id
            def linked(candidate: str) -> ColumnElement[bool]:
                return exists(
                    select(1).select_from(link).where(link.card_id == Card.id, identifier == candidate)
                )
            if operation == "any_of":
                return exists(
                    select(1).select_from(link).where(link.card_id == Card.id, identifier.in_(value))
                )
            if operation == "all_of":
                return and_(*(linked(candidate) for candidate in value))
            return not_(
                exists(
                    select(1).select_from(link).where(link.card_id == Card.id, identifier.in_(value))
                )
            )
        if field == "has_blockers":
            condition = exists(
                select(1).select_from(CardDependency).where(CardDependency.blocked_card_id == Card.id)
            )
            return condition if value else not_(condition)
        if field == "parent_id" and operation == "is_null":
            return Card.parent_id.is_(None) if value else Card.parent_id.is_not(None)
        column = getattr(Card, "effective_stage" if field == "stage" else field)
        if operation == "eq":
            return column == value
        if operation == "in":
            return column.in_(value)
        if operation == "gte":
            return column >= value
        if operation == "lte":
            return column <= value
        if operation == "contains":
            return column.ilike(f"%{value}%")
        raise RequestFilterError(f"Unsupported filter operation: {operation}")

    return compile_group(normalized)


def request_cards_statement(filter_spec: dict[str, Any]):
    return select(Card).where(Card.archived_at.is_(None), compile_filter(filter_spec))
