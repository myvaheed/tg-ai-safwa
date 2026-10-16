"""Writing a Value.

A Value's name is unique without regard to Unicode case. A Value is deleted, never archived:
deleting it takes it off every Card and Check and frees its name in the same transaction.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.foundation.changes import record_change
from tg_agent_shell.foundation.errors import DomainError

from ...constants import DESCRIPTION_MAX_CHARS
from ...enums import ActorType
from ...foundation.log_events import CREATE, DELETE, UPDATE, record_log_event, snapshot
from ...foundation.workspace import bump_workspace
from ..cards.api import Card
from .api import record_unlinked
from .model import CardValue, CheckValue, Value

# The change a hook may follow up on: a Value was created, however it was saved.
VALUE_CREATED = "value.created"


def validate_value_order(order: int, count: int) -> None:
    if type(order) is not int or not 1 <= order <= count:
        raise DomainError(f"Value order must be an integer from 1 to {count}.")


def value_description(text: str) -> str:
    """A description as it is kept: trimmed, never empty, and at most `DESCRIPTION_MAX_CHARS`
    long."""
    described = text.strip()
    if not described:
        raise DomainError("A Value needs a description: why it matters and what you do for it.")
    if len(described) > DESCRIPTION_MAX_CHARS:
        raise DomainError(
            f"Value description holds at most {DESCRIPTION_MAX_CHARS} characters; "
            f"this one has {len(described)}."
        )
    return described


async def create_value(
    session: AsyncSession,
    name: str,
    description: str,
    *,
    order: int | None = None,
    actor: ActorType = ActorType.USER_UI,
) -> Value:
    normalized = name.strip()
    if not normalized:
        raise DomainError("Value name cannot be empty")
    existing = await session.scalar(select(Value).where(Value.name == normalized))
    if existing is not None:
        raise DomainError("A Value with this name already exists")
    count = await session.scalar(select(func.count()).select_from(Value))
    if order is not None:
        validate_value_order(order, count + 1)
    value = Value(name=normalized, description=value_description(description), order=count + 1)
    session.add(value)
    await session.flush()
    if order is not None:
        await _move_value(session, value, order, actor)
    await _record(session, value, CREATE, actor)
    record_change(session, VALUE_CREATED, value.id)
    await bump_workspace(session)
    return value


async def update_value_fields(
    session: AsyncSession,
    value_id: int,
    *,
    name: str | None = None,
    description: str | None = None,
    order: int | None = None,
    actor: ActorType = ActorType.USER_UI,
) -> Value:
    value = await session.get(Value, value_id)
    if value is None:
        raise DomainError("Value does not exist")
    if order is not None:
        validate_value_order(order, await session.scalar(select(func.count()).select_from(Value)))
    before = snapshot(value)
    if name is not None:
        normalized = name.strip()
        if not normalized:
            raise DomainError("Value name cannot be empty")
        duplicate = await session.scalar(
            select(Value).where(
                Value.name == normalized,
                Value.id != value.id,
            )
        )
        if duplicate is not None:
            raise DomainError("A Value with this name already exists")
        value.name = normalized
    if description is not None:
        value.description = value_description(description)
    if order is not None:
        await _move_value(session, value, order, actor)
    value.version += 1
    await _record(session, value, UPDATE, actor, before)
    await bump_workspace(session)
    return value


async def value_cards(session: AsyncSession, value_id: int) -> list[Card]:
    """Every Card carrying this Value."""
    cards = await session.scalars(
        select(Card).join(CardValue, CardValue.card_id == Card.id).where(
            CardValue.value_id == value_id
        )
    )
    return list(cards)


async def value_link_counts(session: AsyncSession, value_id: int) -> tuple[int, int]:
    """How many Cards and how many Checks carry this Value right now."""
    cards = list(await session.scalars(select(CardValue.card_id).where(CardValue.value_id == value_id)))
    checks = list(
        await session.scalars(select(CheckValue.check_id).where(CheckValue.value_id == value_id))
    )
    return len(cards), len(checks)


async def delete_value(
    session: AsyncSession, value_id: int, *, actor: ActorType = ActorType.USER_UI
) -> tuple[Value, int]:
    """Delete a Value and take it off every Card and Check in the same transaction.

    A Value is not archived: the owner keeps a handful, they never finish, and there is
    nothing to declutter. What carried it keeps everything except the link.
    """
    value = await session.get(Value, value_id)
    if value is None:
        raise DomainError("Value does not exist")
    items = [
        {"type": "card", "id": card_id}
        for card_id in await session.scalars(
            select(CardValue.card_id).where(CardValue.value_id == value.id).order_by(CardValue.card_id)
        )
    ] + [
        {"type": "check", "id": check_id}
        for check_id in await session.scalars(
            select(CheckValue.check_id)
            .where(CheckValue.value_id == value.id)
            .order_by(CheckValue.check_id)
        )
    ]
    await _record(session, value, DELETE, actor, snapshot(value))
    await session.execute(delete(CardValue).where(CardValue.value_id == value.id))
    await session.execute(delete(CheckValue).where(CheckValue.value_id == value.id))
    record_unlinked(session, value, items)
    await session.delete(value)
    await session.flush()
    following = list(
        await session.scalars(select(Value).where(Value.order > value.order).order_by(Value.order))
    )
    await _shift_orders(session, following, -1, actor)
    await bump_workspace(session)
    return value, len(items)


async def _move_value(
    session: AsyncSession, value: Value, order: int, actor: ActorType
) -> None:
    previous = value.order
    if order == previous:
        return
    # Open a gap before shifting, so SQLite's immediate unique constraint always holds.
    value.order = 0
    await session.flush()
    upward = order < previous
    following = list(
        await session.scalars(
            select(Value)
            .where(Value.order.between(min(previous, order), max(previous, order)))
            .order_by(Value.order.desc() if upward else Value.order)
        )
    )
    await _shift_orders(session, following, 1 if upward else -1, actor)
    value.order = order


async def _shift_orders(
    session: AsyncSession, values: list[Value], step: int, actor: ActorType
) -> None:
    for value in values:
        before = snapshot(value)
        value.order += step
        value.version += 1
        await session.flush()
        await _record(session, value, UPDATE, actor, before)


async def _record(
    session: AsyncSession,
    value: Value,
    operation: str,
    actor: ActorType,
    before: dict[str, Any] | None = None,
) -> None:
    await record_log_event(session, "value", value, value.name, operation, actor, before)
