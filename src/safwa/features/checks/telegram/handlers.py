"""What a tap on a Check does: open it, answer it, repeat it, or delete it."""

from __future__ import annotations

import html
from collections.abc import Mapping
from typing import Any

from aiogram.types import InlineKeyboardMarkup, Message
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
    edited_screen,
    go,
    place_button,
    render_text_input,
    send_registered,
)
from tg_agent_shell.telegram.contributions import TextInputFlow

from ...schedules.api import SCHEDULE_INSTRUCTION
from ...schedules.telegram import compile_typed_schedule, render_schedule
from ..model import Check, CheckOutcome
from ..use_cases import delete_check, resolve_check, toggle_check_value, update_check_fields
from .screens import render_check, render_check_values, render_checks


def _check(context: CallbackContext) -> Place:
    """The Check a press on its screen was made on."""
    return Place("check_view", {"id": int(context.payload["id"])}, context.back)


async def _on_list(context: CallbackContext) -> None:
    await render_checks(
        context.message,
        context.services,
        int(context.payload["card_id"]),
        back=context.back,
    )


async def _on_choose_values(context: CallbackContext) -> None:
    await render_check_values(
        context.message,
        context.services,
        context.payload["id"],
        back=context.back,
        page=int(context.payload.get("page", 0)),
    )


async def _on_toggle_value(context: CallbackContext) -> None:
    async with context.sessions() as session:
        await toggle_check_value(session, context.payload["id"], context.payload["value_id"])
        await session.commit()
    await _on_choose_values(context)


async def _on_view(context: CallbackContext) -> None:
    await render_check(
        context.message,
        context.services,
        int(context.payload["id"]),
        back=context.back,
        notice=context.payload.get("notice"),
    )


async def _on_open_schedule(context: CallbackContext) -> None:
    """A Schedule with a clock still ahead offers Remind beside Edit; any other opens its
    editor at once."""
    shown = await render_schedule(
        context.message,
        context.services,
        Check,
        int(context.payload["id"]),
        edit=Place("check_edit_schedule", {"id": int(context.payload["id"])}, context.back),
        back=_check(context),
    )
    if not shown:
        await _on_edit_schedule(context)


async def _on_edit_schedule(context: CallbackContext) -> None:
    async with context.sessions() as session:
        check = await session.get(Check, int(context.payload["id"]))
        if check is None:
            raise DomainError("Check does not exist")
        current = check.schedule or ""
    await render_text_input(
        context.message,
        context.services,
        screen=TextInputScreen(
            title="Edit Check Schedule",
            current_value=current,
            instruction=SCHEDULE_INSTRUCTION,
            back=_check(context),
        ),
        state={"flow": "check_schedule", "id": int(context.payload["id"])},
    )


async def _prepare_schedule(
    message: Message, services: Services, state: Mapping[str, Any], value: str
) -> dict[str, Any]:
    return await compile_typed_schedule(message, services, "check", value)


async def _apply_schedule(
    session: AsyncSession, services: Services, state: Mapping[str, Any], value: dict[str, Any]
) -> None:
    await update_check_fields(
        session, int(state["id"]), {"schedule": value["text"], "schedule_rule": value["rule"]}
    )


async def _render_schedule(
    message: Message, services: Services, state: Mapping[str, Any], value: dict[str, Any]
) -> None:
    await render_check(
        message,
        services,
        int(state["id"]),
        back=edited_screen(state).back,
        replace_message_id=int(state["text_input"]["message_id"]),
    )


CHECK_TEXT_INPUTS = (
    TextInputFlow(
        name="check_schedule",
        validator=lambda state: None,
        apply=_apply_schedule,
        render=_render_schedule,
        prepare=_prepare_schedule,
    ),
)


async def _on_delete_prompt(context: CallbackContext) -> None:
    async with context.sessions() as session:
        check = await session.get(Check, int(context.payload["id"]))
        if check is None:
            raise DomainError("Check does not exist")
        confirm = await place_button(
            session,
            context.owner_id,
            "Permanently delete Check",
            Place("check_delete_confirm", {"id": check.id}, context.back),
        )
        back = await back_button(session, context.owner_id, _check(context))
        title = check.title
        await session.commit()
    await send_registered(
        context.message,
        context.services,
        f"<b>Delete Check?</b>\n{html.escape(title)} will be deleted, answered or not. "
        f"Its Card stays, and so do the Values it pointed at.",
        kind=MessageKind.APPROVAL,
        markup=InlineKeyboardMarkup(inline_keyboard=[[confirm], [back]]),
    )


async def _on_delete_confirm(context: CallbackContext) -> None:
    async with context.sessions() as session:
        await delete_check(session, int(context.payload["id"]))
        await session.commit()
    # Back to wherever the Check was opened from: its list, its Card, or the menu.
    await go(context, context.back)


async def _on_set_status(context: CallbackContext) -> None:
    async with context.sessions() as session:
        _answered, successor = await resolve_check(
            session, int(context.payload["id"]), CheckOutcome(str(context.payload["outcome"]))
        )
        await session.commit()
    notice = "A new Pending Check was created for the next round." if successor else None
    await render_check(
        context.message,
        context.services,
        int(context.payload["id"]),
        back=context.back,
        notice=notice,
    )


CHECK_CALLBACK_ACTIONS: dict[str, CallbackHandler] = {
    # The Card screen's button into its Checks, and the same list from one Check.
    "check_list": _on_list,
    "check_choose_values": _on_choose_values,
    "check_toggle_value": _on_toggle_value,
    "check_view": _on_view,
    "check_open_schedule": _on_open_schedule,
    "check_edit_schedule": _on_edit_schedule,
    "check_set_status": _on_set_status,
    "check_delete_prompt": _on_delete_prompt,
    "check_delete_confirm": _on_delete_confirm,
}
