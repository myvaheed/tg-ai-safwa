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
    Services,
    menu_row,
    send_registered,
    token_button,
)

from ...cards.telegram import kind_label
from ..api import request_cards
from ..model import SavedRequest

# How many matching Cards one Request screen lists before it only counts the rest.
REQUEST_RESULT_LIMIT = 25


async def command_requests(message: Message, services: Services) -> None:
    """Show AI-authored saved queries; creation intentionally remains advisor-only."""
    async with services.sessions() as session:
        requests = list(await session.scalars(select(SavedRequest).order_by(SavedRequest.name)))
        rows = [
            [
                await token_button(
                    session,
                    services.owner_id,
                    request.name,
                    "request_view",
                    {"id": request.id},
                )
            ]
            for request in requests
        ]
        rows.append(
            [
                await token_button(
                    session, services.owner_id, "❓ О Запросах", "request_about", {}
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


async def render_about_requests(message: Message, services: Services) -> None:
    async with services.sessions() as session:
        back = await token_button(session, services.owner_id, "↩️ Back", "request_list", {})
        await session.commit()
    await send_registered(
        message,
        services,
        ABOUT_REQUESTS,
        kind=MessageKind.DASHBOARD,
        markup=InlineKeyboardMarkup(inline_keyboard=[[back]]),
    )


async def render_saved_request(
    message: Message,
    services: Services,
    request_id: int,
    *,
    replace: bool | None = None,
) -> None:
    async with services.sessions() as session:
        request = await session.get(SavedRequest, request_id)
        if request is None:
            raise DomainError("Request no longer exists")
        matches = await request_cards(session, request.query_sql, services.views)
        cards = matches[:REQUEST_RESULT_LIMIT]
        rows = [
            [
                await token_button(
                    session,
                    services.owner_id,
                    f"{kind_label(card.kind)} · {card.title}"[:60],
                    "card_view",
                    {"id": card.id, "back": {"action": "request_view", "id": request.id}},
                )
            ]
            for card in cards
        ]
        rows.append(
            [
                await token_button(
                    session,
                    services.owner_id,
                    "↻ Refresh",
                    "request_view",
                    {"id": request.id},
                )
            ]
        )
        rows.append(menu_row())
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
    await render_saved_request(context.message, context.services, context.payload["id"])


async def _on_about(context: CallbackContext) -> None:
    await render_about_requests(context.message, context.services)


async def _on_list(context: CallbackContext) -> None:
    await command_requests(context.message, context.services)


REQUEST_CALLBACK_ACTIONS: dict[str, CallbackHandler] = {
    "request_view": _on_view,
    "request_about": _on_about,
    "request_list": _on_list,
}
