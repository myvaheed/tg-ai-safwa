"""What a title carries after it, for the two entities that are archived.

A Card and a Check are the only things that leave sight rather than being deleted, and
both do it in repeat series. Neither feature owns the vocabulary alone, and neither may
import the other, so it lives here.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..constants import ARCHIVE_MARKER, REPEAT_LIVE, REPEAT_MARKER
from ..features.cards.model import TERMINAL_STAGES, Card
from ..features.cards.model import is_closed_repeat as _is_closed_repeat_card
from ..features.checks.model import Check
from ..features.checks.model import is_closed_repeat as _is_closed_repeat_check


def is_closed_repeat(entity: Card | Check) -> bool:
    """A repeat instance that already ended, so its series continues on a newer row.

    Editing one is almost always aimed at the live instance instead, and the edit
    would not reach it: the successor was copied at close time.
    """
    if isinstance(entity, Check):
        return _is_closed_repeat_check(entity)
    return _is_closed_repeat_card(entity)


async def live_repeat_instance_id(session: AsyncSession, entity: Card | Check) -> int | None:
    """The one open row of a repeat series, or None when the series has ended.

    Only the newest instance can be open, because closing one is what creates the next.
    """
    if isinstance(entity, Check):
        statement = (
            select(Check.id)
            .where(
                Check.series_id == (entity.series_id or entity.id),
                Check.outcome.is_(None),
            )
            .order_by(Check.id.desc())
        )
    else:
        statement = (
            select(Card.id)
            .where(
                Card.repeat_series_id == (entity.repeat_series_id or entity.id),
                Card.effective_stage.notin_([stage.value for stage in TERMINAL_STAGES]),
            )
            .order_by(Card.id.desc())
        )
    return await session.scalar(statement.limit(1))


async def title_marks(session: AsyncSession, entity: Card | Check) -> str:
    """What a title carries after it: its place in a repeat series, and the archive.

    The place is counted over every row the series has ever had, so archiving one does not
    renumber the others, and `live #7` is the open one — a closed instance read as the one
    to work with is the mistake both marks exist to stop.  Nothing is stored renamed:
    `ai_cards` and `ai_checks` render the same marks in SQL.
    """
    marks = ""
    if is_closed_repeat(entity):
        if isinstance(entity, Check):
            statement = (
                select(func.count())
                .select_from(Check)
                .where(Check.series_id == (entity.series_id or entity.id), Check.id <= entity.id)
            )
        else:
            statement = (
                select(func.count())
                .select_from(Card)
                .where(
                    Card.repeat_series_id == (entity.repeat_series_id or entity.id),
                    Card.id <= entity.id,
                )
            )
        live_id = await live_repeat_instance_id(session, entity)
        marks += REPEAT_MARKER.format(
            index=await session.scalar(statement),
            live="" if live_id is None else REPEAT_LIVE.format(live_id=live_id),
        )
    if entity.archived_at is not None:
        marks += ARCHIVE_MARKER
    return marks
