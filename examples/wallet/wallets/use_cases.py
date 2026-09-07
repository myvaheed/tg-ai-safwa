"""Everything that changes a wallet or a category, called by the screens and by nothing else.

The model proposes entries, never these two lists, so this module has no proposal path —
which is the other half of the claim, and the reason the example keeps both kinds.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.foundation.errors import DomainError

from ..ledger.model import Entry
from ..world import bump_ledger
from .model import Category, CategoryKind, Wallet

MAX_NAME_LENGTH = 60


def _name(value: str, what: str) -> str:
    text = value.strip()
    if not text:
        raise DomainError(f"A {what} needs a name")
    if len(text) > MAX_NAME_LENGTH:
        raise DomainError(f"A {what} name is at most {MAX_NAME_LENGTH} characters")
    return text


async def create_wallet(session: AsyncSession, *, name: str, currency: str) -> Wallet:
    text = _name(name, "Wallet")
    code = currency.strip().upper()
    if len(code) != 3 or not code.isalpha():
        raise DomainError("A currency is a three-letter code, such as USD")
    if await session.scalar(select(Wallet).where(Wallet.name == text)) is not None:
        raise DomainError(f"A Wallet called “{text}” already exists")
    wallet = Wallet(name=text, currency=code)
    session.add(wallet)
    await session.flush()
    await bump_ledger(session)
    return wallet


async def rename_wallet(session: AsyncSession, wallet_id: int, name: str) -> Wallet:
    wallet = await session.get(Wallet, wallet_id)
    if wallet is None:
        raise DomainError("Wallet does not exist")
    text = _name(name, "Wallet")
    taken = await session.scalar(select(Wallet).where(Wallet.name == text, Wallet.id != wallet_id))
    if taken is not None:
        raise DomainError(f"A Wallet called “{text}” already exists")
    wallet.name = text
    await bump_ledger(session)
    return wallet


async def create_category(session: AsyncSession, *, name: str, kind: CategoryKind) -> Category:
    text = _name(name, "Category")
    if await session.scalar(select(Category).where(Category.name == text)) is not None:
        raise DomainError(f"A Category called “{text}” already exists")
    category = Category(name=text, kind=kind.value)
    session.add(category)
    await session.flush()
    await bump_ledger(session)
    return category


async def wallet_balance(session: AsyncSession, wallet_id: int) -> int:
    """What is left in one wallet, in minor units: income summed, expense subtracted."""
    signed = func.sum(
        Entry.amount_minor
        * func.iif(Category.kind == CategoryKind.INCOME.value, 1, -1)
    )
    total = await session.scalar(
        select(signed)
        .select_from(Entry)
        .join(Category, Category.id == Entry.category_id)
        .where(Entry.wallet_id == wallet_id)
    )
    return int(total or 0)
