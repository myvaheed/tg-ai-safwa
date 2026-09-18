"""What the Cards ask the Advisor to raise on their own: a blocker just set, a day loaded
past what it is meant to hold, a Sprint started without a kind of energy the Backlog has,
an Action found in Today morning after morning, and — each morning, and when a Sprint
starts — the Goals and Subgoals that still have no Action under them, the Hard Times the
plan does not hold, and a day planned without the rest the Sprint holds.

The mornings themselves are written down by work of its own on the same tick, on whether
or not the question about them is switched off."""

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
    Run,
    RunContext,
    Tick,
)

from ..planning.api import SPRINT_STARTED, active_sprint_end_date, sprint_is_active
from ..profile.api import morning_time
from .api import HARD_TIME_NOTICE_DAYS, PLANNED_STAGES, actions_on_stages
from .hard_time import workspace_zone
from .hierarchy import branch_actions
from .model import (
    TERMINAL_STAGES,
    Card,
    CardCategory,
    CardEnergyType,
    CardKind,
    CardStage,
    Category,
    EnergyType,
    Priority,
    TodayDay,
    effort_label,
)
from .use_cases import CARD_BLOCKED, CARD_TODAY, CARD_TODAY_MORNING, record_today_morning

# How old a Goal or a Subgoal is before having no Action under it is worth a question.
EMPTY_PARENT_GRACE_DAYS = 1
# What a day is meant to hold, in effort points: the open Actions in Today and the ones
# finished that day, together.
TODAY_CAPACITY_EP = 15
# What a plan check keeps as its one pending item: it is about the whole plan, not a Card.
PLAN_CHECK = "plan"
# How many Backlog Actions the energy question names for each kind the Sprint has none of.
ENERGY_CANDIDATES = 3
# The kinds of energy a Sprint is asked to spread: the four energy types, and rest.
ENERGY_KINDS = {
    **{kind.value: f"{kind.value.capitalize()} energy" for kind in EnergyType},
    Category.REST.value: "Rest",
}
# How many mornings in a row an open Action stands in Today before it is asked about, and
# again at each multiple.
TODAY_STALE_DAYS = 3

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

ENERGY_BALANCE_REQUEST = (
    "The Sprint has no open Action for these, and the Backlog has:\n{kinds}\n"
    "Ask the user in one message whether to take one of each into the Sprint, so its energy "
    "is spread. Do not move anything without their answer."
)

REST_TODAY_REQUEST = (
    "Today holds no rest, and the Sprint does:\n{cards}\n"
    "Ask the user in one message whether to take one into Today, so the rest is planned "
    "instead of forced. Do not move anything without their answer."
)

TODAY_STALE_REQUEST = (
    "In Today morning after morning, still open:\n{cards}\n"
    "Ask the user in one message, naming each with its mornings, whether it is too big, "
    "blocked or not wanted, and what to do with it: split it, do it first today, or move "
    "it back to Sprint. Do not change anything without their answer."
)


async def _local_day_start(session: AsyncSession, now: datetime | None) -> datetime:
    """When the workspace's local day began, in UTC."""
    tz = await workspace_zone(session)
    return datetime.combine(
        (now or utcnow()).astimezone(tz).date(), time(0, 0), tzinfo=tz
    ).astimezone(UTC)


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
    day_start = await _local_day_start(session, now)
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
    return (PLAN_CHECK,)


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


async def _energy_kinds(
    session: AsyncSession, stages: Sequence[CardStage]
) -> dict[str, list[Card]]:
    """Each kind of energy, and rest, with the open Actions on these stages that carry it,
    Critical first."""
    cards = {card.id: card for card in await actions_on_stages(session, *stages)}
    rows = await session.execute(
        select(CardEnergyType.card_id, CardEnergyType.energy_type)
        .where(CardEnergyType.card_id.in_(list(cards)))
        .union_all(
            select(CardCategory.card_id, CardCategory.category).where(
                CardCategory.card_id.in_(list(cards)),
                CardCategory.category == Category.REST.value,
            )
        )
    )
    carried: dict[str, list[Card]] = {}
    for card_id, kind in rows:
        carried.setdefault(kind, []).append(cards[card_id])
    for held in carried.values():
        held.sort(key=lambda card: (card.priority != Priority.CRITICAL.value, card.id))
    return carried


async def energy_balance_request(session: AsyncSession, items: Sequence[str]) -> str | None:
    """The request about each kind of energy, and rest, on no open Action in the Sprint while
    an open Backlog Action carries it — read as the question is about to be said."""
    if not await sprint_is_active(session):
        return None
    planned = await _energy_kinds(session, PLANNED_STAGES)
    backlog = await _energy_kinds(session, (CardStage.BACKLOG,))
    lines: list[str] = []
    for kind, label in ENERGY_KINDS.items():
        if kind in planned or kind not in backlog:
            continue
        named = ", ".join(
            f"#{card.id} «{card.title}»"
            + (" (Critical)" if card.priority == Priority.CRITICAL.value else "")
            for card in backlog[kind][:ENERGY_CANDIDATES]
        )
        lines.append(f"- {label}: {named}")
    if not lines:
        return None
    return ENERGY_BALANCE_REQUEST.format(kinds="\n".join(lines))


