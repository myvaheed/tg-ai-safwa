"""The one workspace row: what it is, how it is found, and how its revision moves."""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tg_agent_shell.foundation.errors import DomainError
from tg_agent_shell.foundation.models import Base, TimestampMixin


class WorkspaceMode(StrEnum):
    PLANNING = "planning"
    SPRINT = "sprint"


class Workspace(Base, TimestampMixin):
    __tablename__ = "workspace"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    owner_telegram_id: Mapped[int] = mapped_column(Integer, unique=True)
    mode: Mapped[str] = mapped_column(String(20), default=WorkspaceMode.PLANNING.value)
    active_sprint_id: Mapped[int | None] = mapped_column(ForeignKey("sprints.id"))
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Istanbul")
    # What the next Sprint is meant to achieve, edited during Planning and copied into the
    # Sprint at start. It outlives a Sprint so the next one can start from the last wording.
    sprint_success_criteria: Mapped[str] = mapped_column(Text, default="")
    revision: Mapped[int] = mapped_column(Integer, default=1)


async def require_workspace(session: AsyncSession) -> Workspace:
    workspace = await session.get(Workspace, 1)
    if workspace is None:
        raise DomainError("Workspace is not initialized")
    return workspace


async def bump_workspace(session: AsyncSession) -> Workspace:
    workspace = await require_workspace(session)
    workspace.revision += 1
    return workspace
