"""The Home dashboard's hook: clear older messages after the owner has been quiet."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from tg_agent_shell.foundation.clock import utcnow
from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.hooks.contracts import ChatState, HookSpec, OnTick, Run, RunContext, Tick

from ...foundation.workspace import require_workspace
from ..profile.api import home_after_minutes, secret_word_verifier
from .dashboard import dashboard_text

# How often the chat is looked at, so the quiet time is kept to within this much.
HOME_LOOK_EVERY = timedelta(seconds=30)


async def looked_at(event: Tick) -> tuple[ChatState, ...]:
    return (event.chat,) if event.chat is not None else ()


async def clear_when_quiet(chat: ChatState, context: RunContext) -> None:
    """Draw the dashboard and clear older messages once the quiet time has passed."""
    now = utcnow()
    async with context.sessions() as session:
        quiet = timedelta(minutes=await home_after_minutes(session))
        tz = ZoneInfo((await require_workspace(session)).timezone)
    if not chat.free or now - chat.owner_acted_at < quiet or not _owed(chat, now, tz):
        return
    async with context.sessions() as session:
        secured = await secret_word_verifier(session) is not None
    if secured:
        await context.publish("", MessageKind.HOME.value)
        return
    words = await context.resources.motivator.write(context.sessions)
    if not context.still_current():
        return
    async with context.sessions() as session:
        text = await dashboard_text(session, words, now=now)
    await context.publish(text, MessageKind.HOME.value)


def _owed(chat: ChatState, now: datetime, tz: ZoneInfo) -> bool:
    """Whether the chat holds anything but a dashboard drawn today and left alone since."""
    if chat.newest_kind != MessageKind.HOME.value or chat.newest_at is None:
        return True
    if chat.newest_at.astimezone(tz).date() < now.astimezone(tz).date():
        return True
    return chat.owner_acted_at > chat.newest_at


HOME_HOOK = HookSpec(
    name="home.dashboard",
    owner="home",
    on=(OnTick(every=HOME_LOOK_EVERY),),
    evaluate=looked_at,
    effect=Run(clear_when_quiet),
    title="Home dashboard",
    description="Clears older messages and draws Home after you leave the chat quiet.",
)
