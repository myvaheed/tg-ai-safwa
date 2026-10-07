"""A Schedule typed into an editor is compiled before it is written, and a Schedule with a
clock offers Remind beside Edit."""

from __future__ import annotations

import html
from typing import Any

from aiogram.enums import ChatAction
from aiogram.types import InlineKeyboardMarkup, Message

from tg_agent_shell.foundation.clock import utcnow
from tg_agent_shell.foundation.errors import DomainError
from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.telegram import (
    CallbackContext,
    Place,
    Services,
    back_button,
    place_button,
    send_registered,
)

from ..cards.model import Card
from ..checks.model import Check
from .api import (
    ScheduleTarget,
    entity_reminder,
    item_label,
    item_type,
    remind_timing,
    schedule_summary,
    schedule_target,
    workspace_zone,
)
from .use_cases import set_remind


async def compile_typed_schedule(
    message: Message, services: Services, target: ScheduleTarget, text: str
) -> dict[str, Any]:
    """The text and its rule. A question from the Scheduler refuses the value, so the
    editor shows it and keeps waiting; off clears the Schedule."""
    if text.casefold() == "off":
        return {"text": None, "rule": None}
    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    async with services.sessions() as session:
        tz = await workspace_zone(session)
    rule, question = await services.features.schedule_compiler.compile(text, utcnow(), tz, target)
    if question:
        raise DomainError(question)
    return {"text": text, "rule": rule}


async def render_schedule(
    message: Message,
    services: Services,
    model: type[Card] | type[Check],
    item_id: int,
    *,
    edit: Place,
    back: Place,
) -> bool:
    """The Schedule with Remind beside Edit while it has a clock still ahead or Remind is
    on. False draws nothing, and the caller opens the editor at once. `back` is the item's
    own screen, and `edit` the editor Edit opens."""
    async with services.sessions() as session:
        entity = await session.get(model, item_id)
        if entity is None:
            raise DomainError(f"{model.__name__} does not exist")
        on = await entity_reminder(session, entity) is not None
        if not on and remind_timing(entity, utcnow()) is None:
            return False
        summary = await schedule_summary(session, entity)
        toggle = Place(
            "schedule_remind",
            {"type": item_type(model), "id": item_id, "on": not on, "edit": edit.address},
            back,
        )
        rows = [
            [
                await place_button(
                    session, services.owner_id, f"🔔 Remind: {'On' if on else 'Off'}", toggle
                )
            ],
            [await place_button(session, services.owner_id, "✏️ Edit", edit)],
            [await back_button(session, services.owner_id, back)],
        ]
        await session.commit()
    noun = "Deadline" if schedule_target(entity) == "deadline" else "Schedule"
    lines = [f"<b>{item_label(entity)} {noun}</b>", html.escape(entity.schedule or "—")]
    if summary:
        lines.append(html.escape(summary))
    await send_registered(
        message,
        services,
        "\n".join(lines),
        kind=MessageKind.EDITOR,
        markup=InlineKeyboardMarkup(inline_keyboard=rows),
        related_id=item_id,
    )
    return True


async def _on_remind(context: CallbackContext) -> None:
    model = Card if context.payload["type"] == item_type(Card) else Check
    item_id = int(context.payload["id"])
    async with context.sessions() as session:
        entity = await session.get(model, item_id)
        if entity is None:
            raise DomainError(f"{model.__name__} does not exist")
        await set_remind(session, entity, bool(context.payload["on"]))
        await session.commit()
    await render_schedule(
        context.message,
        context.services,
        model,
        item_id,
        edit=Place.at(context.payload["edit"]),
        back=context.back,
    )


SCHEDULE_CALLBACK_ACTIONS = {"schedule_remind": _on_remind}
