"""Writing a Tag.

A Tag is named once and never twice, it comes back when its archived name is written
again, and it is archived rather than deleted. Archiving takes it off every Card in the
same transaction.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.foundation.changes import record_change
from tg_agent_shell.foundation.errors import DomainError

from ...enums import ActorType
from ...foundation.log_events import CREATE, DELETE, UPDATE, record_log_event, snapshot
from ...foundation.workspace import bump_workspace
from .model import CardTag, Tag

# The change a hook may follow up on: a Tag was created, however it was saved.
TAG_CREATED = "tag.created"


async def create_tag(
    session: AsyncSession,
    name: str,
    description: str | None = None,
    *,
    actor: ActorType = ActorType.USER_UI,
) -> Tag:
    normalized = name.strip()
    if not normalized:
        raise DomainError("Tag name cannot be empty")
    existing = await session.scalar(select(Tag).where(Tag.name.collate("NOCASE") == normalized))
    if existing is not None:
        raise DomainError("A Tag with this name already exists")
    tag = Tag(name=normalized, description=(description or "").strip())
    session.add(tag)
    await session.flush()
    await _record(session, tag, CREATE, actor)
    record_change(session, TAG_CREATED, tag.id)
    await bump_workspace(session)
    return tag


async def update_tag_fields(
    session: AsyncSession,
    tag_id: int,
    *,
    name: str | None = None,
    description: str | None = None,
    actor: ActorType = ActorType.USER_UI,
) -> Tag:
    tag = await session.get(Tag, tag_id)
    if tag is None:
        raise DomainError("Tag does not exist")
    before = snapshot(tag)
    if name is not None:
        normalized = name.strip()
        if not normalized:
            raise DomainError("Tag name cannot be empty")
        duplicate = await session.scalar(
            select(Tag).where(
                Tag.name.collate("NOCASE") == normalized,
                Tag.id != tag.id,
            )
        )
        if duplicate is not None:
            raise DomainError("A Tag with this name already exists")
        tag.name = normalized
    if description is not None:
        tag.description = description.strip()
    tag.version += 1
    await _record(session, tag, UPDATE, actor, before)
    await bump_workspace(session)
    return tag


async def _record(
    session: AsyncSession,
    tag: Tag,
    operation: str,
    actor: ActorType,
    before: dict[str, Any] | None = None,
) -> None:
    await record_log_event(session, "tag", tag, tag.name, operation, actor, before)


async def tag_link_count(session: AsyncSession, tag_id: int) -> int:
    """How many Cards carry this Tag right now."""
    return len(list(await session.scalars(select(CardTag.card_id).where(CardTag.tag_id == tag_id))))


async def delete_tag(
    session: AsyncSession, tag_id: int, *, actor: ActorType = ActorType.USER_UI
) -> tuple[Tag, int]:
    """Delete a Tag and take it off every Card in the same transaction."""
    tag = await session.get(Tag, tag_id)
    if tag is None:
        raise DomainError("Tag does not exist")
    linked_count = await tag_link_count(session, tag.id)
    await _record(session, tag, DELETE, actor, snapshot(tag))
    await session.execute(delete(CardTag).where(CardTag.tag_id == tag.id))
    await session.delete(tag)
    await bump_workspace(session)
    return tag, linked_count
