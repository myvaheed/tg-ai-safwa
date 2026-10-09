"""Home: the dashboard, its menu, item links, and a command that empties the chat.

Home draws at once with fresh words under Values, otherwise requesting them in the background."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from aiogram.types import InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from telegram_llm import markdown_to_telegram_html
from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.telegram import (
    Services,
    claimed_link,
    home_markup,
    open_citation,
    render_citations,
    send_registered,
    start_payload,
)

from .api import menu_markup
from .dashboard import dashboard_text


async def _dashboard(session: AsyncSession, services: Services, words: Mapping[int, str]) -> str:
    """The dashboard as the chat shows it: rendered the way an answer is."""
    text = await dashboard_text(session, words)
    return await render_citations(session, services, markdown_to_telegram_html(text))


def _put_words_in(
    home: Message, services: Services, body: str, kind: MessageKind, markup: InlineKeyboardMarkup
) -> None:
    """Ask for the words in the background, and redraw `home` with them when they come.

    Only while it still shows `body` and the owner has done nothing since: the words are
    kept for the next Home either way.
    """
    acted = services.owner_acted_at

    async def put_in() -> None:
        words = await services.features.motivator.write(services.sessions)
        # Before the lease: a Home the owner left behind must not hold it from a newer one.
        # From the lease on, the owner acting cancels the redraw.
        if not words or services.owner_acted_at != acted:
            return

        async def redraw(still_current: Callable[[], bool]) -> None:
            note = await services.chat.notes.note(home.chat.id, home.message_id)
            if note is None or note.text != body:
                return
            async with services.sessions() as session:
                text = await _dashboard(session, services, words)
            if still_current():
                await services.chat.edit(
                    home, home.message_id, text, kind=kind.value, markup=markup
                )

        await services.turn.run_background(redraw)

    services.chat.spawn(put_in(), "Home words")


async def render_home(message: Message, services: Services) -> None:
    """/start draws a compact Home; navigation unfolds its menu."""
    payload = start_payload(message.text)
    if payload is not None:
        link = claimed_link(services, payload)
        if link is not None:
            await link.open(message, services, payload)
        else:
            await open_citation(message, services, payload)
        return
    access = getattr(services, "access", None)
    if access is not None:
        await access.restore(message)
    words = services.features.motivator.fresh()
    markup = home_markup() if (message.text or "").startswith("/start") else menu_markup(services.commands)
    async with services.sessions() as session:
        body = await _dashboard(session, services, words or {})
    home = await send_registered(message, services, body, kind=MessageKind.DASHBOARD, markup=markup)
    if words is None:
        _put_words_in(home, services, body, MessageKind.DASHBOARD, markup)


async def command_clear(message: Message, services: Services) -> None:
    """Empty the chat without locking the open session or drawing Home."""

    async def clear(still_current: Callable[[], bool]) -> None:
        if still_current():
            await services.access.clear(message)

    await services.turn.run_background(clear)
