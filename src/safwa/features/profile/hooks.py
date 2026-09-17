"""The daily summary: at the Profile's summary time, the Advisor tells the owner what they got done."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.hooks.contracts import Advise, HookSpec, OnTick, Tick

from .api import summary_time

DAILY_SUMMARY_REQUEST = (
    "Daily summary. Read with query_data what the user finished today: `ai_cards` with "
    "kind = 'action' AND stage = 'done' AND updated_at >= today, `ai_checks` with "
    "resolved_at >= today, and `ai_diary` for today. Tell the user what they got done, "
    "in a few warm lines about that work only: invent nothing, list nothing overdue. "
    "If today has no Diary entry, offer to write the day down — unless the Diary "
    "request came with this one."
)


async def evening(event: Tick) -> tuple[str, ...]:
    return (event.at,)


async def daily_summary_request(session: AsyncSession, items: Sequence[str]) -> str | None:
    return DAILY_SUMMARY_REQUEST


DAILY_SUMMARY_HOOK = HookSpec(
    name="profile.daily_summary",
    owner="profile",
    on=(OnTick(at=summary_time),),
    evaluate=evening,
    effect=Advise(prepare=daily_summary_request),
    title="Daily summary",
    description="At the summary time, tells you what you got done that day.",
)
