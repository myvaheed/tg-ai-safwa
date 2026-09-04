"""The Sprint that reached its planned end date closes itself."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tg_agent_shell.telegram import sync_bot_commands
from tg_agent_shell.telegram.manifest import BackgroundContext, BackgroundTask

from .api import available_screens
from .use_cases import expire_due_sprint

logger = logging.getLogger(__name__)

# A Sprint expires at a local midnight, so this poll only has to be finer than a night.
SPRINT_EXPIRY_POLL_SECONDS = 300.0

Announcer = Callable[[int], Awaitable[None]]


async def run_sprint_expiry(
    sessions: async_sessionmaker[AsyncSession],
    *,
    announce: Announcer,
    poll_seconds: float = SPRINT_EXPIRY_POLL_SECONDS,
) -> None:
    """Close a Sprint that ran past its planned end date.

    A separate poll from the Reminder one: closing a Sprint at midnight is arithmetic, not
    a conversation. What the owner is told about it is a conversation, and that is why the
    ending leaves a due Reminder behind rather than writing a message here.
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


async def _expire_and_close_today(context: BackgroundContext) -> None:
    async def announce(number: int) -> None:
        # Today belongs to a running Sprint, so the command list changes the moment one ends.
        await sync_bot_commands(
            context.bot, available_screens(context.services.commands, sprint_active=False)
        )

    await run_sprint_expiry(context.sessions, announce=announce)


SPRINT_EXPIRY = BackgroundTask("sprint-expiry", _expire_and_close_today)
