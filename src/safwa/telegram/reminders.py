"""The `/reminders` screens: a list, a detail view, a text edit, a delete confirmation.

Text is the only editable field here.  A schedule is resolved from plain words by a model
session and then computed in code, so there is no sensible manual form for it — the advisor
changes timing, and this screen says so.  There is no creation button either, for the same
reason Requests has none.
"""

from __future__ import annotations

import html
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram.types import InlineKeyboardMarkup, Message
from sqlalchemy import select

from ..domain import DomainError
from ..enums import MessageKind
from ..features.reminders.schedule import describe, schedule_of
from ..models import Reminder, Workspace
from ._core import Services
from ._messaging import edit_registered_message, paging_row, send_registered, token_button
from ._presentation import menu_row, paginate
from .text_input import TextInputScreen, render_text_input

_TEXT_PREVIEW = 40
_PROMPT_TTL = timedelta(minutes=30)


async def render_reminders(message: Message, services: Services, *, page: int = 0) -> None:
    async with services.sessions() as session:
        tz = await _timezone(session)
        now = datetime.now(UTC)
        reminders = list(
            await session.scalars(
                select(Reminder)
                .where(Reminder.system.is_(False))
                .order_by(Reminder.next_fire_at)
            )
        )
        window = paginate(reminders, page)
        rows = [
            [
                await token_button(
                    session,
                    services.owner_id,
                    _list_label(reminder, tz=tz, now=now),
                    "reminder_view",
                    {"id": reminder.id},
                )
            ]
            for reminder in window.items
        ]
        rows.extend(await paging_row(session, services.owner_id, window, "reminders_page", {}))
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
    replace_message_id: int | None = None,
) -> None:
    async with services.sessions() as session:
        reminder = await session.get(Reminder, reminder_id)
        if reminder is None:
            raise DomainError("Reminder does not exist")
        tz = await _timezone(session)
        text = _detail_text(reminder, tz=tz, now=datetime.now(UTC))
        edit = await token_button(
            session, services.owner_id, "✏️ Text", "reminder_text_prompt", {"id": reminder.id}
        )
        remove = await token_button(
            session, services.owner_id, "🗑 Delete", "reminder_delete_prompt", {"id": reminder.id}
        )
        back = await token_button(
            session, services.owner_id, "↩️ Back", "reminders_page", {"page": 0}
        )
        await session.commit()
    markup = InlineKeyboardMarkup(inline_keyboard=[[edit, remove], [back]])
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
        )


async def render_reminder_text_prompt(
    message: Message, services: Services, reminder_id: int
) -> None:
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
            back_action="reminder_view",
            back_payload={"id": reminder_id},
            related_id=reminder_id,
        ),
        state={"flow": "reminder", "reminder_id": reminder_id},
    )


async def render_reminder_delete_prompt(
    message: Message, services: Services, reminder_id: int
) -> None:
    async with services.sessions() as session:
        reminder = await session.get(Reminder, reminder_id)
        if reminder is None:
            raise DomainError("Reminder does not exist")
        tz = await _timezone(session)
        schedule = describe(schedule_of(reminder), tz=tz, now=datetime.now(UTC))
        confirm = await token_button(
            session, services.owner_id, "Delete Reminder", "reminder_delete_confirm", {"id": reminder_id}
        )
        back = await token_button(
            session, services.owner_id, "↩️ Back", "reminder_view", {"id": reminder_id}
        )
        await session.commit()
    await send_registered(
        message,
        services,
        f"<b>Delete this Reminder?</b>\n{html.escape(schedule)}\n"
        f"{html.escape(reminder.instruction)}\n\n"
        "It stops firing immediately. There is no archive for a Reminder.",
        kind=MessageKind.APPROVAL,
        markup=InlineKeyboardMarkup(inline_keyboard=[[confirm], [back]]),
        related_id=reminder_id,
    )


async def _timezone(session) -> ZoneInfo:  # type: ignore[no-untyped-def]
    workspace = await session.get(Workspace, 1)
    return ZoneInfo(workspace.timezone if workspace else "UTC")


def _list_label(reminder: Reminder, *, tz: ZoneInfo, now: datetime) -> str:
    preview = reminder.instruction.strip().splitlines()[0] if reminder.instruction else ""
    if len(preview) > _TEXT_PREVIEW:
        preview = preview[: _TEXT_PREVIEW - 1].rstrip() + "…"
    return f"{describe(schedule_of(reminder), tz=tz, now=now)} · {preview}"


def _detail_text(reminder: Reminder, *, tz: ZoneInfo, now: datetime) -> str:
    schedule = schedule_of(reminder)
    lines = [
        "<b>Reminder</b>",
        f"{html.escape(describe(schedule, tz=tz, now=now))} · "
        f"next {reminder.next_fire_at.astimezone(tz):%Y-%m-%d %H:%M}",
    ]
    if not schedule.repeating:
        lines.append("Fires once, then deletes itself.")
    lines.extend(["", html.escape(reminder.instruction), "", "<i>Timing is set through your advisor.</i>"])
    return "\n".join(lines)
