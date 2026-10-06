"""Home: the dashboard with the menu under it, the deep link that opens an item instead, and
the command that clears the chat down to Home at once.

Both draw at once, with the words under the Values when fresh ones are kept, and otherwise
ask for them and put them in when they come."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from aiogram.types import InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from telegram_llm import markdown_to_telegram_html
from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.telegram import (
    Services,
    claimed_link,
    clear_draw_home,
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
    """Home with its menu unfolded: on /start, on `↩️ Menu`, and on Home's own `☰ Menu`."""
    payload = start_payload(message.text)
    if payload is not None:
        link = claimed_link(services, payload)
        if link is not None:
            await link.open(message, services, payload)
        else:
            await open_citation(message, services, payload)
        return
    note = await services.chat.notes.note(message.chat.id, message.message_id)
    if note is not None and note.kind == MessageKind.HOME.value:
        # The dashboard a clear drew keeps its words: only its menu unfolds.
        await services.chat.set_buttons(message, menu_markup(services.commands))
        return
    words = services.features.motivator.fresh()
    markup = menu_markup(services.commands)
    async with services.sessions() as session:
        body = await _dashboard(session, services, words or {})
    home = await send_registered(message, services, body, kind=MessageKind.DASHBOARD, markup=markup)
    if words is None:
        _put_words_in(home, services, body, MessageKind.DASHBOARD, markup)


async def command_clear(message: Message, services: Services) -> None:
    """Clear the chat down to Home now, as a quiet chat is cleared. The owner acting stops it."""
    words = services.features.motivator.fresh()

    async def clear(still_current: Callable[[], bool]) -> tuple[Message, str] | None:
        async with services.sessions() as session:
            body = await _dashboard(session, services, words or {})
        if not still_current():
            return None
        return await clear_draw_home(message, services, body), body

    drawn = await services.turn.run_background(clear)
    if drawn is not None and words is None:
        home, body = drawn
        _put_words_in(home, services, body, MessageKind.HOME, home_markup())
