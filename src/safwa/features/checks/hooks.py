"""What the Checks ask the Advisor to raise on their own: a series Missed several times
running."""

from __future__ import annotations

from collections.abc import Sequence
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.foundation.changes import Committed
from tg_agent_shell.hooks.contracts import Advise, HookSpec, OnCommitted

from ...foundation.workspace import require_workspace
from ..cards.api import Card
from .model import Check, CheckOutcome
from .use_cases import CHECK_MISSED, check_card_id

# How many Missed in a row, counted back to the series' last Passed, are worth raising:
# at this many, and at every multiple of it after.
MISSED_RUN = 3

MISSED_RUN_REQUEST = (
    "Missed several times in a row:\n{checks}\n"
    "Raise it with the user in one message, naming each: ask what gets in the way, and "
    "whether the Check, its Card or the approach should change. Decide nothing for them; "
    "do not create or change anything without their answer."
)


async def missed_checks(event: Committed) -> tuple[int, ...]:
    return (event.subject_id,)


async def missed_run(session: AsyncSession, check: Check) -> list[Check]:
    """The series' newest answers back to its last Passed, newest first; Pending is skipped."""
    series_id = check.series_id or check.id
    answered = await session.scalars(
        select(Check)
        .where(
            or_(Check.series_id == series_id, Check.id == series_id),
            Check.outcome.is_not(None),
        )
        .order_by(Check.id.desc())
    )
    run: list[Check] = []
    for instance in answered:
        if instance.outcome != CheckOutcome.MISSED.value:
            break
        run.append(instance)
    return run


async def missed_run_request(session: AsyncSession, items: Sequence[int]) -> str | None:
    """The request about the series whose run of Missed is a multiple of MISSED_RUN now.

    The run is read as it stands: an answer changed since ends it where it is, and a
    series whose run has moved past a multiple asks nothing until the next one.
    """
    tz = ZoneInfo((await require_workspace(session)).timezone)
    lines: list[str] = []
    series_seen: set[int] = set()
    for item in sorted(int(item) for item in items):
        check = await session.get(Check, item)
        if check is None or (check.series_id or check.id) in series_seen:
            continue
        series_seen.add(check.series_id or check.id)
        run = await missed_run(session, check)
        if not run or len(run) % MISSED_RUN:
            continue
        card_id = await check_card_id(session, check.id)
        card = await session.get(Card, card_id) if card_id is not None else None
        where = f"on Card «{card.title}»" if card is not None else "on no Card"
        since = run[-1].resolved_at.astimezone(tz).date().isoformat()
        lines.append(
            f"- #{check.id} «{check.title}» {where}: Missed {len(run)} times in a row, "
            f"since {since}"
        )
    if not lines:
        return None
    return MISSED_RUN_REQUEST.format(checks="\n".join(lines))


MISSED_RUN_HOOK = HookSpec(
    name="checks.missed_run",
    owner="checks",
    on=(OnCommitted(kind=CHECK_MISSED),),
    evaluate=missed_checks,
    effect=Advise(prepare=missed_run_request),
    title="Repeated Missed",
    description="After a Check is Missed several times in a row, asks what gets in the way.",
)
