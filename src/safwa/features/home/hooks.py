"""The Home dashboard's hook: clear older messages after the owner has been quiet."""

from __future__ import annotations

from datetime import timedelta

from tg_agent_shell.foundation.clock import utcnow
from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.hooks.contracts import ChatState, HookSpec, OnTick, Run, RunContext, Tick

from ..profile.api import home_after_minutes, secret_word_verifier

# How often the chat is looked at, so the quiet time is kept to within this much.
HOME_LOOK_EVERY = timedelta(seconds=30)


async def looked_at(event: Tick) -> tuple[ChatState, ...]:
    return (event.chat,) if event.chat is not None else ()


async def clear_when_quiet(chat: ChatState, context: RunContext) -> None:
    """Empty the chat once the quiet time has passed, locking protected access."""
    now = utcnow()
    async with context.sessions() as session:
        quiet = timedelta(minutes=await home_after_minutes(session))
        secured = await secret_word_verifier(session) is not None
    if not chat.free or now - chat.owner_acted_at < quiet:
        return
    if chat.newest_kind is None and not secured:
        return
    await context.publish("", MessageKind.HOME.value)


HOME_HOOK = HookSpec(
    name="home.dashboard",
    owner="home",
    on=(OnTick(every=HOME_LOOK_EVERY),),
    evaluate=looked_at,
    effect=Run(clear_when_quiet),
    title="Chat cleanup",
    description="Empties the chat after you leave it quiet and locks access when Secret word is set.",
)
