"""The menu screen, and the deep link that opens an item instead of it."""

from __future__ import annotations

from aiogram.types import Message

from tg_agent_shell.adapters.kinds import MessageKind
from tg_agent_shell.telegram import (
    Services,
    claimed_link,
    open_citation,
    send_registered,
    start_payload,
)

from ..planning.api import sprint_is_active
from .api import menu_markup


async def render_home(message: Message, services: Services) -> None:
    payload = start_payload(message.text)
    if payload is not None:
        link = claimed_link(services, payload)
        if link is not None:
            await link.open(message, services, payload)
        else:
            await open_citation(message, services, payload)
        return
    async with services.sessions() as session:
        sprint_active = await sprint_is_active(session)
    await send_registered(
        message,
        services,
        "<b>Safwa</b>\nYour personal agile advisor. Choose a dashboard or just write to me.",
        kind=MessageKind.DASHBOARD,
        markup=menu_markup(services.commands, sprint_active=sprint_active),
    )
