"""Running a saved Request over Cards, Checks and Values."""

from __future__ import annotations

from collections.abc import Collection

from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.ai.sql import SQL_TOKEN, UnsafeQueryError, validated_read
from tg_agent_shell.foundation.errors import DomainError

from ..cards.model import Card
from ..checks.model import Check
from ..values.model import Value


class RequestQueryError(DomainError):
    """A saved query that cannot return Card, Check or Value references."""


async def _request_references(
    session: AsyncSession, raw: str, views: Collection[str]
) -> tuple[str, list[tuple[str, int]]]:
    """Validate the read and its actual typed references, including an empty result's columns."""
    if not isinstance(raw, str) or not raw.strip():
        raise RequestQueryError("A Request query is required")
    try:
        statement, sources = validated_read(raw, views)
    except UnsafeQueryError as error:
        raise RequestQueryError(str(error)) from error
    if not sources.intersection({"ai_cards", "ai_checks", "ai_values"}):
        raise RequestQueryError("The query must read ai_cards, ai_checks or ai_values")
    try:
        result = await session.execute(text(statement))
    except SQLAlchemyError as error:
        raise RequestQueryError(f"The query does not run: {getattr(error, 'orig', error)}") from error
    for column in ("id", "item_type"):
        if column not in result.keys():
            raise RequestQueryError(f"The query must return a column named {column}")
    keys: dict[tuple[str, int], None] = {}
    for row in result.mappings():
        if row["item_type"] not in {"card", "check", "value"}:
            raise RequestQueryError("Request SQL item_type must be card, check or value")
        if not isinstance(row["id"], int):
            raise RequestQueryError("Request SQL id results must be integers")
        keys[(row["item_type"], row["id"])] = None
    return statement, list(keys)


async def normalize_request_query(
    session: AsyncSession, raw: str, views: Collection[str]
) -> str:
    """Reject an invalid Request before its proposal or saved row is written."""
    statement, _keys = await _request_references(session, raw, views)
    return statement


async def request_items(
    session: AsyncSession, query_sql: str, views: Collection[str]
) -> list[Card | Check | Value]:
    """Load each typed reference once, preserving SQL order and skipping deleted items."""
    _statement, keys = await _request_references(session, query_sql, views)
    found: dict[tuple[str, int], Card | Check | Value] = {}
    for item_type, model in (("card", Card), ("check", Check), ("value", Value)):
        ids = [item_id for kind, item_id in keys if kind == item_type]
        if ids:
            items = await session.scalars(select(model).where(model.id.in_(ids)))
            found.update(((item_type, item.id), item) for item in items)
    return [found[key] for key in keys if key in found]


def explicit_order(query_sql: str) -> bool:
    """Whether the query orders its whole result: an ORDER BY outside every parenthesis.

    One inside a subquery, a CTE body, a window or an aggregate orders only that part.
    """
    depth = 0
    for token in SQL_TOKEN.finditer(query_sql):
        if token.group() == "(":
            depth += 1
        elif token.group() == ")":
            depth -= 1
        elif depth == 0 and token.lastgroup == "word" and token.group().casefold() == "order":
            return True
    return False
