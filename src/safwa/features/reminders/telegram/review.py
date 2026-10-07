"""How a proposed Reminder reads to the owner, and what the owner typing its text writes.

A Reminder that goes off is handed to the Advisor as a Cue, and the Cue runtime is what
runs that turn.
"""

from __future__ import annotations

import html
import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.ai.contracts import AgentChange
from tg_agent_shell.proposals.api import (
    ChangeAction,
    ProposalChange,
    ProposalScreen,
)
from tg_agent_shell.proposals.render import (
    ACTION_VERBS,
    field_diffs,
    result_value,
)
from tg_agent_shell.telegram import edited_screen, required_text, short_citation_title
from tg_agent_shell.telegram.contributions import TextInputFlow

from ....foundation.workspace import Workspace
from ..model import Reminder
from ..schedule import describe, next_fire, schedule_from_payload, schedule_of
from ..use_cases import update_reminder_text
from .screens import reminder_title, render_reminder

logger = logging.getLogger(__name__)


async def reminder_citation_label(
    session: AsyncSession, services: Any, reminder: Reminder
) -> str:
    return f"⏰ {short_citation_title(await reminder_title(session, services, reminder))}"


def _lines(values: Mapping[str, Any], when_field: str) -> list[str]:
    """The words and the time of a Reminder, as the owner reads them: never the stored
    schedule, which is parameters rather than words."""
    lines = []
    if values.get("instruction"):
        lines.append(f"Words: {result_value(values['instruction'])}")
    if values.get(when_field):
        lines.append(f"When: {result_value(values[when_field])}")
    return lines


class ReminderProposalPresenter:
    entity = "reminder"

    def raw_details(self, change: AgentChange) -> list[str]:
        # Before its timing is worked out, a Reminder's time is the words it was given.
        return _lines(dict(change.values), "when")

    async def details(
        self, session: AsyncSession, change: ProposalChange, fallback: AgentChange | None
    ) -> list[str]:
        return _lines(dict(change.values), "schedule_text")

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
        self, session: AsyncSession, changes: Sequence[ProposalChange]
    ) -> ProposalScreen | None:
        """The words, when they fire and when they fire first, as the owner will meet them."""
        change = changes[-1]
        values = dict(change.values)
        reminder = (
            await session.get(Reminder, change.entity_id) if change.entity_id is not None else None
        )
        workspace = await session.get(Workspace, 1)
        tz = ZoneInfo(workspace.timezone if workspace else "UTC")
        now = datetime.now(UTC)
        current = (
            {"words": reminder.instruction, "when": describe(schedule_of(reminder), tz=tz, now=now)}
            if reminder is not None
            else {}
        )
        proposed = {
            "words": values.get("instruction") or current.get("words") or "",
            "when": values.get("schedule_text") or current.get("when") or "—",
        }
        if change.action in {ChangeAction.ARCHIVE, ChangeAction.DELETE}:
            mode, first = "Delete", None
        else:
            mode = "Create" if change.action is ChangeAction.CREATE else "Edit"
            payload = values.get("schedule")
            first = (
                next_fire(schedule_from_payload(payload), previous=None, now=now, tz=tz)
                if payload is not None
                else reminder.next_fire_at if reminder is not None else None
            )
        lines = [f"🔁 <b>When:</b> {html.escape(proposed['when'])}"]
        if first is not None:
            lines.append(f"⏭ <b>First:</b> {first.astimezone(tz):%a %d.%m %H:%M}")
        lines.extend(["", html.escape(proposed["words"])])
        return ProposalScreen(
            mode=mode,
            item="Reminder",
            blocks=("\n".join(lines),),
            diffs=field_diffs(current, proposed) if mode == "Edit" else (),
        )


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
        back=edited_screen(state).back,
        replace_message_id=int(state["text_input"]["message_id"]),
    )


TEXT_INPUT = TextInputFlow(
    name="reminder",
    validator=lambda _state: required_text("Reminder text"),
    apply=_apply_reminder_text,
    render=_render_reminder,
)
