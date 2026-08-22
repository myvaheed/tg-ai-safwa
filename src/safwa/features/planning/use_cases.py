"""Writing a Value or a Tag.

Both are named once and never twice, both come back when their archived name is written
again, and both are archived rather than deleted. Archiving takes the classification off
everything carrying it in the same transaction, so nothing is left pointing at a Value or
Tag that is no longer live.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...foundation.errors import DomainError
from ...foundation.workspace import bump_workspace
from .model import CardTag, CardValue, CheckValue, Tag, Value


async def create_tag(session: AsyncSession, name: str, description: str | None = None) -> Tag:
    normalized = name.strip()
    if not normalized:
        raise DomainError("Tag name cannot be empty")
    existing = await session.scalar(select(Tag).where(Tag.name.collate("NOCASE") == normalized))
    if existing is not None:
        if existing.archived_at is None:
            raise DomainError("A Tag with this name already exists")
        existing.archived_at = None
        if description is not None:
            existing.description = description.strip()
        existing.version += 1
        await bump_workspace(session)
        return existing
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
    if tag is None or tag.archived_at is not None:
        raise DomainError("Tag does not exist or is archived")
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


async def archive_tag(session: AsyncSession, tag_id: int) -> tuple[Tag, int]:
    """Archive a Tag and take it off every Card in the same transaction."""
    tag = await session.get(Tag, tag_id)
    if tag is None or tag.archived_at is not None:
        raise DomainError("Tag does not exist or is archived")
    linked_count = len(
        list(await session.scalars(select(CardTag.card_id).where(CardTag.tag_id == tag.id)))
    )
    await session.execute(delete(CardTag).where(CardTag.tag_id == tag.id))
    tag.archived_at = datetime.now(UTC)
    tag.version += 1
    await bump_workspace(session)
    return tag, linked_count


async def create_value(
    session: AsyncSession,
    name: str,
    description: str | None = None,
    *,
    active: bool | None = None,
) -> Value:
    normalized = name.strip()
    if not normalized:
        raise DomainError("Value name cannot be empty")
    existing = await session.scalar(select(Value).where(Value.name.collate("NOCASE") == normalized))
    if existing is not None:
        if existing.archived_at is None:
            raise DomainError("A Value with this name already exists")
        existing.archived_at = None
        if description is not None:
            existing.description = description.strip()
        if active is not None:
            existing.active = active
        existing.version += 1
        await bump_workspace(session)
        return existing
    value = Value(
        name=normalized,
        description=(description or "").strip(),
        active=bool(active),
    )
    session.add(value)
    await bump_workspace(session)
    return value


async def update_value_fields(
    session: AsyncSession,
    value_id: int,
    *,
    name: str | None = None,
    description: str | None = None,
    active: bool | None = None,
) -> Value:
    value = await session.get(Value, value_id)
    if value is None or value.archived_at is not None:
        raise DomainError("Value does not exist or is archived")
    if name is not None:
        normalized = name.strip()
        if not normalized:
            raise DomainError("Value name cannot be empty")
        duplicate = await session.scalar(
            select(Value).where(
                Value.name.collate("NOCASE") == normalized,
                Value.id != value.id,
            )
        )
        if duplicate is not None:
            raise DomainError("A Value with this name already exists")
        value.name = normalized
    if description is not None:
        value.description = description.strip()
    if active is not None:
        value.active = active
    value.version += 1
    await bump_workspace(session)
    return value


async def value_link_counts(session: AsyncSession, value_id: int) -> tuple[int, int]:
    """How many Cards and how many Checks carry this Value right now."""
    cards = list(await session.scalars(select(CardValue.card_id).where(CardValue.value_id == value_id)))
    checks = list(
        await session.scalars(select(CheckValue.check_id).where(CheckValue.value_id == value_id))
    )
    return len(cards), len(checks)


async def archive_value(session: AsyncSession, value_id: int) -> tuple[Value, int]:
    """Archive a Value, take it off every Card and Check, and drop its focus."""
    value = await session.get(Value, value_id)
    if value is None or value.archived_at is not None:
        raise DomainError("Value does not exist or is archived")
    cards, checks = await value_link_counts(session, value.id)
    await session.execute(delete(CardValue).where(CardValue.value_id == value.id))
    await session.execute(delete(CheckValue).where(CheckValue.value_id == value.id))
    value.active = False
    value.archived_at = datetime.now(UTC)
    value.version += 1
    await bump_workspace(session)
    return value, cards + checks


async def set_value_focus(session: AsyncSession, value_id: int, active: bool | None = None) -> Value:
    """Set or flip Value focus through the single Value write path."""
    value = await session.get(Value, value_id)
    if value is None or value.archived_at is not None:
        raise DomainError("Value does not exist or is archived")
    return await update_value_fields(
        session, value_id, active=(not value.active) if active is None else active
    )