ENERGY_BALANCE_HOOK = HookSpec(
    name="cards.energy_balance",
    owner="cards",
    on=(OnCommitted(kind=SPRINT_STARTED),),
    evaluate=plan_check_due,
    effect=Advise(prepare=energy_balance_request),
    title="Energy balance",
    description="When a Sprint starts, asks about the kinds of energy, and the rest, it has no Action for while the Backlog does.",
)


async def rest_today_request(
    session: AsyncSession, items: Sequence[str], *, now: datetime | None = None
) -> str | None:
    """The request while the day holds no rest and the Sprint does, or nothing.

    Rest in the day is an open Action of the Rest category in Today, or one finished that
    local day; what the Sprint holds is read as the question is about to be said.
    """
    if not await sprint_is_active(session):
        return None
    rest_ids = select(CardCategory.card_id).where(CardCategory.category == Category.REST.value)
    open_rest = list(
        await session.scalars(
            select(Card)
            .where(
                Card.kind == CardKind.ACTION.value,
                Card.effective_stage.in_([stage.value for stage in PLANNED_STAGES]),
                Card.archived_at.is_(None),
                Card.id.in_(rest_ids),
            )
            .order_by(Card.id)
        )
    )
    if not open_rest or any(card.effective_stage == CardStage.TODAY.value for card in open_rest):
        return None
    day_start = await _local_day_start(session, now)
    rested = await session.scalar(
        select(func.count()).select_from(Card).where(
            Card.kind == CardKind.ACTION.value,
            Card.id.in_(rest_ids),
            Card.completed_at >= day_start,
            Card.completed_at < day_start + timedelta(days=1),
        )
    )
    if rested:
        return None
    lines = "\n".join(f"- #{card.id} «{card.title}»" for card in open_rest)
    return REST_TODAY_REQUEST.format(cards=lines)


REST_TODAY_HOOK = HookSpec(
    name="cards.rest_today",
    owner="cards",
    on=(OnTick(at=morning_time),),
    evaluate=check_due,
    effect=Advise(prepare=rest_today_request),
    title="Rest in Today",
    description="Each morning, asks about taking one of the Sprint's Rest Actions into Today when the day holds none.",
)


async def record_today_mornings(marker: str, context: RunContext) -> None:
    async with context.sessions() as session:
        await record_today_morning(session)
        await session.commit()


TODAY_MORNINGS_HOOK = HookSpec(
    name="cards.today_mornings",
    owner="cards",
    on=(OnTick(at=morning_time),),
    evaluate=check_due,
    effect=Run(record_today_mornings),
    title="Today mornings",
    description="Each morning, writes down which open Actions stand in Today.",
)


async def found_in_today(event: Committed) -> tuple[int, ...]:
    return (event.subject_id,)


async def today_mornings_in_a_row(session: AsyncSession, card_id: int) -> int:
    """How many mornings in a row, up to the last one written down, the Action stood in Today."""
    days = list(
        await session.scalars(
            select(TodayDay.day).where(TodayDay.card_id == card_id).order_by(TodayDay.day.desc())
        )
    )
    run = 0
    for index, day in enumerate(days):
        if day != days[0] - timedelta(days=index):
            break
        run += 1
    return run


async def today_stale_request(session: AsyncSession, items: Sequence[int]) -> str | None:
    """The request about the Actions still open in Today whose mornings in a row are a
    multiple of TODAY_STALE_DAYS now."""
    lines: list[str] = []
    for item in sorted(int(item) for item in items):
        card = await session.get(Card, item)
        if (
            card is None
            or card.effective_stage != CardStage.TODAY.value
            or card.archived_at is not None
        ):
            continue
        run = await today_mornings_in_a_row(session, card.id)
        if not run or run % TODAY_STALE_DAYS:
            continue
        lines.append(f"- #{card.id} «{card.title}»: {run} mornings in a row")
    if not lines:
        return None
    return TODAY_STALE_REQUEST.format(cards="\n".join(lines))


TODAY_STALE_HOOK = HookSpec(
    name="cards.today_stale",
    owner="cards",
    on=(OnCommitted(kind=CARD_TODAY_MORNING),),
    evaluate=found_in_today,
    effect=Advise(prepare=today_stale_request),
    title="Stale in Today",
    description=f"After an Action has stood in Today {TODAY_STALE_DAYS} mornings in a row, asks what to do with it.",
)
