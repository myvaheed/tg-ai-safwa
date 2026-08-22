"""How a proposed Reminder reads to the owner."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from ...ai.contracts import AgentChange
from ...models import ProposalChange
from ..proposals.api import (
    ACTION_VERBS,
    ProposalScreen,
    detail_lines,
    result_value,
)
from .model import Reminder


class ReminderProposalPresenter:
    entity = "reminder"

    def raw_details(self, change: AgentChange) -> list[str]:
        return detail_lines(dict(change.values))

    async def details(
        self, session: AsyncSession, change: ProposalChange, fallback: AgentChange | None
    ) -> list[str]:
        fallback_lines = self.raw_details(fallback) if fallback is not None else []
        return fallback_lines or detail_lines(dict(change.values))

    async def summary(
        self, session: AsyncSession, change: ProposalChange, details: list[str]
    ) -> str:
        values = dict(change.values)
        reminder = (
            await session.get(Reminder, change.entity_id)
            if change.entity_id is not None
            else None
        )
        text = str(values.get("instruction") or (reminder.instruction if reminder else ""))
        head = f"Reminder “{result_value(text)}”" if text else f"Reminder #{change.entity_id}"
        # A Reminder has no archive, so the only removal `remove` can send reads as one.
        verb = (
            "Delete"
            if change.action == "archive"
            else ACTION_VERBS.get(change.action, change.action.title())
        )
        schedule = values.get("schedule_text")
        return f"{verb} {head}" + (f" ({schedule})" if schedule else "")

    async def screen(
        self, session: AsyncSession, change: ProposalChange
    ) -> ProposalScreen | None:
        # A Reminder is instruction plus timing; the generic change list already says both.
        return None
