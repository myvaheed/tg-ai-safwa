"""What Schedules do on their own: ask the Advisor to raise a Schedule that ran its total or
reached its last day, and move each open instance on to the period it is in."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.foundation.changes import Committed
from tg_agent_shell.hooks.contracts import (
    Advise,
    HookSpec,
    OnCommitted,
    OnStarted,
    OnTick,
    Run,
    RunContext,
    Started,
    Tick,
)

from ...constants import SCHEDULER_POLL_SECONDS
from ..cards.model import Card
from ..checks.model import Check
from .api import SCHEDULE_ENDED, item_label, move_on_open_instances

END_REQUEST = (
    "Ran all the times its Schedule has, or reached its last day, and no longer repeats:\n{items}\n"
    "Tell the user in one message, naming each, and ask whether to start it again. "
    "Change nothing until they answer.\n"
    "To start one again, propose its Schedule on that #id, in the same words or new ones."
)


async def ended_items(event: Committed) -> tuple[list[Any], ...]:
    data = event.data or {}
    return ([data["type"], event.subject_id, data["reason"]],)


async def end_request(session: AsyncSession, items: Sequence[list[Any]]) -> str | None:
    """The request about the items whose Schedule still ends where it ended."""
    lines: list[str] = []
    for item_type, item_id, reason in items:
        entity = await session.get(Card if item_type == "card" else Check, item_id)
        definition = entity.schedule_record if entity is not None else None
        rule = (definition.rule if definition is not None else None) or {}
        if reason == "total" and rule.get("limit"):
            end = f"total {rule['limit']}"
        elif reason == "last_day" and rule.get("until"):
            end = f"last day {date.fromisoformat(rule['until']):%d.%m.%Y}"
        else:
            continue
        lines.append(f"- {item_label(entity)} #{entity.id} «{entity.title}», {end}")
    if not lines:
        return None
    return END_REQUEST.format(items="\n".join(lines))


SCHEDULE_END_HOOK = HookSpec(
    name="schedules.end",
    owner="schedules",
    on=(OnCommitted(kind=SCHEDULE_ENDED),),
    evaluate=ended_items,
    effect=Advise(prepare=end_request),
    title="Schedule end",
    description="After a Schedule runs all its times or reaches its last day, asks whether to start it again.",
)


async def every_look(event: Tick | Started) -> tuple[Tick | Started, ...]:
    return (event,)


async def move_on(event: Tick | Started, context: RunContext) -> None:
    async with context.sessions() as session:
        await move_on_open_instances(session)
        await session.commit()


SCHEDULE_PERIOD_HOOK = HookSpec(
    name="schedules.period",
    owner="schedules",
    on=(OnTick(every=timedelta(seconds=SCHEDULER_POLL_SECONDS)), OnStarted()),
    evaluate=every_look,
    effect=Run(move_on),
    title="Schedule periods",
    description="As a new period of a repeating Schedule starts, moves its open Action or Check on to it.",
)
