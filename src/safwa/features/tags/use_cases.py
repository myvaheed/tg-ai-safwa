"""Writing a Tag.

A Tag is named once and never twice, it comes back when its archived name is written
again, and it is archived rather than deleted. Archiving takes it off every Card in the
same transaction.
"""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...foundation.errors import DomainError
from ...foundation.workspace import bump_workspace
from .model import CardTag, Tag


async def create_tag(session: AsyncSession, name: str, description: str | None = None) -> Tag:
    normalized = name.strip()
    if not normalized:
        raise DomainError("Tag name cannot be empty")
    existing = await session.scalar(select(Tag).where(Tag.name.collate("NOCASE") == normalized))
    if existing is not None:
        raise DomainError("A Tag with this name already exists")
    tag = Tag(name=normalized, description=(description or "").strip())
    session.add(tag)
    await bump_workspace(session)
    return tag


async def update_tag_fields(
    session: AsyncSession,
    tag_id: int,
    *,
    name: str | None = None,
    description: str | None = None,
) -> Tag:
    tag = await session.get(Tag, tag_id)
    if tag is None:
        raise DomainError("Tag does not exist")
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
    await bump_workspace(session)
    return tag


async def delete_tag(session: AsyncSession, tag_id: int) -> tuple[Tag, int]:
    """Delete a Tag and take it off every Card in the same transaction."""
    tag = await session.get(Tag, tag_id)
    if tag is None:
        raise DomainError("Tag does not exist")
    linked_count = len(
        list(await session.scalars(select(CardTag.card_id).where(CardTag.tag_id == tag.id)))
    )
    await session.execute(delete(CardTag).where(CardTag.tag_id == tag.id))
    await session.delete(tag)
    await bump_workspace(session)
    return tag, linked_count
