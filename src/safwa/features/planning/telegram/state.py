"""Which Backlog Actions the plan's picked Requests narrow it to."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from ...cards.model import Card, CardKind
from ...saved_requests.api import request_items
from ...saved_requests.model import SavedRequest


async def resolve_filters(
    session: AsyncSession, filters: list[int], views: frozenset[str]
) -> tuple[list[int], set[int] | None]:
    """The picked Requests that still exist, and the Action ids all of them return.

    None is "nothing is picked", which is not the same as an empty intersection.
    """
    live: list[int] = []
    matched: set[int] | None = None
    for request_id in filters:
        request = await session.get(SavedRequest, request_id)
        if request is None:
            continue
        live.append(request_id)
        ids = {
            item.id for item in await request_items(session, request.query_sql, views)
            if isinstance(item, Card) and item.kind == CardKind.ACTION.value
        }
        matched = ids if matched is None else matched & ids
    return live, matched

