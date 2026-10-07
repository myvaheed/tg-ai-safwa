"""The `/reminders` screens: a list, a detail view, a text edit, a delete confirmation.

Text is the only editable field here.  A schedule is resolved from plain words by a model
session and then computed in code, so there is no sensible manual form for it — the advisor
changes timing, and this screen says so.  There is no creation button either, for the same
reason Requests has none.
"""

from __future__ import annotations

import html
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from aiogram.types import InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.foundation.errors import DomainError
from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.telegram import (
    CallbackContext,
    CallbackHandler,
    Place,
    Services,
    TextInputScreen,
    back_button,
    edit_registered_message,
    go,
    menu_row,
    paginate,
    paging_row,
    place_button,
    render_text_input,
    send_registered,
)

from ....foundation.workspace import Workspace
from ..model import Reminder
from ..schedule import describe, schedule_of
from ..use_cases import delete_reminder

_TEXT_PREVIEW = 40
_PROMPT_TTL = timedelta(minutes=30)


async def reminder_title(session: AsyncSession, services: Any, reminder: Reminder) -> str:
    """What a Reminder is about: the Card or Check Remind made it for, or the start of its
    words. The item is read through the screens catalogue, so this feature names none."""
    spec = services.screens.by_type.get(reminder.item_type) if reminder.item_type else None
    item = await session.get(spec.model, reminder.item_id) if spec is not None else None
    words = item.title if item is not None else (reminder.instruction.strip().splitlines() or [""])[0]
    return words if len(words) <= _TEXT_PREVIEW else words[: _TEXT_PREVIEW - 1].rstrip() + "…"


def reminder_when(reminder: Reminder, *, tz: ZoneInfo, now: datetime) -> str:
    """When a Reminder fires. One Remind made starts when its item's Schedule does, so its
    start is not said again."""
    return describe(schedule_of(reminder), tz=tz, now=None if reminder.item_type else now)


async def render_reminders(message: Message, services: Services, *, page: int = 0) -> None:
    async with services.sessions() as session:
        tz = await _timezone(session)
        now = datetime.now(UTC)
        reminders = list(
            await session.scalars(select(Reminder).order_by(Reminder.next_fire_at))
        )
        window = paginate(reminders, page)
        here = Place("reminders_page", {"page": window.index})
        rows = [
            [
                await place_button(
                    session,
                    services.owner_id,
                    f"{await reminder_title(session, services, reminder)} · "
                    f"{reminder_when(reminder, tz=tz, now=now)}",
                    here.child("reminder_view", id=reminder.id),
                )
            ]
            for reminder in window.items
        ]
        rows.extend(await paging_row(session, services.owner_id, window, here))
        await session.commit()
    body = (
        "Triggers you set. Your advisor creates and reschedules them; here you can edit the "
        "text or delete one."
        if reminders
        else "No Reminders yet. Ask your advisor to set one."
    )
    await send_registered(
        message,
        services,
        f"<b>Reminders</b>\n{body}",
        kind=MessageKind.DASHBOARD,
        markup=InlineKeyboardMarkup(inline_keyboard=rows + [menu_row()]),
    )


async def render_reminder(
    message: Message,
    services: Services,
    reminder_id: int,
    *,
    back: Place | None = None,
    replace_message_id: int | None = None,
    replace: bool | None = None,
) -> None:
    """One Reminder. `back` is where it was opened from."""
    async with services.sessions() as session:
        reminder = await session.get(Reminder, reminder_id)
        if reminder is None:
            raise DomainError("Reminder does not exist")
        tz = await _timezone(session)
        text = _detail_text(
            reminder, await reminder_title(session, services, reminder), tz=tz, now=datetime.now(UTC)
        )
        edit = await place_button(
            session,
            services.owner_id,
            "✏️ Text",
            Place("reminder_text_prompt", {"id": reminder.id}, back),
        )
        remove = await place_button(
            session,
            services.owner_id,
            "🗑 Delete",
            Place("reminder_delete_prompt", {"id": reminder.id}, back),
        )
        leave = await back_button(session, services.owner_id, back)
        await session.commit()
    markup = InlineKeyboardMarkup(inline_keyboard=[[edit, remove], [leave]])
    if replace_message_id is not None:
        await edit_registered_message(
            message,
            services,
            replace_message_id,
            text,
            kind=MessageKind.DASHBOARD,
            markup=markup,
            related_id=reminder_id,
        )
    else:
        await send_registered(
            message,
            services,
            text,
            kind=MessageKind.DASHBOARD,
            markup=markup,
            related_id=reminder_id,
            replace=replace,
        )


