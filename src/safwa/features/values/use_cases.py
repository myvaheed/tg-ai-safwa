"""Writing a Value.

A Value is named once and never twice, it comes back when its archived name is written
again, and it is archived rather than deleted. Archiving takes it off every Card and every
Check in the same transaction, so nothing is left pointing at a Value that is not live.
"""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...foundation.errors import DomainError
from ...foundation.workspace import bump_workspace
from .model import CardValue, CheckValue, Value


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
        raise DomainError("A Value with this name already exists")
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
    if value is None:
        raise DomainError("Value does not exist")
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


async def delete_value(session: AsyncSession, value_id: int) -> tuple[Value, int]:
    """Delete a Value and take it off every Card and Check in the same transaction.

    A Value is not archived: the owner keeps a handful, they never finish, and there is
    nothing to declutter. What carried it keeps everything except the link.
    """
    value = await session.get(Value, value_id)
    if value is None:
        raise DomainError("Value does not exist")
    cards, checks = await value_link_counts(session, value.id)
    await session.execute(delete(CardValue).where(CardValue.value_id == value.id))
    await session.execute(delete(CheckValue).where(CheckValue.value_id == value.id))
    await session.delete(value)
    await bump_workspace(session)
    return value, cards + checks


async def set_value_focus(session: AsyncSession, value_id: int, active: bool | None = None) -> Value:
    """Set or flip Value focus through the single Value write path."""
    value = await session.get(Value, value_id)
    if value is None:
        raise DomainError("Value does not exist")
    return await update_value_fields(
        session, value_id, active=(not value.active) if active is None else active
    )
