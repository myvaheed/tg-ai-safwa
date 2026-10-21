"""A Schedule typed into an editor is compiled before it is written, and a Schedule with a
clock offers Remind beside Edit."""

from __future__ import annotations

import html
from collections.abc import Callable, Mapping
from typing import Any

from aiogram.enums import ChatAction
from aiogram.types import InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.ai.contracts import AgentChange
from tg_agent_shell.foundation.clock import utcnow
from tg_agent_shell.foundation.errors import DomainError
from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.proposals.api import EditableField
from tg_agent_shell.telegram import (
    CallbackContext,
    Place,
    Services,
    TextInputScreen,
    back_button,
    edit_registered_message,
    edited_screen,
    go,
    place_button,
    render_text_input,
    send_registered,
)
from tg_agent_shell.telegram.contributions import TextInputFlow

from ...constants import REMIND_LEAD_MAX_MINUTES
from ..cards.model import Card
from ..checks.model import Check
from .agent import schedule_cleared
from .api import (
    REMIND_LEAD_RANGE,
    ScheduleTarget,
    entity_reminder,
    item_label,
    item_type,
    remind_label,
    remind_timing,
    schedule_summary,
    schedule_target,
    workspace_zone,
)
from .use_cases import set_remind

# The leads Remind offers as buttons, in minutes; Other takes any other.
REMIND_LEADS = (0, 15, 60, 24 * 60)
REMIND_INSTRUCTION = (
    f"Send how many minutes before its time to remind, 0 to {REMIND_LEAD_MAX_MINUTES}."
)


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


def _item(payload: Mapping[str, Any]) -> tuple[type[Card] | type[Check], int]:
    return (Card if payload["type"] == item_type(Card) else Check), int(payload["id"])


async def _entity(session: AsyncSession, model: type[Card] | type[Check], item_id: int) -> Card | Check:
    entity = await session.get(model, item_id)
    if entity is None:
        raise DomainError(f"{model.__name__} does not exist")
    return entity


