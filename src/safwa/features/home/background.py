"""The look that clears a chat the owner left quiet down to the Home dashboard.

It runs under the background lease, so the owner always wins: any message or press of
theirs cancels it, and their next quiet period starts over.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram.types import Message

from tg_agent_shell.cues.runtime import chat_is_free
from tg_agent_shell.foundation.clock import utcnow
from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.foundation.poll import run_poll
from tg_agent_shell.telegram import Services, draw_home, owner_anchor
from tg_agent_shell.telegram.manifest import BackgroundContext, BackgroundTask

from ...foundation.workspace import require_workspace
from ..profile.api import home_after_minutes
from .api import motivator
from .dashboard import dashboard_text


async def clear_when_quiet(services: Services, anchor: Message) -> None:
    """Draw the dashboard over everything else once the owner has been quiet long enough.

    `anchor` stands for the owner in the chat the dashboard is drawn in.
    """
    now = utcnow()
    async with services.sessions() as session:
        quiet = timedelta(minutes=await home_after_minutes(session))
        tz = ZoneInfo((await require_workspace(session)).timezone)
    chat_id = anchor.chat.id
    if now - services.owner_acted_at < quiet or not await _owed(services, chat_id, now, tz):
        return
    if not await chat_is_free(services):
        return

    async def draw(still_current: Callable[[], bool]) -> None:
        words = await motivator(services).write(services.sessions)
        if not still_current():
            return
        async with services.sessions() as session:
            text = await dashboard_text(session, services, words, now=now)
        if still_current():
            await draw_home(anchor, services, text)

    await services.turn.run_background(draw)


async def _owed(services: Services, chat_id: int, now: datetime, tz: ZoneInfo) -> bool:
    """Whether the chat holds anything but a dashboard drawn today."""
    homes = await services.chat.notes.outgoing(chat_id, kinds={MessageKind.HOME.value})
    if not homes or homes[0].at is None:
        return True
    home = homes[0]
    if home.at.astimezone(tz).date() < now.astimezone(tz).date():
        return True
    if services.owner_acted_at > home.at:
        return True
    newest = await services.chat.notes.messages(chat_id, limit=1)
    return bool(newest) and newest[0].message_id > home.message_id


async def _poll(context: BackgroundContext) -> None:
    if not context.scheduler_enabled:
        return

    async def tick() -> None:
        await clear_when_quiet(context.services, owner_anchor(context.bot, context.owner_id))

    await run_poll(tick, poll_seconds=context.poll_seconds, name="Home dashboard")


HOME_DASHBOARD = BackgroundTask("home-dashboard", _poll)
