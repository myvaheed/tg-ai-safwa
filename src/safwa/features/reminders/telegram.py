"""How a Reminder reaches the owner: the proposal screen, and nothing else.

A Reminder that goes off is handed to the Advisor as a Cue, and the Cue runtime is what
runs that turn. This module registers no ``@router`` handlers.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from ...ai.contracts import AgentChange
from ..proposals.api import (
    ACTION_VERBS,
    ChangeAction,
    ProposalChange,
    ProposalScreen,
    detail_lines,
    result_value,
)
from .model import Reminder

logger = logging.getLogger(__name__)


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
            if change.action is ChangeAction.ARCHIVE
            else ACTION_VERBS.get(change.action, change.action.title())
        )
        schedule = values.get("schedule_text")
        return f"{verb} {head}" + (f" ({schedule})" if schedule else "")

    async def screen(
        self, session: AsyncSession, change: ProposalChange
    ) -> ProposalScreen | None:
        # A Reminder is instruction plus timing; the generic change list already says both.
        return None