async def render_schedule(
    message: Message,
    services: Services,
    model: type[Card] | type[Check],
    item_id: int,
    *,
    edit: Place,
    back: Place,
    replace_message_id: int | None = None,
) -> bool:
    """The Schedule with Remind beside Edit while it has a clock still ahead or Remind is
    on. False draws nothing, and the caller opens the editor at once. `back` is the item's
    own screen, and `edit` the editor Edit opens."""
    async with services.sessions() as session:
        entity = await _entity(session, model, item_id)
        reminder = await entity_reminder(session, entity)
        if reminder is None and remind_timing(entity, utcnow()) is None:
            return False
        summary = await schedule_summary(session, entity)
        here = Place("schedule_view", {"type": item_type(model), "id": item_id, "edit": edit.address}, back)
        remind = remind_label(reminder.lead_minutes if reminder else None)
        rows = [
            [
                await place_button(
                    session,
                    services.owner_id,
                    f"🔔 Remind: {remind}",
                    here.child("schedule_remind", **here.args),
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
    markup = InlineKeyboardMarkup(inline_keyboard=rows)
    if replace_message_id is not None:
        await edit_registered_message(
            message,
            services,
            replace_message_id,
            "\n".join(lines),
            kind=MessageKind.EDITOR,
            markup=markup,
            related_id=item_id,
        )
        return True
    await send_registered(
        message,
        services,
        "\n".join(lines),
        kind=MessageKind.EDITOR,
        markup=markup,
        related_id=item_id,
    )
    return True


async def _on_view(context: CallbackContext) -> None:
    model, item_id = _item(context.payload)
    edit = Place.at(context.payload["edit"])
    shown = await render_schedule(
        context.message, context.services, model, item_id, edit=edit, back=context.back
    )
    if not shown:
        await go(context, edit)


async def _on_remind(context: CallbackContext) -> None:
    """Remind's choices, the one in force marked. `context.back` is the Schedule."""
    model, item_id = _item(context.payload)
    args = context.place.args
    async with context.sessions() as session:
        entity = await _entity(session, model, item_id)
        reminder = await entity_reminder(session, entity)
        current = reminder.lead_minutes if reminder else None
        rows = [
            [
                await place_button(
                    session,
                    context.owner_id,
                    ("✅ " if lead == current else "") + remind_label(lead),
                    Place("schedule_remind_set", {**args, "lead": lead}, context.back),
                )
            ]
            for lead in (None, *REMIND_LEADS)
        ]
        rows.append(
            [
                await place_button(
                    session,
                    context.owner_id,
                    "✏️ Other",
                    Place("schedule_remind_other", args, context.back),
                )
            ]
        )
        rows.append([await back_button(session, context.owner_id, context.back)])
        text = (
            f"<b>Remind</b>\nWhen should Safwa remind you of {item_label(entity)} "
            f"«{html.escape(entity.title)}»?"
        )
        await session.commit()
    await send_registered(
        context.message,
        context.services,
        text,
        kind=MessageKind.EDITOR,
        markup=InlineKeyboardMarkup(inline_keyboard=rows),
        related_id=item_id,
    )


async def _on_remind_set(context: CallbackContext) -> None:
    model, item_id = _item(context.payload)
    async with context.sessions() as session:
        await set_remind(session, await _entity(session, model, item_id), context.payload["lead"])
        await session.commit()
    await go(context, context.back)


async def _on_remind_other(context: CallbackContext) -> None:
    model, item_id = _item(context.payload)
    async with context.sessions() as session:
        reminder = await entity_reminder(session, await _entity(session, model, item_id))
        current = remind_label(reminder.lead_minutes if reminder else None)
    await render_text_input(
        context.message,
        context.services,
        screen=TextInputScreen(
            title="Remind",
            current_value=current,
            instruction=REMIND_INSTRUCTION,
            back=context.back,
            related_id=item_id,
        ),
        state={"flow": "schedule_remind", "type": context.payload["type"], "id": item_id},
    )


def _lead(raw: str) -> int:
    if not raw.isdigit() or int(raw) > REMIND_LEAD_MAX_MINUTES:
        raise ValueError(REMIND_LEAD_RANGE)
    return int(raw)


async def _remind_options(session: AsyncSession, item_id: int | None) -> list[tuple[str, Any]]:
    del session, item_id
    return [(remind_label(lead), lead) for lead in (None, *REMIND_LEADS)]


# Remind on a proposal that sets it, with the choices and Other its Schedule offers.
REMIND_REVIEW_FIELD = EditableField(
    "🔔 Remind",
    "Remind",
    options=_remind_options,
    parse=_lead,
    instruction=REMIND_INSTRUCTION,
)


def _set_schedule(call: AgentChange, text: Any) -> AgentChange:
    """Remind needs a time, so a Schedule cleared by hand takes the proposal's Remind with it."""
    values = {**call.values, "schedule": text}
    if schedule_cleared(text):
        values.pop("remind", None)
    return call.model_copy(update={"values": values})


def schedule_review_field(
    label: str, title: str, instruction: str, parse: Callable[[str], Any] | None = None
) -> EditableField:
    """A Schedule or Deadline typed under Edit, with the words its own editor asks for."""
    return EditableField(label, title, parse=parse, instruction=instruction, write=_set_schedule)


async def _apply_lead(
    session: AsyncSession, services: Services, state: Mapping[str, Any], value: int
) -> None:
    model, item_id = _item(state)
    await set_remind(session, await _entity(session, model, item_id), value)


async def _render_lead(
    message: Message, services: Services, state: Mapping[str, Any], value: int
) -> None:
    schedule = edited_screen(state)
    model, item_id = _item(state)
    await render_schedule(
        message,
        services,
        model,
        item_id,
        edit=Place.at(schedule.args["edit"]),
        back=schedule.back,
        replace_message_id=int(state["text_input"]["message_id"]),
    )


SCHEDULE_CALLBACK_ACTIONS = {
    "schedule_view": _on_view,
    "schedule_remind": _on_remind,
    "schedule_remind_set": _on_remind_set,
    "schedule_remind_other": _on_remind_other,
}
SCHEDULE_TEXT_INPUTS = (
    TextInputFlow(
        name="schedule_remind",
        validator=lambda state: _lead,
        apply=_apply_lead,
        render=_render_lead,
    ),
)
