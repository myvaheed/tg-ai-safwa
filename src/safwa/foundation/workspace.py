"""Workspace lookup and revision mechanics shared by feature operations."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from .errors import DomainError
from .models import Workspace


async def require_workspace(session: AsyncSession) -> Workspace:
    workspace = await session.get(Workspace, 1)
    if workspace is None:
        raise DomainError("Workspace is not initialized")
    return workspace


async def bump_workspace(session: AsyncSession) -> Workspace:
    workspace = await require_workspace(session)
    workspace.revision += 1
    return workspace
