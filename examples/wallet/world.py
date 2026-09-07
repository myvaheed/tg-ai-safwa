"""What a proposal is made against here: one row, a revision and a timezone.

The shell asks for a `World` and nothing else — not a table, not a name. Safwa answers with
its workspace row; this application answers with a row of its own, which is the whole of
what "a second application" has to prove about this contract.
"""

from __future__ import annotations

from sqlalchemy import Integer, String
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tg_agent_shell.foundation.errors import DomainError
from tg_agent_shell.proposals.api import World

from .models import Base, TimestampMixin


class Ledger(Base, TimestampMixin):
    """The single row this application is bound to."""

    __tablename__ = "ledger"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    owner_telegram_id: Mapped[int] = mapped_column(Integer, unique=True)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    # Moved by every write the model may propose against, so a review prepared before one
    # is refused after it.
    revision: Mapped[int] = mapped_column(Integer, default=1)


async def require_ledger(session: AsyncSession) -> Ledger:
    ledger = await session.get(Ledger, 1)
    if ledger is None:
        raise DomainError("The ledger is not initialised")
    return ledger


async def bump_ledger(session: AsyncSession) -> Ledger:
    ledger = await require_ledger(session)
    ledger.revision += 1
    return ledger


async def bootstrap_ledger(session: AsyncSession, owner_id: int, timezone: str) -> Ledger:
    """Bind the database to its owner, once, before the first message is taken."""
    ledger = await session.get(Ledger, 1)
    if ledger is None:
        ledger = Ledger(id=1, owner_telegram_id=owner_id, timezone=timezone)
        session.add(ledger)
    elif ledger.owner_telegram_id != owner_id:
        raise DomainError("This ledger is already bound to another Telegram owner")
    await session.flush()
    return ledger


async def ledger_world(session: AsyncSession) -> World:
    ledger = await require_ledger(session)
    return World(revision=ledger.revision, timezone=ledger.timezone)
