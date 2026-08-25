"""What another feature may ask about a Tag: an answer, never a row."""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .model import CardTag, Tag


async def unlinkable_tag_id(session: AsyncSession, tag_ids: Iterable[int]) -> int | None:
    """The first id that cannot be linked, because no live Tag has it."""
    wanted = set(tag_ids)
    if not wanted:
        return None
    live = set(
        await session.scalars(select(Tag.id).where(Tag.id.in_(wanted)))
    )
    return next((tag_id for tag_id in sorted(wanted) if tag_id not in live), None)


async def attach_tags(session: AsyncSession, card_id: int, tag_ids: Iterable[int]) -> None:
    """Put a Card's Tag links in place. The Card owns the operation, this row is ours."""
    for tag_id in sorted(set(tag_ids)):
        session.add(CardTag(card_id=card_id, tag_id=tag_id))
