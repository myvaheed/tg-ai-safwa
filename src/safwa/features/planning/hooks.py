"""The Sprint's own clockwork: the midnight that ends it, the summary said once it has,
the marks on the Actions its Success criterion rests on, and the word when none is left."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import time
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.foundation.changes import Committed
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

# What the key check keeps as its one pending item: it is about the running Sprint.
KEY_CHECK = "keys"

KEY_WARNING_REQUEST = (
    "Sprint {number}, Success criterion: {criterion}\n"
    "No Action still open in the Sprint is one the criterion rests on, and none such has "
    "been finished.\n"
    "Tell the user in one message that the Success criterion does not look reachable with "
    "what is planned. Propose nothing and ask nothing."
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
    async with context.sessions() as session:
        if event.kind == SPRINT_STARTED:
            await context.resources.key_actions.mark(session, event.subject_id)
        else:
            workspace = await session.get(Workspace, 1)
            if workspace is None or not workspace.active_sprint_id:
                return
            await context.resources.key_actions.mark(
                session, workspace.active_sprint_id, card_ids=(event.subject_id,)
            )
        await session.commit()


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
    """The word while the running Sprint has no key Action open and none finished, or nothing."""
    workspace = await session.get(Workspace, 1)
    if workspace is None or not workspace.active_sprint_id:
        return None
    sprint = await session.get(Sprint, workspace.active_sprint_id)
    if sprint is None:
        return None
    keys = await session.scalars(
        select(SprintCommitment).where(
            SprintCommitment.sprint_id == sprint.id, SprintCommitment.key_action.is_(True)
        )
    )
    for commitment in keys:
        finished = commitment.result == CardStage.DONE.value
        open_in_sprint = commitment.result is None and commitment.removed_at is None
        if finished or open_in_sprint:
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
