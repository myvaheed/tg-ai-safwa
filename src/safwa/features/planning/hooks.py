"""The Sprint's own clockwork: the warnings that it is ending, the midnight that ends it, the
summary said once it has, the marks on the Actions its Success criterion rests on, and the
word when none is left."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, time, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.foundation.changes import Committed
from tg_agent_shell.foundation.clock import utcnow
from tg_agent_shell.hooks.contracts import (
    Advise,
    HookSpec,
    OnCommitted,
    OnTick,
    Run,
    RunContext,
    Tick,
)

from ...foundation.workspace import Workspace
from ..cards.model import CardStage
from .api import SPRINT_ENDED, SPRINT_JOINED, SPRINT_KEY_ACTIONS, SPRINT_LEFT, SPRINT_STARTED
from .key_actions import KeyActions
from .model import Sprint, SprintCommitment
from .use_cases import expire_due_sprint, sprint_summary

# What the key check and the end warning keep as their one pending item: each is about the
# running Sprint.
KEY_CHECK = "keys"
END_CHECK = "end"

SPRINT_ENDS_TOMORROW = (
    "Sprint {number} ends tomorrow, {end_date}. Check what is still open in Sprint and Today, "
    "and help the user finalize the status of each of those Actions."
)
SPRINT_ENDS_TODAY = (
    "Sprint {number} ends today, {end_date}. Tell the user to close it from the 🏃 Sprint "
    "screen; if they do not, Safwa closes it automatically at midnight and whatever is still "
    "open keeps its stage."
)

KEY_WARNING_REQUEST = (
    "Sprint {number}, Success criterion: {criterion}\n"
    "No Action still open in the Sprint is one the criterion rests on, and none such has "
    "been finished.\n"
    "Tell the user in one message that the Success criterion does not look reachable with "
    "what is planned. Propose nothing and ask nothing."
)


async def _running(session: AsyncSession) -> tuple[Sprint, ZoneInfo] | None:
    workspace = await session.get(Workspace, 1)
    if workspace is None or not workspace.active_sprint_id:
        return None
    sprint = await session.get(Sprint, workspace.active_sprint_id)
    return (sprint, ZoneInfo(workspace.timezone)) if sprint is not None else None


async def sprint_clock(session: AsyncSession) -> time:
    """The local time the running Sprint was started at, which its warnings keep; midnight
    while none runs, when the warning finds nothing to say."""
    running = await _running(session)
    if running is None:
        return time(0, 0)
    sprint, tz = running
    return sprint.actual_started_at.astimezone(tz).time()


async def end_check(event: Tick) -> tuple[str, ...]:
    return (END_CHECK,)


async def sprint_end_request(
    session: AsyncSession, items: Sequence[str], *, now: datetime | None = None
) -> str | None:
    """The warning on the day before the running Sprint's last day and on that day, or
    nothing. The day it started says nothing: its clock has not come round since."""
    running = await _running(session)
    if running is None:
        return None
    sprint, tz = running
    today = (now or utcnow()).astimezone(tz).date()
    end = sprint.planned_end_date
    if today == sprint.actual_started_at.astimezone(tz).date():
        return None
    template = {end - timedelta(days=1): SPRINT_ENDS_TOMORROW, end: SPRINT_ENDS_TODAY}.get(today)
    if template is None:
        return None
    return template.format(number=sprint.number, end_date=end.isoformat())


SPRINT_END_HOOK = HookSpec(
    name="planning.sprint_end",
    owner="planning",
    on=(OnTick(at=sprint_clock),),
    evaluate=end_check,
    effect=Advise(prepare=sprint_end_request),
    title="Sprint end warning",
    description=(
        "The day before a Sprint's last day and on that day, at the time it started, says "
        "that it is ending."
    ),
)


async def midnight(session: AsyncSession) -> time:
    """The local midnight: a Sprint runs through its last day and ends when that day does."""
    return time(0, 0)


async def passed_midnight(event: Tick) -> tuple[str, ...]:
    return (event.at,)


async def expire_due_sprint_now(session: AsyncSession) -> None:
    """Close the running Sprint if the midnight after its last day has passed."""
    await expire_due_sprint(session)


async def expire_sprint(marker: str, context: RunContext) -> None:
    async with context.sessions() as session:
        await expire_due_sprint_now(session)
        await session.commit()


SPRINT_EXPIRY_HOOK = HookSpec(
    name="planning.sprint_expiry",
    owner="planning",
    on=(OnTick(at=midnight),),
    evaluate=passed_midnight,
    effect=Run(expire_sprint),
    title="Sprint expiry",
    description="At the midnight after a Sprint's last day, ends it.",
)


async def ended_sprint(event: Committed) -> tuple[int, ...]:
    return (event.subject_id,)


async def sprint_summary_request(session: AsyncSession, items: Sequence[int]) -> str | None:
    """The summary of each Sprint that ended, written from its record as it is about to be said."""
    summaries = []
    for sprint_id in items:
        sprint = await session.get(Sprint, sprint_id)
        if sprint is not None:
            summaries.append(await sprint_summary(session, sprint))
    return "\n\n".join(summaries) if summaries else None


SPRINT_SUMMARY_HOOK = HookSpec(
    name="planning.sprint_summary",
    owner="planning",
    on=(OnCommitted(kind=SPRINT_ENDED),),
    evaluate=ended_sprint,
    effect=Advise(prepare=sprint_summary_request),
    title="Sprint summary",
    description="When a Sprint ends, tells you how it went and asks about what is still open.",
)


class KeyActionResources(Protocol):
    key_actions: KeyActions


async def sprint_change(event: Committed) -> tuple[Committed, ...]:
    return (event,)


async def mark_key_actions(event: Committed, context: RunContext[KeyActionResources]) -> None:
    """Mark the Sprint's open Actions when it starts, and the one that joined it."""
    if event.kind == SPRINT_STARTED:
        await context.resources.key_actions.mark(context.sessions, event.subject_id)
        return
    async with context.sessions() as session:
        workspace = await session.get(Workspace, 1)
        sprint_id = workspace.active_sprint_id if workspace is not None else None
    if sprint_id:
        await context.resources.key_actions.mark(
            context.sessions, sprint_id, card_ids=(event.subject_id,)
        )


