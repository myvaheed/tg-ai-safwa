"""The operations an approved proposal calls, which are the ones a screen would call too."""

from __future__ import annotations

from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.foundation.errors import DomainError

from ..wallets.model import Category, Wallet
from ..world import bump_ledger
from .model import Entry

MAX_NOTE_LENGTH = 200


def _amount(amount_minor: int) -> int:
    if amount_minor <= 0:
        raise DomainError("An amount is a positive number of minor units")
    return amount_minor


def _note(note: str) -> str:
    text = note.strip()
    if len(text) > MAX_NOTE_LENGTH:
        raise DomainError(f"A note is at most {MAX_NOTE_LENGTH} characters")
    return text


async def _resolve(session: AsyncSession, wallet_id: int, category_id: int) -> None:
    if await session.get(Wallet, wallet_id) is None:
        raise DomainError(f"Wallet #{wallet_id} does not exist")
    if await session.get(Category, category_id) is None:
        raise DomainError(f"Category #{category_id} does not exist")


async def record_entry(
    session: AsyncSession,
    *,
    wallet_id: int,
    category_id: int,
    amount_minor: int,
    happened_on: date,
    note: str = "",
) -> Entry:
    await _resolve(session, wallet_id, category_id)
    entry = Entry(
        wallet_id=wallet_id,
        category_id=category_id,
        amount_minor=_amount(amount_minor),
        happened_on=happened_on,
        note=_note(note),
    )
    session.add(entry)
    await session.flush()
    await bump_ledger(session)
    return entry


async def update_entry(
    session: AsyncSession,
    entry_id: int,
    *,
    wallet_id: int,
    category_id: int,
    amount_minor: int,
    happened_on: date,
    note: str = "",
) -> Entry:
    """Replace one line whole. A patch would leave the review screen lying about the result."""
    entry = await session.get(Entry, entry_id)
    if entry is None:
        raise DomainError("Entry does not exist")
    await _resolve(session, wallet_id, category_id)
    entry.wallet_id = wallet_id
    entry.category_id = category_id
    entry.amount_minor = _amount(amount_minor)
    entry.happened_on = happened_on
    entry.note = _note(note)
    entry.version += 1
    await bump_ledger(session)
    return entry


async def delete_entry(session: AsyncSession, entry_id: int) -> None:
    entry = await session.get(Entry, entry_id)
    if entry is None:
        raise DomainError("Entry does not exist")
    await session.delete(entry)
    await bump_ledger(session)
