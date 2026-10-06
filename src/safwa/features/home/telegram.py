"""Home: the dashboard with the menu under it, the deep link that opens an item instead, and
the command that clears the chat down to Home at once."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from telegram_llm import markdown_to_telegram_html
from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.telegram import (
    Services,
    claimed_link,
    clear_draw_home,
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
    # The words under each Value are written only for a clear, so Home opens at once.
    async with services.sessions() as session:
        body = await _dashboard(session, services, {})
    await send_registered(
        message, services, body, kind=MessageKind.DASHBOARD, markup=menu_markup(services.commands)
    )


async def command_clear(message: Message, services: Services) -> None:
    """Clear the chat down to Home now, as a quiet chat is cleared. The owner acting stops it."""

    async def clear(still_current: Callable[[], bool]) -> None:
        words = await services.features.motivator.write(services.sessions)
        async with services.sessions() as session:
            body = await _dashboard(session, services, words)
        if still_current():
            await clear_draw_home(message, services, body)

    await services.turn.run_background(clear)
