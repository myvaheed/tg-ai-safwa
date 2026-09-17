"""What the Cards ask the Advisor to raise on their own: a blocker just set, a day loaded
past what it is meant to hold, and — each morning, and when a Sprint starts — the Goals
and Subgoals that still have no Action under them, and the Hard Times the plan does not
hold."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.foundation.changes import Committed
from tg_agent_shell.foundation.clock import utcnow
from tg_agent_shell.hooks.contracts import (
    Advise,
    HookSpec,
    OnCommitted,
    OnTick,
    Tick,
)

from ..planning.api import SPRINT_STARTED, active_sprint_end_date
from ..profile.api import morning_time
from .hard_time import workspace_zone
from .hierarchy import branch_actions
from .model import TERMINAL_STAGES, Card, CardKind, CardStage, effort_label
from .use_cases import CARD_BLOCKED, CARD_TODAY

# How old a Goal or a Subgoal is before having no Action under it is worth a question.
EMPTY_PARENT_GRACE_DAYS = 1
# What a day is meant to hold, in effort points: the open Actions in Today and the ones
# finished that day, together.
TODAY_CAPACITY_EP = 15
# How many days ahead a Hard Time is near enough to belong in Today: today and tomorrow.
HARD_TIME_NOTICE_DAYS = 1
# What the plan check keeps as its one pending item: it is about the whole plan, not a Card.
HARD_TIME_CHECK = "plan"

HARD_TIME_REQUEST = (
    "Hard Times the plan does not hold:\n{cards}\n"
    "Ask the user in one message whether to take each into the Sprint, and the ones due "
    "today or tomorrow into Today. Do not move anything without their answer."
)

TODAY_OVERLOAD_REQUEST = (
    "Today holds {total} EP, over the {capacity} EP a day is meant to hold; {done} EP of "
    "it is finished already. Still open in Today:\n{cards}\n"
    "Ask the user in one message which of these to move back to Sprint, and say which you "
    "would move first. Do not move anything without their answer."
)

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
    title="Blocker follow-up",
    description="After an Action is blocked, asks whether to set a Reminder to come back to it.",
)


async def entered_today(event: Committed) -> tuple[int, ...]:
    return (event.subject_id,)


async def today_overload_request(
    session: AsyncSession, items: Sequence[int], *, now: datetime | None = None
) -> str | None:
    """The request while the day holds more than it is meant to, or nothing.

    The day is the workspace's local day, and what it holds is summed now, not when an
    Action entered Today: the ones still open there, and the ones finished today.
    """
    tz = await workspace_zone(session)
    day_start = datetime.combine(
        (now or utcnow()).astimezone(tz).date(), time(0, 0), tzinfo=tz
    ).astimezone(UTC)
    open_today = list(
        await session.scalars(
            select(Card)
            .where(
                Card.kind == CardKind.ACTION.value,
                Card.effective_stage == CardStage.TODAY.value,
                Card.archived_at.is_(None),
            )
            .order_by(Card.id)
        )
    )
    finished = await session.scalar(
        select(func.coalesce(func.sum(Card.effort_points), 0)).where(
            Card.kind == CardKind.ACTION.value,
            Card.completed_at >= day_start,
            Card.completed_at < day_start + timedelta(days=1),
        )
    )
    total = sum(card.effort_points or 0 for card in open_today) + (finished or 0)
    if total <= TODAY_CAPACITY_EP:
        return None
    lines = "\n".join(
        f"- #{card.id} «{card.title}» ({effort_label(card.effort_points)} EP)"
        for card in open_today
    )
    return TODAY_OVERLOAD_REQUEST.format(
        total=effort_label(total),
        capacity=TODAY_CAPACITY_EP,
        done=effort_label(finished or 0),
        cards=lines,
    )


TODAY_OVERLOAD_HOOK = HookSpec(
    name="cards.today_overload",
    owner="cards",
    on=(OnCommitted(kind=CARD_TODAY),),
    evaluate=entered_today,
    effect=Advise(prepare=today_overload_request),
    title="Today overload",
    description="After an Action enters Today, asks what to move back when the day holds more than it is meant to.",
)


async def plan_check_due(event: Committed | Tick) -> tuple[str, ...]:
    return (HARD_TIME_CHECK,)


async def hard_time_request(
    session: AsyncSession, items: Sequence[str], *, now: datetime | None = None
) -> str | None:
    """The request about the open Actions whose Hard Time comes before the plan holds them.

    Two windows, read on the workspace's local days as the question is about to be said:
    a Hard Time by the Sprint's last day while the Action sits in Backlog, and one today
    or tomorrow while it is not in Today. In Planning there is no plan to hold them.
    """
    end = await active_sprint_end_date(session)
    if end is None:
        return None
    tz = await workspace_zone(session)
    today = (now or utcnow()).astimezone(tz).date()
    near = today + timedelta(days=HARD_TIME_NOTICE_DAYS)
    cards = await session.scalars(
        select(Card)
        .where(
            Card.kind == CardKind.ACTION.value,
            Card.archived_at.is_(None),
            Card.effective_stage.not_in([stage.value for stage in TERMINAL_STAGES]),
            Card.hard_time_at.is_not(None),
        )
        .order_by(Card.hard_time_at, Card.id)
    )
    lines: list[str] = []
    for card in cards:
        assert card.hard_time_at is not None
        when = card.hard_time_at.astimezone(tz)
        stage = CardStage(card.effective_stage)
        if when.date() < today:
            continue
        outside_sprint = when.date() <= end and stage is CardStage.BACKLOG
        outside_today = when.date() <= near and stage is not CardStage.TODAY
        if outside_sprint or outside_today:
            lines.append(
                f"- #{card.id} «{card.title}»: {when:%Y-%m-%d %H:%M}, in {stage.value.capitalize()}"
            )
    if not lines:
        return None
    return HARD_TIME_REQUEST.format(cards="\n".join(lines))


HARD_TIME_HOOK = HookSpec(
    name="cards.hard_time_plan",
    owner="cards",
    on=(OnCommitted(kind=SPRINT_STARTED), OnTick(at=morning_time)),
    evaluate=plan_check_due,
    effect=Advise(prepare=hard_time_request),
    title="Hard Time outside the plan",
    description="When a Sprint starts and each morning, asks about the Actions whose Hard Time comes before the plan holds them.",
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
    on=(OnTick(at=morning_time),),
    evaluate=check_due,
    effect=Advise(prepare=empty_parents_request),
    title="Goals without Actions",
    description="Each morning, asks about the Goals and Subgoals that have no Action under them.",
)
