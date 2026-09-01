"""What another feature may ask of Saved Requests.

One question: which Cards a stored query names right now. Planning asks it to draw the
plan a Request stands for; nothing outside reaches the Request itself.
"""

from __future__ import annotations

from collections.abc import Collection

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ...ai.sql import RequestQueryError, normalize_request_sql
from ..cards.model import Card


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
    cards = list(await session.scalars(select(Card).where(Card.id.in_(ids))))
    by_id = {card.id: card for card in cards}
    return [by_id[card_id] for card_id in ids if card_id in by_id]
