"""The Diary nudge: at the Profile's Diary time, the Advisor is asked to write the day up."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.hooks.contracts import Advise, HookSpec, OnTick, Tick

from ..profile.api import diary_instructions, diary_time

DIARY_REQUEST = "End of day. Call the diary subagent for today, then propose what it reports."


async def evening(event: Tick) -> tuple[str, ...]:
    return (event.at,)


async def diary_request(session: AsyncSession, items: Sequence[str]) -> str | None:
    """The request, with the owner's standing Diary instruction read as it is about to be said."""
    extra = (await diary_instructions(session)).strip()
    return f"{DIARY_REQUEST} {extra}" if extra else DIARY_REQUEST


DIARY_HOOK = HookSpec(
    name="diary.nudge",
    owner="diary",
    on=(OnTick(at=diary_time),),
    evaluate=evening,
    effect=Advise(prepare=diary_request),
    title="Diary nudge",
    description="At the Diary time, asks to write your day up.",
)
