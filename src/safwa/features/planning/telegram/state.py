"""Which Backlog Actions the plan's picked Requests narrow it to."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from ...saved_requests.api import request_cards
from ...saved_requests.model import SavedRequest


async def resolve_filters(
    session: AsyncSession, filters: list[int], views: frozenset[str]
) -> tuple[list[int], set[int] | None]:
    """The picked Requests that still exist, and the Card ids all of them return.

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
            card.id for card in await request_cards(session, request.query_sql, views)
        }
        matched = ids if matched is None else matched & ids
    return live, matched

