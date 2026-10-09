"""The Requests the owner keeps: the list, and one Request run against the workspace."""

from __future__ import annotations

import html

from aiogram.types import InlineKeyboardMarkup, Message
from sqlalchemy import select

from tg_agent_shell.foundation.errors import DomainError
from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.telegram import (
    CallbackContext,
    CallbackHandler,
    Place,
    Services,
    back_button,
    menu_row,
    place_button,
    send_registered,
)

from ...cards.telegram import item_button_label
from ..api import request_cards
from ..model import SavedRequest

# How many matching Cards one Request screen lists before it only counts the rest.
REQUEST_RESULT_LIMIT = 25

# The Requests list, which the menu opens and every Request opened from it goes back to.
_LIST = Place("request_list")


async def command_requests(message: Message, services: Services) -> None:
    """Show AI-authored saved queries; creation intentionally remains advisor-only."""
    async with services.sessions() as session:
        requests = list(await session.scalars(select(SavedRequest).order_by(SavedRequest.name)))
        rows = [
            [
                await place_button(
                    session,
                    services.owner_id,
                    request.name,
                    _LIST.child("request_view", id=request.id),
                )
            ]
            for request in requests
        ]
        rows.append(
            [
                await place_button(
                    session, services.owner_id, "❓ О Запросах", _LIST.child("request_about")
                )
            ]
        )
        await session.commit()
    await send_registered(
        message,
        services,
        "<b>Requests</b>\nSaved card queries created by your advisor.",
        kind=MessageKind.DASHBOARD,
        markup=InlineKeyboardMarkup(inline_keyboard=rows + [menu_row()]),
    )


# Written for the owner rather than for the model, so it is their language and their words.
ABOUT_REQUESTS = (
    "<b>О Запросах</b>\n"
    "Запрос — это подборка карточек по условию. Например, «Все цели» показывает ваши Цели, "
    "а запрос «Дела дома» может находить Действия с Тегом «дом». При каждом открытии "
    "подборка собирается заново.\n"
    "Теги помогают собрать карточки из разных Целей в одну подборку.\n"
    "Запросы пишет Safwa: попросите её собрать нужную подборку — после сохранения она "
    "открывается одной кнопкой."
)


async def render_about_requests(
    message: Message, services: Services, *, back: Place | None
) -> None:
    async with services.sessions() as session:
        leave = await back_button(session, services.owner_id, back)
        await session.commit()
    await send_registered(
        message,
        services,
        ABOUT_REQUESTS,
        kind=MessageKind.DASHBOARD,
        markup=InlineKeyboardMarkup(inline_keyboard=[[leave]]),
    )


async def render_saved_request(
    message: Message,
    services: Services,
    request_id: int,
    *,
    back: Place | None = None,
    replace: bool | None = None,
) -> None:
    """One Request run against the workspace now. `back` is where it was opened from."""
    here = Place("request_view", {"id": request_id}, back)
    async with services.sessions() as session:
        request = await session.get(SavedRequest, request_id)
        if request is None:
            raise DomainError("Request no longer exists")
        matches = await request_cards(session, request.query_sql, services.views)
        cards = matches[:REQUEST_RESULT_LIMIT]
        rows = [
            [
                await place_button(
                    session,
                    services.owner_id,
                    await item_button_label(session, card),
                    here.child("card_view", id=card.id),
                )
            ]
            for card in cards
        ]
        rows.append([await place_button(session, services.owner_id, "↻ Refresh", here)])
        rows.append([await back_button(session, services.owner_id, back)])
        await session.commit()
    details = request.description or "No description."
    details += f"\n\n{len(matches)} matching card{'s' if len(matches) != 1 else ''}"
    if len(matches) > REQUEST_RESULT_LIMIT:
        details += f" (showing first {REQUEST_RESULT_LIMIT})"
    await send_registered(
        message,
        services,
        f"<b>{html.escape(request.name)}</b>\n{html.escape(details)}",
        kind=MessageKind.DASHBOARD,
        markup=InlineKeyboardMarkup(inline_keyboard=rows),
        related_id=request.id,
        replace=replace,
    )


async def _on_view(context: CallbackContext) -> None:
    await render_saved_request(
        context.message, context.services, context.payload["id"], back=context.back
    )


async def _on_about(context: CallbackContext) -> None:
    await render_about_requests(context.message, context.services, back=context.back)


async def _on_list(context: CallbackContext) -> None:
    await command_requests(context.message, context.services)


REQUEST_CALLBACK_ACTIONS: dict[str, CallbackHandler] = {
    "request_view": _on_view,
    "request_about": _on_about,
    "request_list": _on_list,
}
