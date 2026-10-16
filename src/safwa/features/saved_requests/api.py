"""What another feature may ask of Saved Requests.

One question: which Cards a stored query names right now. Planning asks it to draw the
plan a Request stands for; nothing outside reaches the Request itself.
"""

from __future__ import annotations

from collections.abc import Collection

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.ai.sql import SQL_TOKEN

from ..cards.api import CardQueryError, normalize_card_query
from ..cards.model import Card


async def request_cards(
    session: AsyncSession, query_sql: str, views: Collection[str]
) -> list[Card]:
    """Run a saved safe query and load its live Cards in query result order."""
    statement = await normalize_card_query(session, query_sql, views)
    result = await session.execute(text(statement))
    rows = result.mappings().all()
    try:
        ids = list(dict.fromkeys(int(row["id"]) for row in rows))
    except (TypeError, ValueError) as error:
        raise CardQueryError("Request SQL id results must be integers") from error
    if not ids:
        return []
    cards = list(await session.scalars(select(Card).where(Card.id.in_(ids))))
    by_id = {card.id: card for card in cards}
    return [by_id[card_id] for card_id in ids if card_id in by_id]


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
