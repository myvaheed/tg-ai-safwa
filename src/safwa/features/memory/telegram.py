"""The one command that shows what Safwa remembers.

Memory draws no buttons and takes no words: nothing here writes it, and the owner reads
the same text the Advisor is given — whole, in as many messages as Telegram needs.
"""

from __future__ import annotations

import html

from aiogram.types import Message

from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.telegram import Services, send_prose

from .use_cases import remembered


async def command_memory(message: Message, services: Services) -> None:
    async with services.sessions() as session:
        text = await remembered(session)
    await send_prose(
        message,
        services,
        "<b>Persistent memory</b>\n" + html.escape(text),
        kind=MessageKind.DASHBOARD,
    )
