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
    Services,
    TextInputScreen,
    go_back_action,
    render_text_input,
    send_registered,
    token_button,
)
from tg_agent_shell.telegram.contributions import TextInputFlow

from ...schedules.api import SCHEDULE_INSTRUCTION
from ...schedules.telegram import compile_typed_schedule
from ..model import Check, CheckOutcome
from ..use_cases import delete_check, edit_check_schedule, resolve_check, toggle_check_value
from .screens import render_check, render_check_values, render_checks


def _back(context: CallbackContext) -> dict[str, Any]:
    return dict(context.payload.get("back") or {})


def _card_id(context: CallbackContext) -> int | None:
    """A Check opened by the advisor, or hanging on no Card, has no owning Card screen."""
    card_id = context.payload.get("card_id")
    return int(card_id) if card_id is not None else None


async def _on_list(context: CallbackContext) -> None:
    await render_checks(
        context.message,
        context.services,
        int(context.payload["card_id"]),
        back=_back(context),
    )


async def _on_choose_values(context: CallbackContext) -> None:
    await render_check_values(
        context.message,
        context.services,
        context.payload["id"],
        card_id=context.payload.get("card_id"),
        back=context.payload.get("back"),
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
        card_id=_card_id(context),
        back=_back(context),
    )


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
            back_action="check_view",
            back_payload=context.payload,
        ),
        state={"flow": "check_schedule", **context.payload},
    )


async def _prepare_schedule(
    services: Services, state: Mapping[str, Any], value: str
) -> dict[str, Any]:
    return await compile_typed_schedule(services, "check", value)


async def _apply_schedule(
    session: AsyncSession, services: Services, state: Mapping[str, Any], value: dict[str, Any]
) -> None:
    await edit_check_schedule(session, int(state["id"]), value["text"], value["rule"])


async def _render_schedule(
    message: Message, services: Services, state: Mapping[str, Any], value: dict[str, Any]
) -> None:
    await render_check(
        message,
        services,
        int(state["id"]),
        card_id=state.get("card_id"),
        back=state.get("back"),
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
        confirm = await token_button(
            session,
            context.owner_id,
            "Permanently delete Check",
            "check_delete_confirm",
            context.payload,
        )
        back = await token_button(
            session, context.owner_id, "↩️ Back", "check_view", context.payload
        )
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
    # Back to whatever list the Check was opened from: the Check itself is gone.
    await _on_list(context)


async def _on_set_status(context: CallbackContext) -> None:
    notice = None
    async with context.sessions() as session:
        check = await session.get(Check, int(context.payload["id"]))
        if check is None:
            raise DomainError("Check does not exist")
        _answered, successor = await resolve_check(
            session, check.id, CheckOutcome(str(context.payload["outcome"]))
        )
        await session.commit()
        if successor is not None:
            notice = "A new Pending Check was created for the next round."
    await render_check(
        context.message,
        context.services,
        int(context.payload["id"]),
        card_id=_card_id(context),
        back=_back(context),
        notice=notice,
    )


CHECK_CALLBACK_ACTIONS: dict[str, CallbackHandler] = {
    # The Card screen's button into its Checks, and the same list from one Check.
    "check_list": _on_list,
    "check_choose_values": _on_choose_values,
    "check_toggle_value": _on_toggle_value,
    "check_view": _on_view,
    "check_edit_schedule": _on_edit_schedule,
    "check_set_status": _on_set_status,
    "check_delete_prompt": _on_delete_prompt,
    "check_delete_confirm": _on_delete_confirm,
    "check_back": go_back_action,
}
