"""Validated, SQL-backed saved Card requests."""

from __future__ import annotations

import re
from collections.abc import Collection

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .ai.sql import UnsafeQueryError, validate_read_sql
from .models import Card


class RequestQueryError(ValueError):
    pass


def normalize_request_sql(raw: str, views: Collection[str]) -> str:
    """Validate the stored query is a safe, read-only Card-ID query."""
    if not isinstance(raw, str) or not raw.strip():
        raise RequestQueryError("Request SQL is required")
    try:
        statement = validate_read_sql(raw, views)
    except UnsafeQueryError as error:
        raise RequestQueryError(str(error)) from error
    if "ai_cards" not in statement.casefold():
        raise RequestQueryError("Request SQL must query ai_cards and return Card ids")
    if not re.search(
        r"\bselect\s+(?:distinct\s+)?(?:[a-z_][a-z0-9_]*\.)?id(?:\s+as\s+id)?\b",
        statement,
        re.IGNORECASE,
    ):
        raise RequestQueryError("Request SQL must return a column named id")
    return statement


async def request_cards(
    session: AsyncSession, query_sql: str, views: Collection[str]
) -> list[Card]:
    """Run a saved safe query and load its live Cards in query result order."""
    statement = normalize_request_sql(query_sql, views)
    result = await session.execute(text(statement))
    rows = result.mappings().all()
    if any("id" not in row for row in rows):
        raise RequestQueryError("Request SQL must return a column named id")
    try:
        ids = list(dict.fromkeys(int(row["id"]) for row in rows))
    except (TypeError, ValueError) as error:
        raise RequestQueryError("Request SQL id results must be integers") from error
    if not ids:
        return []
    cards = list(
        await session.scalars(select(Card).where(Card.id.in_(ids), Card.archived_at.is_(None)))
    )
    by_id = {card.id: card for card in cards}
    return [by_id[card_id] for card_id in ids if card_id in by_id]