async def render_reminder_text_prompt(
    message: Message, services: Services, reminder_id: int, *, back: Place | None = None
) -> None:
    """Type the words of a Reminder. `back` is where that Reminder was opened from."""
    async with services.sessions() as session:
        reminder = await session.get(Reminder, reminder_id)
        if reminder is None:
            raise DomainError("Reminder does not exist")
        current = reminder.instruction
    await render_text_input(
        message,
        services,
        screen=TextInputScreen(
            title="Edit Reminder text",
            current_value=current,
            instruction=(
                "Send the new text. It must stand on its own when it fires, so name any Card or "
                "Check by #id. The schedule will not change."
            ),
            back=Place("reminder_view", {"id": reminder_id}, back),
            related_id=reminder_id,
        ),
        state={"flow": "reminder", "reminder_id": reminder_id},
    )


async def render_reminder_delete_prompt(
    message: Message, services: Services, reminder_id: int, *, back: Place | None = None
) -> None:
    """Ask before a Reminder is deleted. `back` is where that Reminder was opened from."""
    async with services.sessions() as session:
        reminder = await session.get(Reminder, reminder_id)
        if reminder is None:
            raise DomainError("Reminder does not exist")
        tz = await _timezone(session)
        schedule = describe(schedule_of(reminder), tz=tz, now=datetime.now(UTC))
        confirm = await place_button(
            session,
            services.owner_id,
            "Delete Reminder",
            Place("reminder_delete_confirm", {"id": reminder_id}, back),
        )
        leave = await back_button(
            session, services.owner_id, Place("reminder_view", {"id": reminder_id}, back)
        )
        await session.commit()
    await send_registered(
        message,
        services,
        f"<b>Delete this Reminder?</b>\n{html.escape(schedule)}\n"
        f"{html.escape(reminder.instruction)}\n\n"
        "It stops firing immediately. There is no archive for a Reminder.",
        kind=MessageKind.APPROVAL,
        markup=InlineKeyboardMarkup(inline_keyboard=[[confirm], [leave]]),
        related_id=reminder_id,
    )


async def _timezone(session) -> ZoneInfo:  # type: ignore[no-untyped-def]
    workspace = await session.get(Workspace, 1)
    return ZoneInfo(workspace.timezone if workspace else "UTC")


def _detail_text(reminder: Reminder, title: str, *, tz: ZoneInfo, now: datetime) -> str:
    schedule = schedule_of(reminder)
    lines = [
        f"<b>⏰ {html.escape(title)}</b>",
        f"{html.escape(reminder_when(reminder, tz=tz, now=now))} · "
        f"next {reminder.next_fire_at.astimezone(tz):%Y-%m-%d %H:%M}",
    ]
    if not schedule.repeating:
        lines.append("Fires once, then deletes itself.")
    lines.extend(["", html.escape(reminder.instruction), "", "<i>Timing is set through your advisor.</i>"])
    return "\n".join(lines)


async def _on_page(context: CallbackContext) -> None:
    await render_reminders(
        context.message, context.services, page=int(context.payload.get("page", 0))
    )


async def _on_view(context: CallbackContext) -> None:
    await render_reminder(
        context.message, context.services, int(context.payload["id"]), back=context.back
    )


async def _on_text_prompt(context: CallbackContext) -> None:
    await render_reminder_text_prompt(
        context.message, context.services, int(context.payload["id"]), back=context.back
    )


async def _on_delete_prompt(context: CallbackContext) -> None:
    await render_reminder_delete_prompt(
        context.message, context.services, int(context.payload["id"]), back=context.back
    )


async def _on_delete_confirm(context: CallbackContext) -> None:
    async with context.sessions() as session:
        await delete_reminder(session, int(context.payload["id"]))
        await session.commit()
    await go(context, context.back)


REMINDER_CALLBACK_ACTIONS: dict[str, CallbackHandler] = {
    "reminders_page": _on_page,
    "reminder_view": _on_view,
    "reminder_text_prompt": _on_text_prompt,
    "reminder_delete_prompt": _on_delete_prompt,
    "reminder_delete_confirm": _on_delete_confirm,
}
