"""How a proposed entry is checked, and then written by the same use cases a screen calls."""

from __future__ import annotations

from datetime import date as calendar_date
from typing import Any

from tg_agent_shell.foundation.errors import DomainError, StaleStateError
from tg_agent_shell.proposals.api import (
    ApplyContext,
    ChangeAction,
    PreparationContext,
    PreparedChange,
    ProposalChange,
    ToolPreparationError,
    require_target,
)

from ..wallets.model import Category, Wallet
from .model import Entry
from .use_cases import delete_entry, record_entry, update_entry


class EntryProposalHandler:
    entity = "entry"
    version_model: type[Any] | None = Entry
    # An entry removed is a movement of money the ledger no longer remembers happening.
    destructive_actions = frozenset({ChangeAction.DELETE})
    destructive_warning = "This permanently removes that line from the ledger."

    async def prepare(self, context: PreparationContext, change: Any) -> PreparedChange:
        entry, expected_version = await require_target(context, change, Entry)
        if change.action is ChangeAction.DELETE:
            change.values = {}
            return PreparedChange(values={}, expected_version=expected_version)
        # An update replaces the line whole, so what the call left out is filled from what
        # is saved: the review screen then shows what Save will actually store.
        values = dict(change.values)
        resolved = {
            "wallet_id": values.get("wallet_id") or (entry.wallet_id if entry else None),
            "category_id": values.get("category_id") or (entry.category_id if entry else None),
            "amount_minor": values.get("amount_minor") or (entry.amount_minor if entry else None),
            "happened_on": str(
                values.get("happened_on")
                or (entry.happened_on.isoformat() if entry else "")
            ),
            "note": str(values.get("note") if values.get("note") is not None else (entry.note if entry else "")),
        }
        await self._resolve_names(context, resolved)
        return PreparedChange(values=resolved, expected_version=expected_version)

    async def _resolve_names(self, context: PreparationContext, values: dict[str, Any]) -> None:
        """Say which list the missing row is in, so the model can look it up rather than guess."""
        wallet = await context.session.get(Wallet, values["wallet_id"])
        if wallet is None:
            raise ToolPreparationError(
                "target_not_found",
                f"Wallet #{values['wallet_id']} does not exist.",
                "Find the wallet in ai_wallets and retry with its id. If it is not there, "
                "tell the user to add it themselves.",
            )
        category = await context.session.get(Category, values["category_id"])
        if category is None:
            raise ToolPreparationError(
                "target_not_found",
                f"Category #{values['category_id']} does not exist.",
                "Find the category in ai_categories and retry with its id. If it is not "
                "there, tell the user to add it themselves.",
            )
        try:
            calendar_date.fromisoformat(values["happened_on"])
        except ValueError as error:
            raise ToolPreparationError(
                "invalid_arguments",
                "happened_on must be a calendar date written as YYYY-MM-DD.",
                "Retry the entry call with the day you meant, written as YYYY-MM-DD.",
            ) from error
        values["wallet_name"] = wallet.name
        values["currency"] = wallet.currency
        values["category_name"] = category.name
        values["kind"] = category.kind

    async def apply(self, context: ApplyContext, change: ProposalChange) -> list[int]:
        session = context.session
        values = dict(change.values)
        if change.action is ChangeAction.CREATE:
            entry = await record_entry(
                session,
                wallet_id=int(values["wallet_id"]),
                category_id=int(values["category_id"]),
                amount_minor=int(values["amount_minor"]),
                happened_on=calendar_date.fromisoformat(str(values["happened_on"])),
                note=str(values.get("note") or ""),
            )
            return [entry.id]
        if change.action not in {ChangeAction.UPDATE, ChangeAction.DELETE}:
            raise DomainError(f"Unsupported approved ledger action: {change.action}")
        entry = await session.get(Entry, change.entity_id) if change.entity_id else None
        if entry is None or entry.version != change.expected_version:
            raise StaleStateError("That entry changed; refresh this proposal")
        entry_id = entry.id
        if change.action is ChangeAction.DELETE:
            await delete_entry(session, entry_id)
        else:
            await update_entry(
                session,
                entry_id,
                wallet_id=int(values["wallet_id"]),
                category_id=int(values["category_id"]),
                amount_minor=int(values["amount_minor"]),
                happened_on=calendar_date.fromisoformat(str(values["happened_on"])),
                note=str(values.get("note") or ""),
            )
        return [entry_id]
