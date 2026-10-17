"""What Schedules ask the Advisor to raise on their own: a Schedule that ran its total."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.foundation.changes import Committed
from tg_agent_shell.hooks.contracts import Advise, HookSpec, OnCommitted

from ..cards.model import Card
from ..checks.model import Check
from .api import SCHEDULE_TOTAL_REACHED, item_label

TOTAL_REQUEST = (
    "Ran all the times its Schedule has in total, and no longer repeats:\n{items}\n"
    "Tell the user in one message, naming each, and ask whether to start it again. "
    "Change nothing until they answer.\n"
    "To start one again, propose its Schedule on that #id, in the same words or new ones."
)


async def finished_totals(event: Committed) -> tuple[list[Any], ...]:
    return ([(event.data or {})["type"], event.subject_id],)


async def total_request(session: AsyncSession, items: Sequence[list[Any]]) -> str | None:
    """The request about the items whose Schedule still ends at the total it ran."""
    lines: list[str] = []
    for item_type, item_id in items:
        entity = await session.get(Card if item_type == "card" else Check, item_id)
        definition = entity.schedule_record if entity is not None else None
        limit = definition.rule.get("limit") if definition is not None else None
        if limit is None:
            continue
        lines.append(f"- {item_label(entity)} #{entity.id} «{entity.title}», total {limit}")
    if not lines:
        return None
    return TOTAL_REQUEST.format(items="\n".join(lines))


SCHEDULE_TOTAL_HOOK = HookSpec(
    name="schedules.total",
    owner="schedules",
    on=(OnCommitted(kind=SCHEDULE_TOTAL_REACHED),),
    evaluate=finished_totals,
    effect=Advise(prepare=total_request),
    title="Schedule total",
    description="After a Schedule runs all its times, asks whether to start it again.",
)
