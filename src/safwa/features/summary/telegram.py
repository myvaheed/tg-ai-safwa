"""The explicit command that cuts the conversation window.

Summary draws no buttons: the command answers with a receipt and gives the turn straight
back, because the work itself runs in the background under the turn's lease.
"""

from __future__ import annotations

from aiogram.types import Message

from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.telegram import Services, send_registered, send_summary

from .api import dialogue_summary


async def command_summarize(message: Message, services: Services) -> None:
    """Cut the context deliberately: post a Summary now instead of waiting for the budget."""
    written = await services.turn.run_background(
        lambda still_current: dialogue_summary(services).close_window(
            message.chat.id,
            lambda text: send_summary(message, services, text),
            force=True,
            still_current=still_current,
        )
    )
    if written is None:
        await send_registered(
            message,
            services,
            "Wait for the current advisor response, then retry /summarize.",
            kind=MessageKind.ERROR,
        )
        return
    if not written:
        await send_registered(
            message,
            services,
            "There is no new dialogue to summarize.",
            kind=MessageKind.RECEIPT,
        )
