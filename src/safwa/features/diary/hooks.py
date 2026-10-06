"""The Diary's hooks: the evening nudge, and the check that a day is read before it is rewritten."""

from __future__ import annotations

import json
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.hooks.contracts import (
    Advise,
    BeforeProposals,
    HookSpec,
    OnBeforeProposals,
    OnTick,
    ReturnProposals,
    Tick,
)

from ..profile.api import diary_instructions, evening_time

DIARY_REQUEST = 'End of day. Call route("diary") for today.'

DAY_NOT_READ = (
    'Read {day} first with read_day(date="{day}"). Then send diary again, with the words '
    "already saved for that day folded into body."
)


async def evening(event: Tick) -> tuple[str, ...]:
    return (event.at,)


async def diary_request(session: AsyncSession, items: Sequence[str]) -> str | None:
    """The request, with the owner's standing Diary instruction read as it is about to be said."""
    extra = (await diary_instructions(session)).strip()
    return f"{DIARY_REQUEST} {extra}" if extra else DIARY_REQUEST


DIARY_HOOK = HookSpec(
    name="diary.nudge",
    owner="diary",
    on=(OnTick(at=evening_time),),
    evaluate=evening,
    effect=Advise(prepare=diary_request),
    title="Diary nudge",
    description="At the Evening time, asks to write your day up.",
)


async def unread_days(event: BeforeProposals) -> tuple[str, ...]:
    """Each day the response writes new words for that this session never read (DI-READ-023).

    A day is read when `read_day` answered for it; a call that failed read nothing. Photos
    alone leave the words as they are, so only a call carrying `body` is checked.
    """
    read: set[str] = set()
    for item in event.reads:
        if item.tool != "read_day":
            continue
        try:
            result = json.loads(item.result)
        except json.JSONDecodeError:
            continue
        if isinstance(result, dict) and "saved" in result:
            read.add(str(result.get("date")))
    written = dict.fromkeys(
        str(call.values.get("date"))
        for call in event.calls
        if call.tool == "diary" and call.values.get("body")
    )
    return tuple(DAY_NOT_READ.format(day=day) for day in written if day not in read)


DIARY_READ_HOOK = HookSpec(
    name="diary.read_first",
    owner="diary",
    on=(OnBeforeProposals(),),
    evaluate=unread_days,
    effect=ReturnProposals(code="day_not_read"),
    title="Read the day first",
    description="Sends back new words for a day the Diary has not read.",
)
