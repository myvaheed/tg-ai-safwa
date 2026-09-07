"""How one entry reads: on its own screen, on a citation, and on the review screen."""

from __future__ import annotations

import html
from typing import Any

from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.ai.contracts import AgentChange
from tg_agent_shell.foundation.errors import DomainError
from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.proposals.api import (
    ChangeAction,
    ProposalChange,
    ProposalScreen,
)
from tg_agent_shell.proposals.render import (
    ACTION_VERBS,
    detail_lines,
)
from tg_agent_shell.telegram import Services, send_registered

from ..wallets.model import Category, Wallet
from .model import Entry


def money(amount_minor: int, currency: str) -> str:
    """Minor units as the owner reads them; the sign is the caller's to add."""
    return f"{amount_minor // 100}.{amount_minor % 100:02d} {currency}"


def signed(amount_minor: int, kind: str, currency: str) -> str:
    return ("+" if kind == "income" else "−") + money(amount_minor, currency)


async def _named(session: AsyncSession, entry: Entry) -> tuple[Wallet, Category]:
    wallet = await session.get(Wallet, entry.wallet_id)
    category = await session.get(Category, entry.category_id)
    if wallet is None or category is None:
        raise DomainError("That entry points at a wallet or category that is gone")
    return wallet, category


async def entry_line(session: AsyncSession, entry: Entry) -> str:
    wallet, category = await _named(session, entry)
    return (
        f"{entry.happened_on.isoformat()} · {signed(entry.amount_minor, category.kind, wallet.currency)}"
        f" · {category.name} · {wallet.name}"
    )


async def render_entry(
    message: Message,
    services: Services,
    entry_id: int,
    *,
    replace: bool | None = None,
) -> None:
    """One entry in full and read-only: the ledger is written through proposals alone."""
    async with services.sessions() as session:
        entry = await session.get(Entry, entry_id)
        if entry is None:
            raise DomainError("Entry does not exist")
        line = await entry_line(session, entry)
        note = entry.note
    body = f"<b>🧾 {html.escape(line)}</b>"
    if note:
        body += f"\n\n{html.escape(note)}"
    await send_registered(
        message,
        services,
        body,
        kind=MessageKind.DASHBOARD,
        related_id=entry_id,
        replace=replace,
    )


async def entry_citation_label(session: AsyncSession, services: Any, entry: Entry) -> str:
    return await entry_line(session, entry)


class EntryProposalPresenter:
    entity = "entry"

    def raw_details(self, change: AgentChange) -> list[str]:
        return detail_lines(dict(change.values))

    async def details(
        self, session: AsyncSession, change: ProposalChange, fallback: AgentChange | None
    ) -> list[str]:
        if change.action is ChangeAction.DELETE:
            entry = await session.get(Entry, change.entity_id) if change.entity_id else None
            return [await entry_line(session, entry)] if entry is not None else ["Entry: removed"]
        values = dict(change.values)
        lines = [
            f"Date: {values.get('happened_on', '')}",
            f"Wallet: {values.get('wallet_name', '')}",
            f"Category: {values.get('category_name', '')}",
            f"Amount: {signed(int(values.get('amount_minor') or 0), str(values.get('kind') or ''), str(values.get('currency') or ''))}",
        ]
        if values.get("note"):
            lines.append(f"Note: {values['note']}")
        return lines

    async def summary(
        self, session: AsyncSession, change: ProposalChange, details: list[str]
    ) -> str:
        verb = ACTION_VERBS.get(change.action, change.action.title())
        values = dict(change.values)
        if change.action is ChangeAction.DELETE:
            return f"{verb} ledger entry #{change.entity_id}"
        return (
            f"{verb} ledger entry: "
            f"{signed(int(values.get('amount_minor') or 0), str(values.get('kind') or ''), str(values.get('currency') or ''))}"
            f" in {values.get('wallet_name', '')}"
        )

    async def screen(
        self, session: AsyncSession, change: ProposalChange
    ) -> ProposalScreen | None:
        lines = await self.details(session, change, None)
        return ProposalScreen(
            mode=(
                "Create"
                if change.action is ChangeAction.CREATE
                else "Remove"
                if change.action is ChangeAction.DELETE
                else "Edit"
            ),
            item="Ledger entry",
            blocks=(html.escape("\n".join(lines)),),
            diffs=(),
        )
