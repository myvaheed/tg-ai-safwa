"""What a Check is called, for a feature that has to name one.

A door carries the vocabulary and the reads that need no operation. Answering a Check,
letting go of one and sweeping the archive are operations, and they are asked for at the
operations layer, in `use_cases.py`.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .model import Check as Check
from .model import CheckOutcome as CheckOutcome
from .model import is_closed_repeat as is_closed_repeat


async def live_repeat_instance_id(session: AsyncSession, check: Check) -> int | None:
    """The open Check in this repeat series, or None when the series has ended."""
    return await session.scalar(
        select(Check.id)
        .where(
            Check.series_id == (check.series_id or check.id),
            Check.outcome.is_(None),
        )
        .order_by(Check.id.desc())
        .limit(1)
    )
