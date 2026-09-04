"""How a proposed Reminder reads to the owner, and what the owner typing its text writes.

A Reminder that goes off is handed to the Advisor as a Cue, and the Cue runtime is what
runs that turn.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.ai.contracts import AgentChange
from tg_agent_shell.proposals.api import (
    ACTION_VERBS,
    ChangeAction,
    ProposalChange,
    ProposalScreen,
    detail_lines,
    result_value,
)
from tg_agent_shell.telegram import required_text
from tg_agent_shell.telegram.contributions import TextInputFlow

from ..model import Reminder
from ..use_cases import update_reminder_text
from .screens import render_reminder

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


async def _apply_reminder_text(
    session: AsyncSession, services: Any, state: Mapping[str, Any], value: str
) -> None:
    del services
    await update_reminder_text(session, int(state["reminder_id"]), value)


async def _render_reminder(
    message: Any, services: Any, state: Mapping[str, Any], value: str
) -> None:
    await render_reminder(
        message,
        services,
        int(state["reminder_id"]),
        replace_message_id=int(state["text_input"]["message_id"]),
    )


TEXT_INPUT = TextInputFlow(
    name="reminder",
    validator=lambda _state: required_text("Reminder text"),
    apply=_apply_reminder_text,
    render=_render_reminder,
)
