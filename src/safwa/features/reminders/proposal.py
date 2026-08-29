"""How a proposed Reminder is checked and then written.

Timing is resolved during preparation, before the proposal row exists, so the review
screen shows a real schedule and Save applies exactly what the owner approved.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from ...foundation.errors import DomainError, StaleStateError
from ...foundation.models import Workspace
from ..proposals.api import (
    ApplyContext,
    ChangeAction,
    PreparationContext,
    PreparedChange,
    ProposalChange,
    ToolPreparationError,
    require_target,
)
from .agent import resolve_schedule
from .model import Reminder
from .schedule import ScheduleError, describe, schedule_from_payload, schedule_payload
from .use_cases import (
    create_reminder,
    delete_reminder,
    reschedule_reminder,
    update_reminder_text,
)


class ReminderProposalHandler:
    entity = "reminder"
    # A Reminder proposal keeps the version it was prepared against.
    version_model: type[Any] | None = None

    async def prepare(self, context: PreparationContext, change: Any) -> PreparedChange:
        _reminder, expected_version = await require_target(context, change, Reminder)
        values = await self._resolve_timing(context, dict(change.values))
        return PreparedChange(values=values, expected_version=expected_version)

    async def _resolve_timing(
        self, context: PreparationContext, values: dict[str, Any]
    ) -> dict[str, Any]:
        """An unresolvable phrase becomes a retryable tool error carrying the question to ask."""
        prepared = dict(values)
        when = str(prepared.pop("when", "") or "").strip()
        if not when:
            return prepared  # an edit with no timing leaves the schedule alone
        tz = ZoneInfo(context.workspace.timezone)
        now = datetime.now(UTC)
        try:
            schedule = await resolve_schedule(
                context.provider,
                when=when,
                instruction=str(prepared.get("instruction", "")),
                now=now,
                tz=tz,
            )
        except ScheduleError as error:
            raise ToolPreparationError(
                "schedule_unclear",
                str(error),
                "Ask the user this exact question, then call reminder again with their "
                "answer in when. Never invent a time.",
            ) from error
        prepared["schedule"] = schedule_payload(schedule)
        prepared["schedule_text"] = describe(schedule, tz=tz, now=now)
        return prepared

    async def apply(self, context: ApplyContext, change: ProposalChange) -> list[int]:
        """Save an approved Reminder through the same domain calls the UI uses.

        The schedule travels in the values blob, already resolved, so Save writes what the
        review screen showed.
        """
        session = context.session
        workspace = await session.get(Workspace, 1)
        tz = ZoneInfo(workspace.timezone if workspace else "UTC")
        values = dict(change.values)
        payload = values.get("schedule")
        if change.action is ChangeAction.CREATE:
            if payload is None:
                raise DomainError("A new Reminder needs a schedule")
            reminder = await create_reminder(
                session,
                instruction=str(values.get("instruction", "")),
                schedule=schedule_from_payload(payload),
                tz=tz,
            )
            return [reminder.id]
        if change.entity_id is None:
            raise DomainError("This Reminder change has no target")
        # `remove` may only send archive for a Reminder, and a Reminder has no archive.
        if change.action in {ChangeAction.DELETE, ChangeAction.ARCHIVE}:
            await delete_reminder(session, change.entity_id)
            return [change.entity_id]
        if change.action is not ChangeAction.UPDATE:
            raise DomainError(f"Unsupported approved Reminder action: {change.action}")
        reminder = await session.get(Reminder, change.entity_id)
        if reminder is None or reminder.version != change.expected_version:
            raise StaleStateError("A Reminder changed; refresh this proposal")
        if values.get("instruction"):
            await update_reminder_text(session, reminder.id, str(values["instruction"]))
        if payload is not None:
            await reschedule_reminder(
                session, reminder.id, schedule=schedule_from_payload(payload), tz=tz
            )
        return [reminder.id]
