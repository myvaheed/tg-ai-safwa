"""What another feature may ask about a Value: an answer, never a row."""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.foundation.changes import record_change

from .model import CardValue, Value
from .model import CheckValue as CheckValue

CARD_VALUE_LINKED = "card.value_linked"
CHECK_VALUE_LINKED = "check.value_linked"

VALUE_LINK_MEANING = (
    "Link a Value to a Goal, Action or Check only when the item supports the Value.\n"
    "Do not link just because the Value is needed to complete the item."
)


async def values_in_focus(session: AsyncSession) -> list[Value]:
    """The Values the owner put in focus, by name."""
    return list(
        await session.scalars(select(Value).where(Value.active.is_(True)).order_by(Value.name))
    )


async def unlinkable_value_id(session: AsyncSession, value_ids: Iterable[int]) -> int | None:
    """The first id that cannot be linked, because no live Value has it."""
    wanted = set(value_ids)
    if not wanted:
        return None
    live = set(
        await session.scalars(
            select(Value.id).where(Value.id.in_(wanted))
        )
    )
    return next((value_id for value_id in sorted(wanted) if value_id not in live), None)


async def attach_values(session: AsyncSession, card_id: int, value_ids: Iterable[int]) -> None:
    """Put a Card's Value links in place. The Card owns the operation, this row is ours."""
    for value_id in sorted(set(value_ids)):
        session.add(CardValue(card_id=card_id, value_id=value_id))
        record_change(session, CARD_VALUE_LINKED, card_id, data={"value_id": value_id})


async def value_names(session: AsyncSession) -> dict[int, str]:
    """Every Value's name by its id."""
    return {
        value_id: name
        for value_id, name in await session.execute(select(Value.id, Value.name))
    }
