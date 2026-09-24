"""The Request writes, and the one read that matters: running a saved query.

A Request is validated when it is written *and* again every time it is run, because the
row is the only thing standing between a stored statement and the database. What it
returns is a list on a screen and never enters the model's history, so the query is bound
by nothing but being valid — the caps in `ReadOnlyQueryRunner` exist for what a model reads.
"""

from __future__ import annotations

from collections.abc import Collection

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.foundation.changes import record_change
from tg_agent_shell.foundation.errors import DomainError

from ...foundation.workspace import bump_workspace
from ..cards.api import CardQueryError, normalize_card_query
from .model import SavedRequest

# The change a hook may follow up on: a Request was created, however it was saved.
REQUEST_CREATED = "request.created"

# What a new workspace starts with, so the owner sees what a Request is before
# asking for one. It is an ordinary Request from the moment it exists: renaming,
# re-aiming and deleting it work as they do for any other.
DEFAULT_REQUESTS: tuple[tuple[str, str, str], ...] = (
    (
        "Все цели",
        "Каждая Цель, включая архивные.",
        "SELECT id FROM ai_cards WHERE kind = 'goal' ORDER BY title",
    ),
)


async def create_saved_request(
    session: AsyncSession,
    name: str,
    query_sql: str,
    description: str | None = None,
    *,
    views: Collection[str],
) -> SavedRequest:
    normalized_name = name.strip()
    if not normalized_name:
        raise DomainError("Request name cannot be empty")
    try:
        normalized_query = await normalize_card_query(session, query_sql, views)
    except CardQueryError as error:
        raise DomainError(str(error)) from error
    existing = await session.scalar(
        select(SavedRequest).where(SavedRequest.name.collate("NOCASE") == normalized_name)
    )
    if existing is not None:
        raise DomainError("A Request with this name already exists")
    request = SavedRequest(
        name=normalized_name,
        description=(description or "").strip(),
        query_sql=normalized_query,
    )
    session.add(request)
    await session.flush()
    record_change(session, REQUEST_CREATED, request.id)
    await bump_workspace(session)
    return request


async def update_saved_request(
    session: AsyncSession,
    request_id: int,
    *,
    name: str | None = None,
    description: str | None = None,
    query_sql: str | None = None,
    views: Collection[str],
) -> SavedRequest:
    request = await session.get(SavedRequest, request_id)
    if request is None:
        raise DomainError("Request does not exist")
    if name is not None:
        normalized_name = name.strip()
        if not normalized_name:
            raise DomainError("Request name cannot be empty")
        duplicate = await session.scalar(
            select(SavedRequest).where(
                SavedRequest.name.collate("NOCASE") == normalized_name,
                SavedRequest.id != request.id,
            )
        )
        if duplicate is not None:
            raise DomainError("A Request with this name already exists")
        request.name = normalized_name
    if description is not None:
        request.description = description.strip()
    if query_sql is not None:
        try:
            request.query_sql = await normalize_card_query(session, query_sql, views)
        except CardQueryError as error:
            raise DomainError(str(error)) from error
    request.version += 1
    await bump_workspace(session)
    return request


async def seed_default_requests(
    session: AsyncSession, *, views: Collection[str]
) -> list[SavedRequest]:
    """Write the Request a brand new workspace starts with.

    Called once, when the workspace row is created, so a Request the owner deletes
    stays deleted rather than coming back on the next start.
    """
    return [
        await create_saved_request(session, name, query_sql, description, views=views)
        for name, description, query_sql in DEFAULT_REQUESTS
    ]


async def delete_saved_request(session: AsyncSession, request_id: int) -> SavedRequest:
    request = await session.get(SavedRequest, request_id)
    if request is None:
        raise DomainError("Request does not exist")
    await session.delete(request)
    await bump_workspace(session)
    return request
