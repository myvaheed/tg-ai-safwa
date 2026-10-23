"""How a proposed Reminder is checked and then written.

Timing is resolved during preparation, before the proposal row exists, so the review
screen shows a real schedule and Save applies exactly what the owner approved. A condition's
SELECT is run there too, so the screen shows what it finds now.

The Reminder an item's Remind made is the item's to change: a change or a deletion of it goes
back to the model with the item to use `remind` on.
"""

from __future__ import annotations

from datetime import UTC, datetime
from functools import partial
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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

from ...enums import ActorType
from ...foundation.workspace import Workspace
from ..cards.model import Card
from ..checks.model import Check
from ..schedules.api import item_type
from .agent import UNREADABLE_ITEMS, resolve_schedule, unclear
from .api import Condition, ScheduleError, describe, schedule_from_payload, schedule_payload
from .conditions import (
    ConditionError,
    finds_text,
    found_ids,
    item_names,
    never_reached,
    one_series_select,
)
from .model import Reminder, ScheduleKind
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
        reminder, expected_version = await require_target(context, change, Reminder)
        if reminder is not None and reminder.item_type is not None:
            item, title = await _remind_of(context.session, reminder)
            raise ToolPreparationError(
                "remind_of_item",
                f"Reminder #{reminder.id} is the Remind of {item} «{title}».",
                f"Use remind on {item}.",
            )
        if change.action is ChangeAction.DELETE:
            return PreparedChange(values={}, expected_version=expected_version)
        values = await self._resolve_timing(context, dict(change.values))
        return PreparedChange(values=values, expected_version=expected_version)

    async def _resolve_timing(
        self, context: PreparationContext, values: dict[str, Any]
    ) -> dict[str, Any]:
        """An unresolvable phrase becomes a retryable tool error carrying what is unclear and
        the forms that work."""
        prepared = dict(values)
        when = str(prepared.pop("when", "") or "").strip()
        if not when:
            return prepared  # an edit with no timing leaves the schedule alone
        tz = ZoneInfo(context.world.timezone)
        now = datetime.now(UTC)
        try:
            schedule = await resolve_schedule(
                context.provider,
                when=when,
                instruction=str(prepared.get("instruction", "")),
                now=now,
                tz=tz,
                names=await item_names(context.session),
                series_select=partial(one_series_select, context.session),
            )
            if schedule.kind is ScheduleKind.CONDITION:
                prepared["finds"] = await _finds(context.session, schedule.condition)
        except ScheduleError as error:
            raise ToolPreparationError(
                "schedule_unclear",
                str(error),
                "Tell the user what is unclear and these forms, then call reminder again with "
                "their answer in when. Never invent a time.",
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
                actor=ActorType.AI,
            )
            return [reminder.id]
        if change.entity_id is None:
            raise DomainError("This Reminder change has no target")
        if change.action is ChangeAction.DELETE:
            await delete_reminder(session, change.entity_id, actor=ActorType.AI)
            return [change.entity_id]
        if change.action is not ChangeAction.UPDATE:
            raise DomainError(f"Unsupported approved Reminder action: {change.action}")
        reminder = await session.get(Reminder, change.entity_id)
        if reminder is None or reminder.version != change.expected_version:
            raise StaleStateError("A Reminder changed; refresh this proposal")
        if values.get("instruction"):
            await update_reminder_text(
                session, reminder.id, str(values["instruction"]), actor=ActorType.AI
            )
        if payload is not None:
            await reschedule_reminder(
                session,
                reminder.id,
                schedule=schedule_from_payload(payload),
                tz=tz,
                actor=ActorType.AI,
            )
        return [reminder.id]


async def _remind_of(session: AsyncSession, reminder: Reminder) -> tuple[str, str]:
    """The item whose Remind made the Reminder, as `remind` names it, and its title."""
    model = Check if reminder.item_type == item_type(Check) else Card
    item = await session.get(model, reminder.item_id)
    kind = "Check" if model is Check else item.kind.title()
    return f"{kind} #{item.id}", item.title


async def _finds(session: AsyncSession, condition: Condition) -> str:
    """What the condition's SELECT finds now, or why it is refused: it does not run, or it
    finds one series that can never reach its count (RM-COND-034)."""
    try:
        ids = await found_ids(session, condition)
    except ConditionError as error:
        raise unclear(f"{UNREADABLE_ITEMS} {error}", "answers") from error
    never = await never_reached(session, condition, ids)
    if never is not None:
        raise unclear(never, "answers")
    return await finds_text(session, condition, ids)


async def owner_reminders(session: AsyncSession) -> list[tuple[int, str]]:
    """Every Reminder the owner set, by its instruction (PR-SIMILAR-030)."""
    rows = await session.execute(select(Reminder.id, Reminder.instruction))
    return [(reminder_id, instruction) for reminder_id, instruction in rows]
