"""What another feature may ask about a Tag: an answer, never a row, and the method for one."""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...foundation.method import Example, Method
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


# Advice, not a rule: no check reads it, and a Tag the owner wants is theirs to keep. Its
# definition is the one line every prompt that writes items already reads.
TAG = Method(
    name="Tag",
    definition="Tag: a free label for finding things.",
    criteria=(
        "finds Cards by what none of their own fields says: a place, a means, a person or a "
        "context, such as Home, Phone or Anna;",
        "does not repeat a Value, a Goal, a stage, a Priority, a Category, an Energy type, "
        "a Deadline or Blocked, which already find Cards;",
        "fits Cards under different Goals; the Cards of one Goal are found by that Goal.",
    ),
    leaves=("This is advice, not a rule: suggest it, and keep the Tag the user wants.",),
    writing=(),
    examples=(
        Example('"📍 Home", "What I do at home"', clear=True),
        Example('"📞 Calls", "What I do by phone, in one go"', clear=True),
        Example('"👩 Anna", "What I do with or for Anna"', clear=True),
        Example('"Work"', clear=False, verdict="The Category Work already finds these."),
        Example('"Health"', clear=False, verdict="The Value Health already finds these."),
        Example('"Urgent"', clear=False, verdict="Priority or a Deadline already says it."),
        Example('"Physical"', clear=False, verdict="The Energy type Physical already finds these."),
        Example(
            '"Marathon", on the Actions under Goal "Run a half marathon"',
            clear=False,
            verdict="The Goal already groups them.",
        ),
    ),
)
