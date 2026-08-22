"""The Sprint that reached its planned end date closes itself."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ...bootstrap.module_manifest import BackgroundContext, BackgroundTask
from ...constants import SPRINT_EXPIRY_POLL_SECONDS
from ...domain import expire_due_sprint
from ...enums import MessageKind
from ...history import mark_message, register_message
from ...telegram import sync_bot_commands

logger = logging.getLogger(__name__)

Announcer = Callable[[int], Awaitable[None]]


async def run_sprint_expiry(
    sessions: async_sessionmaker[AsyncSession],
    *,
    announce: Announcer,
    poll_seconds: float = SPRINT_EXPIRY_POLL_SECONDS,
) -> None:
    """Close a Sprint that ran past its planned end date and say so once.

    A separate poll from the Reminder one: it takes no lease and asks the advisor nothing,
    because closing a Sprint at midnight is arithmetic, not a conversation.
    """
    while True:
        try:
            async with sessions() as session:
                sprint = await expire_due_sprint(session)
                number = sprint.number if sprint is not None else None
                await session.commit()
            if number is not None:
                await announce(number)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Sprint expiry poll failed")
        await asyncio.sleep(poll_seconds)


async def _announce_and_expire(context: BackgroundContext) -> None:
    async def announce(number: int) -> None:
        text = (
            f"⏹ Sprint {number} reached its planned end date and was closed automatically. "
            "Whatever was still open kept its stage."
        )
        marked_text, event_id = mark_message(text, MessageKind.RECEIPT)
        sent = await context.bot.send_message(context.settings.telegram_owner_id, marked_text)
        async with context.sessions() as session:
            await register_message(
                session,
                sent.chat.id,
                sent.message_id,
                "out",
                MessageKind.RECEIPT,
                event_id=event_id,
            )
            await session.commit()
        await sync_bot_commands(context.bot, sprint_active=False)

    await run_sprint_expiry(context.sessions, announce=announce)


SPRINT_EXPIRY = BackgroundTask("sprint-expiry", _announce_and_expire)
