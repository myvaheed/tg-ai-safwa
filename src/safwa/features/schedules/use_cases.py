"""Remind: the Reminder a Card's or Check's Schedule makes, kept in step with that item.

The Reminder is an ordinary one, fired by the Reminders' tick. What is Remind's own is only
keeping it true: the item's next instance, a changed Schedule or title, and its end.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.foundation.clock import utcnow
from tg_agent_shell.foundation.errors import DomainError

from ...enums import ActorType
from ..cards.model import Card
from ..checks.model import Check
from ..reminders.api import schedule_of
from ..reminders.model import Reminder
from ..reminders.use_cases import (
    create_reminder,
    delete_reminder,
    move_reminder,
    reschedule_reminder,
    update_reminder_text,
)
from .api import entity_reminder, item_type, remind_text, remind_timing, workspace_zone


async def set_remind(session: AsyncSession, entity: Card | Check, on: bool) -> None:
    """On makes a Reminder at the Schedule's moments; off deletes it."""
    reminder = await entity_reminder(session, entity)
    if not on:
        if reminder is not None:
            await delete_reminder(session, reminder.id)
        return
    if reminder is not None:
        return
    timing = remind_timing(entity, utcnow())
    if timing is None:
        raise DomainError("Remind needs a Schedule with a time that is still ahead")
    await create_reminder(
        session,
        instruction=remind_text(entity),
        schedule=timing,
        tz=await workspace_zone(session),
        item_type=item_type(type(entity)),
        item_id=entity.id,
    )


async def follow_remind(
    session: AsyncSession,
    entity: Card | Check,
    successor: Card | Check | None = None,
    *,
    actor: ActorType = ActorType.USER_UI,
) -> None:
    """Move Remind's Reminder on to the next instance, a changed Schedule or title, and
    delete it once nothing is ahead to remind of. Its next fire moves only with its timing."""
    reminder = await entity_reminder(session, entity)
    if reminder is None:
        return
    current = successor or entity
    timing = remind_timing(current, utcnow())
    if timing is None:
        await delete_reminder(session, reminder.id, actor=actor)
        return
    if reminder.item_id != current.id:
        await move_reminder(session, reminder.id, current.id)
    text = remind_text(current)
    if reminder.instruction != text:
        await update_reminder_text(session, reminder.id, text, actor=actor)
    if schedule_of(reminder) != timing:
        await reschedule_reminder(
            session, reminder.id, schedule=timing, tz=await workspace_zone(session), actor=actor
        )


async def drop_reminders(
    session: AsyncSession,
    model: type[Card] | type[Check],
    ids: Sequence[int],
    *,
    actor: ActorType = ActorType.USER_UI,
) -> None:
    """Deleted items take Remind's Reminders with them."""
    for reminder_id in list(
        await session.scalars(
            select(Reminder.id).where(
                Reminder.item_type == item_type(model), Reminder.item_id.in_(ids)
            )
        )
    ):
        await delete_reminder(session, reminder_id, actor=actor)
