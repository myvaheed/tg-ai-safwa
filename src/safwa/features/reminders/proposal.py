"""How a proposed Reminder is checked and then written.

Timing is resolved during preparation, before the proposal row exists, so the review
screen shows a real schedule and Save applies exactly what the owner approved. A condition's
items are picked there by their own short session, from the open items and the owner's words
in the conversation the call came from, then kept as their series and found, so the screen
shows what it counts now.

The Reminder an item's Remind made is the item's to change: a change or a deletion of it goes
back to the model with the item to use `remind` on.
"""

from __future__ import annotations

from datetime import UTC, datetime
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
from .agent import (
    ConditionUnclear,
    CountsAnswers,
    read_condition,
    resolve_schedule,
)
from .api import (
    Condition,
    Schedule,
    ScheduleError,
    describe,
    schedule_from_payload,
    schedule_payload,
)
from .conditions import finds_text, found_ids, items_series, never_reached
from .items import open_items
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
        """An unresolvable time becomes a retryable tool error carrying what is unclear and
        the forms that work; a condition's items or count it cannot keep become one too."""
        prepared = dict(values)
        when = str(prepared.pop("when", "") or "").strip()
        on_answers = str(prepared.pop("on_answers", "") or "").strip()
        if not when and not on_answers:
            return prepared  # an edit with no timing leaves the schedule alone
        tz = ZoneInfo(context.world.timezone)
        now = datetime.now(UTC)
        if on_answers:
            condition = await _condition(context)
            prepared["finds"] = await _finds(context.session, condition)
            schedule = Schedule(kind=ScheduleKind.CONDITION, condition=condition)
        else:
            try:
                schedule = await resolve_schedule(
                    context.provider,
                    when=when,
                    now=now,
                    tz=tz,
                    conversation=context.conversation,
                )
            except CountsAnswers as error:
                raise ToolPreparationError(
                    "when_counts_answers",
                    str(error),
                    "Call reminder again with the same words in on_answers instead of when.",
                ) from error
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
                remind_text=str(values.get("remind_text", "")),
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
        if values.get("remind_text"):
            await update_reminder_text(
                session, reminder.id, str(values["remind_text"]), actor=ActorType.AI
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


async def _condition(context: PreparationContext) -> Condition:
    """The condition its own session reads, its items kept as their series, or refused when
    the owner's words do not say what it counts (RM-COND-030)."""
    before = context.earlier and context.earlier.get("schedule")
    if before:
        # Only the words are edited by hand, and what the call counts was read from the call
        # (PR-EDIT-034, RM-EDIT-038).
        condition = schedule_from_payload(before).condition
        if condition is not None:
            return condition
    try:
        fields = await read_condition(
            context.provider,
            open_items=await open_items(
                context.session, context.query_runner.search, context.conversation
            ),
            conversation=context.conversation,
        )
    except ConditionUnclear as error:
        raise ToolPreparationError(
            "condition_unclear",
            str(error),
            "Ask the user what is missing, then call reminder again.",
        ) from error
    # The session named only items its block lists, so each exists (RM-COND-031).
    kind, series = await items_series(context.session, fields.items)
    return Condition(
        items=fields.items,
        series=series,
        kind=kind,
        answer=fields.answer,
        count=fields.count,
        in_row=fields.in_row,
        once=fields.once,
    )


async def _finds(session: AsyncSession, condition: Condition) -> str:
    """What the condition finds now, or why it is refused: its one series can never reach its
    count (RM-COND-034)."""
    ids = await found_ids(session, condition)
    never = await never_reached(session, condition, ids)
    if never is not None:
        raise ToolPreparationError(
            "count_never_reached",
            never,
            "Tell the user, then call reminder again with their answer.",
        )
    return await finds_text(session, condition, ids)


async def owner_reminders(session: AsyncSession) -> list[tuple[int, str]]:
    """Every Reminder the owner set, by its remind_text (PR-SIMILAR-030)."""
    rows = await session.execute(select(Reminder.id, Reminder.remind_text))
    return [(reminder_id, remind_text) for reminder_id, remind_text in rows]
