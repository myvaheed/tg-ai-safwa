"""What the plan screen remembers between taps.

A deep-link payload is 64 characters of `[A-Za-z0-9_-]` and cannot carry the page or the
picked Requests, so they live in one `UiSession` row that every tap reads and rewrites.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ....enums import MessageKind
from ....models import SavedRequest, TelegramMessage, UiSession
from ...saved_requests.use_cases import request_cards

PLAN_UI_KIND = "sprint_plan"
_PLAN_TTL = timedelta(hours=24)


def plan_back(state: dict[str, Any]) -> dict[str, Any]:
    """What a Card opened from the plan has to be handed to come back to it."""
    return {
        "kind": PLAN_UI_KIND,
        "page": int(state.get("page", 0)),
        "filters": list(state.get("filters", [])),
    }


async def load_plan_state(session: AsyncSession, owner_id: int) -> dict[str, Any]:
    ui = await session.scalar(
        select(UiSession).where(
            UiSession.owner_id == owner_id, UiSession.kind == PLAN_UI_KIND
        )
    )
    return dict(ui.state) if ui is not None else {}


async def store_state(session: AsyncSession, owner_id: int, state: dict[str, Any]) -> None:
    await session.execute(delete(UiSession).where(UiSession.owner_id == owner_id))
    session.add(
        UiSession(
            owner_id=owner_id,
            kind=PLAN_UI_KIND,
            state=state,
            expires_at=datetime.now(UTC) + _PLAN_TTL,
        )
    )


async def plan_screen_id(session: AsyncSession, chat_id: int, state: dict[str, Any]) -> int | None:
    """The plan screen this tap belongs to, or None when it is no longer in the chat."""
    message_id = state.get("message_id")
    if message_id is None:
        return None
    known = await session.scalar(
        select(TelegramMessage.message_id).where(
            TelegramMessage.chat_id == chat_id,
            TelegramMessage.message_id == int(message_id),
            TelegramMessage.kind == MessageKind.DASHBOARD.value,
        )
    )
    return known


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

