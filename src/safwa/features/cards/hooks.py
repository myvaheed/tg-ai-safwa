"""What the Cards ask the Advisor to raise on their own: a blocker just set, and each
morning the Goals and Subgoals that still have no Action under them."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.foundation.changes import Committed
from tg_agent_shell.foundation.clock import utcnow
from tg_agent_shell.hooks.contracts import (
    Advise,
    HookSpec,
    HookSwitch,
    OnCommitted,
    OnTick,
    Tick,
)

from .hierarchy import branch_actions
from .model import TERMINAL_STAGES, Card, CardKind
from .use_cases import CARD_BLOCKED

# When the morning check runs, by the workspace's clock, and how old a Goal or a Subgoal
# is before having no Action under it is worth a question.
EMPTY_PARENT_CHECK_TIME = "09:00"
EMPTY_PARENT_GRACE_DAYS = 1

EMPTY_PARENTS_REQUEST = (
    "Without any Action under them:\n{cards}\n"
    "Ask the user in one message, naming each: plan its Actions now, or create one Action "
    "«Plan the actions for <title>» to come back to it later. Wait for their choice. "
    "Do not create anything without their answer."
)

BLOCKER_REQUEST = (
    "Blocked since we last spoke:\n{cards}\n"
    "Ask the user whether to set a Reminder to come back to each; if they want one, agree "
    "when and propose it. Do not create anything without their answer."
)


async def blocked_cards(event: Committed) -> tuple[int, ...]:
    return (event.subject_id,)


async def blocker_request(session: AsyncSession, items: Sequence[int]) -> str | None:
    """The request about the Cards that are still blocked and still open, or nothing."""
    cards = list(
        await session.scalars(
            select(Card)
            .where(
                Card.id.in_([int(item) for item in items]),
                Card.blocked.is_(True),
                Card.archived_at.is_(None),
                Card.effective_stage.not_in([stage.value for stage in TERMINAL_STAGES]),
            )
            .order_by(Card.id)
        )
    )
    if not cards:
        return None
    lines = "\n".join(f"- #{card.id} «{card.title}»: {card.blocked_description}" for card in cards)
    return BLOCKER_REQUEST.format(cards=lines)


BLOCKER_HOOK = HookSpec(
    name="cards.blocker",
    owner="cards",
    on=(OnCommitted(kind=CARD_BLOCKED),),
    evaluate=blocked_cards,
    effect=Advise(prepare=blocker_request),
    switch=HookSwitch(
        title="Blocker follow-up",
        description="After an Action is blocked, asks whether to set a Reminder to come back to it.",
    ),
)


async def check_due(event: Tick) -> tuple[str, ...]:
    return (event.at,)


async def empty_parents_request(
    session: AsyncSession, items: Sequence[str], *, now: datetime | None = None
) -> str | None:
    """The request about the Goals and Subgoals old enough and still without an Action.

    An Action anywhere in the branch counts, a finished or an archived one included: a Goal
    whose Action sits under its Subgoal has one.
    """
    cutoff = (now or utcnow()) - timedelta(days=EMPTY_PARENT_GRACE_DAYS)
    parents = await session.scalars(
        select(Card)
        .where(
            Card.kind.in_([CardKind.GOAL.value, CardKind.SUBGOAL.value]),
            Card.archived_at.is_(None),
            Card.created_at <= cutoff,
        )
        .order_by(Card.id)
    )
    empty = [card for card in parents if not await branch_actions(session, card.id)]
    if not empty:
        return None
    lines = "\n".join(
        f"- #{card.id} «{card.title}» ({card.kind.capitalize()})" for card in empty
    )
    return EMPTY_PARENTS_REQUEST.format(cards=lines)


EMPTY_PARENTS_HOOK = HookSpec(
    name="cards.empty_parents",
    owner="cards",
    on=(OnTick(at=EMPTY_PARENT_CHECK_TIME),),
    evaluate=check_due,
    effect=Advise(prepare=empty_parents_request),
    switch=HookSwitch(
        title="Goals without Actions",
        description="Each morning, asks about the Goals and Subgoals that have no Action under them.",
    ),
)
