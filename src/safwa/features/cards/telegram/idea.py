"""The Idea: a title, a note, and the one button that turns it into Cards.

An Idea is outside the tree and outside every stage, so it carries none of the Card screen's
controls and it has a list of its own.  Expanding it is an ordinary owner turn: the request
goes into the chat as the owner's own words, and Safwa reads the Idea and proposes whatever
tree it would make of it — no screen decides which kinds that is.
"""

from __future__ import annotations

import html
from datetime import UTC, datetime, timedelta
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from telegram_llm import HistoryEntry
from tg_agent_shell.foundation.errors import DomainError
from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.telegram import (
    CallbackContext,
    CallbackHandler,
    Services,
    menu_row,
    paginate,
    paging_row,
    send_owner_turn,
    send_registered,
    token_button,
    with_notice,
)
from tg_agent_shell.telegram.dialogue import run_dialogue_turn
from tg_agent_shell.telegram.model import UiSession

from ..model import Card, CardKind
from .creation import start_manual_card_creation

# The Ideas list is the newest first: an Idea is written to be picked up again soon.
IDEA_LIST_BACK = {"action": "idea_list"}


async def _ideas(session: AsyncSession) -> list[Card]:
    return list(
        await session.scalars(
            select(Card).where(Card.kind == CardKind.IDEA.value).order_by(Card.created_at.desc())
        )
    )


async def render_ideas(
    message: Message, services: Services, *, page: int = 0, notice: str | None = None
) -> None:
    async with services.sessions() as session:
        current = paginate(await _ideas(session), page)
        rows: list[list[InlineKeyboardButton]] = []
        descriptions: list[str] = []
        for idea in current.items:
            descriptions.append(f"• {idea.title}")
            rows.append(
                [
                    await token_button(
                        session,
                        services.owner_id,
                        idea.title[:60],
                        "card_view",
                        {"id": idea.id, "back": {**IDEA_LIST_BACK, "page": current.index}},
                    )
                ]
            )
        rows.extend(await paging_row(session, services.owner_id, current, "idea_list", {}))
        rows.append(
            [await token_button(session, services.owner_id, "➕ New Idea", "idea_new", {})]
        )
        rows.append(menu_row())
        await session.commit()
    body = "\n".join(html.escape(description) for description in descriptions)
    await send_registered(
        message,
        services,
        with_notice(
            f"<b>Ideas</b> · {current.label}\n" + (body or "Nothing here yet."), notice
        ),
        kind=MessageKind.DASHBOARD,
        markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


async def command_ideas(message: Message, services: Services) -> None:
    await render_ideas(message, services)


async def render_idea(
    message: Message,
    services: Services,
    idea: Card,
    *,
    back: dict[str, Any],
    notice: str | None = None,
    replace: bool | None = None,
) -> None:
    """One Idea: its two fields, the button that expands it, and Delete."""
    async with services.sessions() as session:
        rows = [
            [
                await token_button(
                    session,
                    services.owner_id,
                    "✏️ Title",
                    "card_edit_text",
                    {"id": idea.id, "field": "title"},
                ),
                await token_button(
                    session,
                    services.owner_id,
                    "📝 Note",
                    "card_edit_text",
                    {"id": idea.id, "field": "note"},
                ),
            ],
            [
                await token_button(
                    session, services.owner_id, "✨ Expand", "idea_expand", {"id": idea.id}
                )
            ],
            [
                await token_button(
                    session, services.owner_id, "Delete", "card_delete_prompt", {"id": idea.id}
                )
            ],
            [
                await token_button(
                    session, services.owner_id, "↩️ Back", "card_back", {"back": back}
                )
            ],
        ]
        await session.execute(delete(UiSession).where(UiSession.owner_id == services.owner_id))
        session.add(
            UiSession(
                owner_id=services.owner_id,
                kind="card_editor",
                state={"card_id": idea.id, "back": back, "message_id": message.message_id},
                expires_at=datetime.now(UTC) + timedelta(minutes=30),
            )
        )
        await session.commit()
    text = (
        "<b>Idea</b>\n"
        f"Title: <b>{html.escape(idea.title)}</b>\n"
        f"Note: {html.escape(idea.note or '—')}"
    )
    await send_registered(
        message,
        services,
        with_notice(text, notice),
        kind=MessageKind.EDITOR,
        markup=InlineKeyboardMarkup(inline_keyboard=rows),
        related_id=idea.id,
        replace=replace,
    )


async def _on_list(context: CallbackContext) -> None:
    await render_ideas(
        context.message, context.services, page=int(context.payload.get("page", 0))
    )


async def _on_new(context: CallbackContext) -> None:
    await start_manual_card_creation(context.message, context.services, kind=CardKind.IDEA)


async def _on_expand(context: CallbackContext) -> None:
    """Hand the Idea to Safwa as an ordinary owner turn, and let it propose the tree."""
    async with context.sessions() as session:
        idea = await session.get(Card, int(context.payload["id"]))
        if idea is None or idea.kind != CardKind.IDEA.value:
            raise DomainError("Idea does not exist")
        request = f"Разверни идею «{idea.title}» в карточки." + (
            f"\nЗаметка: {idea.note}" if idea.note else ""
        )
    sent = await send_owner_turn(context.message, context.services, request)
    source = HistoryEntry(
        message_id=sent.message_id,
        sender_id=context.message.bot.id,
        role="user",
        text=request,
        created_at=sent.date.astimezone(UTC),
        kind=MessageKind.DIALOGUE_USER.value,
    )
    await run_dialogue_turn(context.message, context.services, request, source)


IDEA_CALLBACK_ACTIONS: dict[str, CallbackHandler] = {
    "idea_list": _on_list,
    "idea_new": _on_new,
    "idea_expand": _on_expand,
}
