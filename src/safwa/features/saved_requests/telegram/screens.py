"""The Requests the owner keeps: the list, and one Request run against the workspace."""

from __future__ import annotations

import html
from collections import Counter

from aiogram.types import InlineKeyboardMarkup, Message
from sqlalchemy import select

from telegram_llm import markdown_to_telegram_html
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
    render_citations,
    send_registered,
)

from ....foundation.marks import title_marks
from ...cards.model import Card
from ...cards.telegram import RESULT_LIMIT, item_button_label
from ...cards.telegram.presentation import ITEM_BUTTON_LIMIT, result_order
from ...checks.model import Check
from ...checks.telegram.screens import CHECK_STATUS_EMOJIS
from ...values.model import Value
from ..api import RequestQueryError, explicit_order, request_items
from ..model import SavedRequest

# The Requests list, which the menu opens and every Request opened from it goes back to.
_LIST = Place("request_list")


def _result_summary(items: list[Card | Check | Value]) -> str:
    counts = Counter(
        item.kind if isinstance(item, Card) else "check" if isinstance(item, Check) else "value"
        for item in items
    )
    return " · ".join(
        f"{emoji}{counts[kind]}"
        for kind, emoji in (("goal", "🎯"), ("action", "⭐️"), ("thought", "💭"), ("check", "☑️"), ("value", "💎"))
        if counts[kind]
    ) or "0"


async def command_requests(message: Message, services: Services) -> None:
    """Show AI-authored saved queries; creation intentionally remains advisor-only."""
    async with services.sessions() as session:
        requests = list(await session.scalars(select(SavedRequest).order_by(SavedRequest.name)))
        rows = []
        for request in requests:
            try:
                summary = _result_summary(
                    await request_items(session, request.query_sql, services.views)
                )
            except RequestQueryError:
                summary = "⚠️"
            name = request.name[:ITEM_BUTTON_LIMIT - len(summary) - 3]
            rows.append(
                [
                    await place_button(
                        session,
                        services.owner_id,
                        f"{name} · {summary}",
                        _LIST.child("request_view", id=request.id),
                    )
                ]
            )
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
        "<b>Requests</b>\nSaved Card, Check and Value queries created by your advisor.",
        kind=MessageKind.DASHBOARD,
        markup=InlineKeyboardMarkup(inline_keyboard=rows + [menu_row()]),
    )


# Written for the owner rather than for the model, so it is their language and their words.
ABOUT_REQUESTS = (
    "<b>О Запросах</b>\n"
    "Запрос — это подборка Карточек, Чеков и Ценностей по условию. Например, «Goals without children» показывает Цели без детей, "
    "а запрос «Дела дома» может находить Действия с Тегом «дом». При каждом открытии "
    "подборка собирается заново. Иконки 🎯, ⭐️, 💭, ☑️ и 💎 после названия показывают количество "
    "найденных Целей, Действий, Мыслей, Чеков и Ценностей.\n"
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
        matches = await request_items(session, request.query_sql, services.views)
        shown = matches
        if not explicit_order(request.query_sql):
            cards = await result_order(session, [item for item in matches if isinstance(item, Card)])
            shown = [*cards, *(item for item in matches if not isinstance(item, Card))]
        rows = []
        for item in shown[:RESULT_LIMIT]:
            if isinstance(item, Card):
                label = await item_button_label(session, item)
                view = "card_view"
            elif isinstance(item, Check):
                marker = await title_marks(session, item)
                suffix = f"{marker} · {CHECK_STATUS_EMOJIS[item.status]}"
                title = item.title[:ITEM_BUTTON_LIMIT - len(suffix) - 3]
                label = f"☑️ {title}{suffix}"
                view = "check_view"
            else:
                label = f"💎 {item.name}"[:ITEM_BUTTON_LIMIT]
                view = "value_view"
            rows.append(
                [await place_button(
                    session, services.owner_id, label, here.child(view, id=item.id)
                )]
            )
        rows.append([await place_button(session, services.owner_id, "↻ Refresh", here)])
        rows.append([await back_button(session, services.owner_id, back)])
        markup = InlineKeyboardMarkup(inline_keyboard=rows)
        count = _result_summary(matches)
        if len(matches) > RESULT_LIMIT:
            count += f" (showing first {RESULT_LIMIT})"
        description = await render_citations(
            session, services, markdown_to_telegram_html(request.description or "No description."), here=here
        )
        await session.commit()
    await send_registered(
        message,
        services,
        f"<b>{html.escape(request.name)}</b>\n\n{description}\n\n{count}",
        kind=MessageKind.DASHBOARD,
        markup=markup,
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
