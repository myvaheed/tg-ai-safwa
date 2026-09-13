"""What a repeat series means to whoever reads it: its marks, and what it refuses.

A Card and a Check are the only things that repeat, and the only things that leave sight
rather than being deleted. Which rows are one series, and which one of them is still open,
is the entity's own answer — this layer names no feature and asks the entity instead.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession

# One wording, in two renderings: `title_marks` builds a title in Python and the `ai_*`
# views build one in SQL, so a row reads the same whether the model queried it or the
# owner tapped a citation.
REPEAT_MARKER = " [🔄{index}{live}]"
REPEAT_LIVE = ", live #{live_id}"
ARCHIVE_MARKER = " [📦]"
# The one mark SQL does not mirror: today is the owner's calendar day in the workspace
# timezone, and SQLite has no timezone database to work it out with. So it is written
# where a screen renders a title and nowhere else, and the model is told nothing of it.
REPEAT_TODAY_MARKER = " [🔄✓]"
MARKER_FORMAT = REPEAT_MARKER.replace("{index}", "%d").replace("{live}", "%s")
LIVE_FORMAT = REPEAT_LIVE.replace("{live_id}", "%d")


class RepeatSeries(Protocol):
    """An entity whose instances close one after another, each copied from the last."""

    id: int
    archived_at: datetime | None

    def is_closed_repeat(self) -> bool: ...

    def live_instance_query(self) -> Select[tuple[int]]: ...

    def series_index_query(self) -> Select[tuple[int]]: ...


async def live_repeat_instance_id(session: AsyncSession, entity: RepeatSeries) -> int | None:
    """The one open row of a repeat series, or None when the series has ended."""
    return await session.scalar(entity.live_instance_query())


async def live_instance_hint(session: AsyncSession, entity: RepeatSeries) -> str:
    """Where to retry a call that reached a closed instance."""
    live_id = await live_repeat_instance_id(session, entity)
    if live_id is None:
        return "The series has ended. Tell the owner instead of proposing again."
    return f"Retry this call with #{live_id}, the open one in its series."


async def closed_repeat_refusal(
    session: AsyncSession, entity: RepeatSeries, label: str
) -> tuple[str, str, str] | None:
    """Why a closed instance may not be changed, or None when it is not one.

    Editing one is almost always aimed at the live instance instead, and the edit would
    not reach it: the successor was copied at close time.
    """
    if not entity.is_closed_repeat():
        return None
    return (
        "closed_repeat",
        f"{label.title()} #{entity.id} is a closed repeat and cannot be changed.",
        await live_instance_hint(session, entity),
    )


async def title_marks(session: AsyncSession, entity: RepeatSeries) -> str:
    """What a title carries after it: its place in a repeat series, and the archive.

    The place is counted over every row the series has ever had, so archiving one does not
    renumber the others, and `live #7` is the open one — a closed instance read as the one
    to work with is the mistake both marks exist to stop.  Nothing is stored renamed:
    `ai_cards` and `ai_checks` render the same marks in SQL.
    """
    marks = ""
    if entity.is_closed_repeat():
        live_id = await live_repeat_instance_id(session, entity)
        marks += REPEAT_MARKER.format(
            index=await session.scalar(entity.series_index_query()),
            live="" if live_id is None else REPEAT_LIVE.format(live_id=live_id),
        )
    if entity.archived_at is not None:
        marks += ARCHIVE_MARKER
    return marks
