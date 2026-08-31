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

from ...ai.sql import RequestQueryError, normalize_request_sql
from ...foundation.errors import DomainError
from ...foundation.workspace import bump_workspace
from .model import SavedRequest


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
        normalized_query = normalize_request_sql(query_sql, views)
    except RequestQueryError as error:
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
            request.query_sql = normalize_request_sql(query_sql, views)
        except RequestQueryError as error:
            raise DomainError(str(error)) from error
    request.version += 1
    await bump_workspace(session)
    return request


async def delete_saved_request(session: AsyncSession, request_id: int) -> SavedRequest:
    request = await session.get(SavedRequest, request_id)
    if request is None:
        raise DomainError("Request does not exist")
    await session.delete(request)
    await bump_workspace(session)
    return request