KEY_ACTIONS_HOOK = HookSpec(
    name="planning.key_actions",
    owner="planning",
    on=(OnCommitted(kind=SPRINT_STARTED), OnCommitted(kind=SPRINT_JOINED)),
    evaluate=sprint_change,
    effect=Run(mark_key_actions),
    title="Key Actions",
    description="When a Sprint starts, and when an Action joins it, marks the Actions its Success criterion rests on.",
)


async def keys_changed(event: Committed) -> tuple[str, ...]:
    return (KEY_CHECK,)


async def key_warning_request(session: AsyncSession, items: Sequence[str]) -> str | None:
    """The word while the running Sprint has no key Action open and none finished, or
    nothing — also nothing while an open Action is not marked yet, since not marked is not
    the same as marked not key."""
    workspace = await session.get(Workspace, 1)
    if workspace is None or not workspace.active_sprint_id:
        return None
    sprint = await session.get(Sprint, workspace.active_sprint_id)
    if sprint is None:
        return None
    commitments = await session.scalars(
        select(SprintCommitment).where(SprintCommitment.sprint_id == sprint.id)
    )
    for commitment in commitments:
        finished = commitment.result == CardStage.DONE.value
        open_in_sprint = commitment.result is None and commitment.removed_at is None
        if commitment.key_action and (finished or open_in_sprint):
            return None
        if open_in_sprint and commitment.key_action is None:
            return None
    return KEY_WARNING_REQUEST.format(number=sprint.number, criterion=sprint.success_criteria)


KEY_WARNING_HOOK = HookSpec(
    name="planning.key_warning",
    owner="planning",
    on=(OnCommitted(kind=SPRINT_KEY_ACTIONS), OnCommitted(kind=SPRINT_LEFT)),
    evaluate=keys_changed,
    effect=Advise(prepare=key_warning_request),
    title="Unreachable criterion",
    description="When the Sprint is left with no Action its Success criterion rests on, says so.",
)
