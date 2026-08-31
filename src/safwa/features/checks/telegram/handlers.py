"""What a tap on a Check does: open it, answer it, repeat it, or delete it."""

from __future__ import annotations

import html

from aiogram.types import InlineKeyboardMarkup

from ....enums import MessageKind
from ....foundation.errors import DomainError
from ....models import Check
from ....shell import (
    CallbackContext,
    CallbackHandler,
    go_back_action,
    send_registered,
    token_button,
)
from ..model import CheckOutcome
from ..use_cases import delete_check, resolve_check, toggle_check_value, update_check_fields
from .screens import render_check, render_check_values, render_checks


def _back(context: CallbackContext) -> dict:
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


async def _on_toggle_repeat(context: CallbackContext) -> None:
    async with context.sessions() as session:
        check = await session.get(Check, int(context.payload["id"]))
        if check is None:
            raise DomainError("Check does not exist")
        await update_check_fields(session, check.id, {"repeatable": not check.repeatable})
        await session.commit()
    await _on_view(context)


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
    "check_toggle_repeat": _on_toggle_repeat,
    "check_set_status": _on_set_status,
    "check_delete_prompt": _on_delete_prompt,
    "check_delete_confirm": _on_delete_confirm,
    "check_back": go_back_action,
}
