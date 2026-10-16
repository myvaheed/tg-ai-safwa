"""What the Cards ask the Advisor to raise on their own: a parent whose Actions are all
finished, a Goal closed over open Cards, an open Card whose Goals are all closed, Cards a
deletion left without a parent, a blocker just set, an Action finished without its time or effort estimate, a day loaded
past what it is meant to hold, a Sprint started without a kind of energy the Backlog has
or with Actions the balance cannot read,
an Action found in Today morning after morning, and — each morning, and when a Sprint
starts — the Goals and Subgoals that still have no Action under them, the Schedules the
plan does not hold, the appointments and Deadlines already passed, and a day planned
without the rest the Sprint holds.

The mornings themselves are written down by work of its own on the same tick, on whether
or not the question about them is switched off."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, time, timedelta
from typing import Any

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
    RunAndAdvise,
    RunContext,
    Tick,
)

from ...constants import INBOX_TAG_NAME
from ..planning.api import SPRINT_STARTED, active_sprint_end_date, plan_load, sprint_is_active
from ..profile.api import (
    EFFORT_TRACKING_REMINDER,
    TIME_TRACKING_REMINDER,
    TODAY_OVERLOAD,
    effort_tracking_on,
    morning_time,
)
from ..schedules.api import appointment_label, workspace_zone
from ..tags.model import CardTag, Tag
from .api import PLANNED_STAGES, SCHEDULE_NOTICE_DAYS, actions_on_stages, order_cards, parents_of
from .hierarchy import branch_actions, branch_ids, card_parent_ids
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
    card_kind_name,
    effort_label,
)
from .use_cases import (
    CARD_ACTIONS_FINISHED,
    CARD_BLOCKED,
    CARD_CREATED,
    CARD_DONE,
    CARD_ORPHANED,
    CARD_REOPENED,
    CARD_TODAY,
    CARD_TODAY_MORNING,
    record_today_morning,
)

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

SCHEDULE_PLAN_REQUEST = (
    "Scheduled Actions due soon, and where each stands:\n{cards}\n"
    "Ask the user in one message whether to take the ones due today or tomorrow into Today, "
    "and the others into the Sprint. Change nothing until they answer."
)

TODAY_OVERLOAD_REQUEST = (
    "Today holds {total} EP, over the {capacity} EP a day is meant to hold; {done} EP of "
    "it is finished already. Still open in Today:\n{cards}\n"
    "Ask the user in one message which of these to move back to Sprint, and say which you "
    "would move first. Change nothing until they answer."
)

EMPTY_PARENTS_REQUEST = (
    "Goals and Subgoals with no Action under them:\n{cards}\n"
    "Ask the user in one message, naming each: plan its Actions now, or create one Action "
    "«Plan the actions for <title>» to come back to it later. Change nothing until they answer."
)

OVERDUE_REQUEST = (
    "Open Cards whose appointment or Deadline has passed:\n{cards}\n"
    "Ask the user in one message, naming each: move its Schedule to a new time, or remove "
    "the Schedule. Change nothing until they answer."
)

BLOCKER_REQUEST = (
    "Blocked Actions:\n{cards}\n"
    "Ask the user in one message whether to set a Reminder to come back to each. If they "
    "want one and say when, route to workspace_mutator. Change nothing until they answer."
)

TIME_TRACKING_REQUEST = (
    "Finished without the time they took:\n{cards}\n"
    "Ask the user in one message how long each took. If they tell you, route to "
    "workspace_mutator to record it on that exact Card, not on its open repeat. "
    "If they do not know, leave it."
)

EFFORT_TRACKING_REQUEST = (
    "Finished without an Effort Points estimate:\n{cards}\n"
    "Ask the user in one message how many EP each cost in their usual state. "
    "If they tell you, route to workspace_mutator to record effort_points on that exact "
    "Card, not on its open repeat. Do not estimate without their answer. "
    "If they do not know, leave it."
)

ENERGY_GAPS = (
    "The Sprint has no open Action for these, and the Backlog has:\n{kinds}\n"
    "Ask whether to take one of each into the Sprint."
)
UNLABELED_ACTIONS = (
    "These open Actions in the Sprint lack labels the balance reads:\n{cards}\n"
    "Ask whether to add them, suggesting labels from each title."
)
ENERGY_BALANCE_REQUEST = (
    "{parts}\nAsk the user in one message. Change nothing until they answer."
)

REST_TODAY_REQUEST = (
    "Today holds no rest, and the Sprint does:\n{cards}\n"
    "Ask the user in one message whether to take one into Today. "
    "Change nothing until they answer."
)

OPEN_UNDER_CLOSED_REQUEST = (
    "Closed with open Cards under them:\n{goals}\n"
    "Ask the user in one message, naming each Goal: keep it closed, reopen it, or close the "
    "open Cards listed under it. To close them, route to workspace_mutator to complete each; "
    "remove the Schedule of one marked repeats before completing it. "
    "Change nothing until they answer."
)

CLOSED_PARENTS_REQUEST = (
    "Open Cards whose Goals are all closed:\n{cards}\n"
    "Ask the user in one message whether to reopen the closed Goals named for each. If they "
    "say yes, route to workspace_mutator to reopen each. Change nothing until they answer."
)

ORPHANS_REQUEST = (
    "Deleted Cards, and the Cards each left without a parent:\n{cards}\n"
    "Ask the user in one message where to put each Card left without a parent: under a Goal "
    "the deleted Card stood under, under another Goal, or nowhere. To put one under a Goal, "
    "route to workspace_mutator to link that parent. Change nothing until they answer."
)

TODAY_STALE_REQUEST = (
    "Open in Today several mornings in a row:\n{cards}\n"
    "Ask the user in one message, naming each with its mornings, whether it is too big, "
    "blocked or not wanted, and what to do with it: split it, do it first today, or move "
    "it back to Sprint. Change nothing until they answer."
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
                Card.blocked_description != "",
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


async def finished_parents(event: Committed) -> tuple[int, ...]:
    return (event.subject_id,)


async def parent_completion_request(session: AsyncSession, items: Sequence[int]) -> str | None:
    parents = await session.scalars(
        select(Card).where(
            Card.id.in_(items),
            Card.kind == CardKind.GOAL.value,
            Card.effective_stage != CardStage.DONE.value,
            Card.archived_at.is_(None),
        ).order_by(Card.id)
    )
    parents = list(parents)
    above = await parents_of(session, [parent.id for parent in parents])
    lines = []
    for parent in parents:
        actions = await branch_actions(session, parent.id)
        if actions and all(action.effective_stage == CardStage.DONE.value for action in actions):
            lines.append(f"- #{parent.id} «{parent.title}» ({card_kind_name(parent.kind, parent.id in above)})")
    if not lines:
        return None
    return (
        "All Actions under these Goals and Subgoals are Done:\n" + "\n".join(lines) + "\n"
        "Ask the user in one message whether to close each too or create a new Action under it. "
        "Change nothing until they answer."
    )


PARENT_COMPLETION_HOOK = HookSpec(
    name="cards.parent_completion",
    owner="cards",
    on=(OnCommitted(kind=CARD_ACTIONS_FINISHED),),
    evaluate=finished_parents,
    effect=Advise(prepare=parent_completion_request),
    title="Goal completion follow-up",
    description="After every Action under a Goal or Subgoal is Done, asks whether to close it too or create a new Action.",
)


async def changed_card(event: Committed) -> tuple[int, ...]:
    return (event.subject_id,)


def _named(card: Card) -> str:
    return f"#{card.id} «{card.title}»"


async def open_cards_under(session: AsyncSession, goal: Card) -> list[Card]:
    """The open Cards under a closed Goal that serve no other open Goal.

    A Card is one of them when each of its parents is that Goal, another of them, or a
    closed Goal; one that also stands under an open Goal stays out, and so does what stands
    under it only through such a Card.
    """
    below = await branch_ids(session, goal.id)
    if not below:
        return []
    parents = await parents_of(session, below)
    held = {parent for found in parents.values() for parent in found}
    stages = dict(
        (await session.execute(select(Card.id, Card.effective_stage).where(Card.id.in_(held | below)))).all()
    )
    named: set[int] = set()
    grown = True
    while grown:
        grown = False
        for card_id in below - named:
            if stages.get(card_id) == CardStage.DONE.value:
                continue
            if all(
                parent == goal.id or parent in named or stages.get(parent) == CardStage.DONE.value
                for parent in parents.get(card_id, ())
            ):
                named.add(card_id)
                grown = True
    return list(await session.scalars(select(Card).where(Card.id.in_(named)).order_by(Card.id)))


async def closed_over_open(card_id: int, context: RunContext) -> tuple[int, ...]:
    """The Goal just closed, when open Cards that serve no other open Goal stand under it.

    Read before anything is queued, so an Action finished, or a Goal closed over nothing
    open, asks nothing.
    """
    async with context.sessions() as session:
        goal = await session.get(Card, card_id)
        if (
            goal is None
            or goal.kind != CardKind.GOAL.value
            or goal.effective_stage != CardStage.DONE.value
        ):
            return ()
        return (goal.id,) if await open_cards_under(session, goal) else ()


async def open_under_closed_request(session: AsyncSession, items: Sequence[int]) -> str | None:
    """The request about the Goals still closed over open Cards that serve no other open Goal."""
    goals = list(
        await session.scalars(
            select(Card)
            .where(
                Card.id.in_([int(item) for item in items]),
                Card.kind == CardKind.GOAL.value,
                Card.effective_stage == CardStage.DONE.value,
                Card.archived_at.is_(None),
            )
            .order_by(Card.id)
        )
    )
    above = await parents_of(session, [goal.id for goal in goals])
    lines = []
    for goal in goals:
        cards = await open_cards_under(session, goal)
        if cards:
            listed = ", ".join(
                _named(card) + (" (repeats)" if card.repeats() else "") for card in cards
            )
            lines.append(
                f"- {_named(goal)} ({card_kind_name(goal.kind, goal.id in above)}): {listed}"
            )
    return OPEN_UNDER_CLOSED_REQUEST.format(goals="\n".join(lines)) if lines else None


OPEN_UNDER_CLOSED_HOOK = HookSpec(
    name="cards.open_under_closed",
    owner="cards",
    on=(OnCommitted(kind=CARD_DONE),),
    evaluate=changed_card,
    effect=RunAndAdvise(run=closed_over_open, prepare=open_under_closed_request),
    title="Open Cards under a closed Goal",
    description=(
        "After a Goal or Subgoal is closed over open Cards that serve no other open Goal, "
        "asks whether to keep it closed, reopen it or close them too."
    ),
)


async def closed_above(session: AsyncSession, card_id: int) -> list[Card]:
    """The closed Goals above an open Card whose parents are all closed, or nothing.

    Its parents come first; a closed Goal above one of them is named too while every parent
    of the Goal below it is closed.
    """
    found: list[Card] = []
    waiting = [card_id]
    seen: set[int] = set()
    while waiting:
        current = waiting.pop(0)
        parents = [
            parent
            for parent_id in await card_parent_ids(session, current)
            if (parent := await session.get(Card, parent_id)) is not None
        ]
        if not parents or any(parent.effective_stage != CardStage.DONE.value for parent in parents):
            if current == card_id:
                return []
            continue
        for parent in parents:
            if parent.id not in seen:
                seen.add(parent.id)
                found.append(parent)
                waiting.append(parent.id)
    return found


async def opened_under_closed(card_id: int, context: RunContext) -> tuple[int, ...]:
    """The Card just created or reopened, when every Goal it stands under is closed."""
    async with context.sessions() as session:
        card = await session.get(Card, card_id)
        if card is None or card.effective_stage == CardStage.DONE.value:
            return ()
        return (card.id,) if await closed_above(session, card.id) else ()


async def closed_parents_request(session: AsyncSession, items: Sequence[int]) -> str | None:
    """The request about the Cards still open under Goals that are all closed."""
    cards = await session.scalars(
        select(Card)
        .where(
            Card.id.in_([int(item) for item in items]),
            Card.effective_stage != CardStage.DONE.value,
            Card.archived_at.is_(None),
        )
        .order_by(Card.id)
    )
    lines = []
    for card in cards:
        if closed := await closed_above(session, card.id):
            lines.append(f"- {_named(card)}: " + ", ".join(_named(goal) for goal in closed))
    return CLOSED_PARENTS_REQUEST.format(cards="\n".join(lines)) if lines else None


CLOSED_PARENTS_HOOK = HookSpec(
    name="cards.closed_parents",
    owner="cards",
    on=(OnCommitted(kind=CARD_CREATED), OnCommitted(kind=CARD_REOPENED)),
    evaluate=changed_card,
    effect=RunAndAdvise(run=opened_under_closed, prepare=closed_parents_request),
    title="Closed parents",
    description=(
        "After a Card is created or reopened under Goals that are all closed, asks whether "
        "to reopen them."
    ),
)


async def orphaned(event: Committed) -> tuple[Mapping[str, Any], ...]:
    return (dict(event.data or {}),)


async def orphans_request(
    session: AsyncSession, items: Sequence[Mapping[str, Any]]
) -> str | None:
    """The request about the Cards each deletion left without a parent, still without one."""
    lines = []
    for item in items:
        children = list(
            await session.scalars(
                select(Card)
                .where(Card.id.in_(item.get("children", [])), Card.archived_at.is_(None))
                .order_by(Card.id)
            )
        )
        held = await parents_of(session, [child.id for child in children])
        left = [child for child in children if child.id not in held]
        if not left:
            continue
        places = list(
            await session.scalars(
                select(Card)
                .where(
                    Card.id.in_(item.get("parent_ids", [])),
                    Card.kind == CardKind.GOAL.value,
                    Card.archived_at.is_(None),
                )
                .order_by(Card.id)
            )
        )
        stood = (
            "; it stood under " + ", ".join(_named(place) for place in places) if places else ""
        )
        lines.append(
            f"- «{item.get('title', '')}»{stood}. Left without a parent: "
            + ", ".join(_named(child) for child in left)
        )
    return ORPHANS_REQUEST.format(cards="\n".join(lines)) if lines else None


ORPHANS_HOOK = HookSpec(
    name="cards.orphans",
    owner="cards",
    on=(OnCommitted(kind=CARD_ORPHANED),),
    evaluate=orphaned,
    effect=Advise(prepare=orphans_request),
    title="Cards without a parent",
    description=(
        "After a Card is deleted, asks where to put the Cards it left without a parent, "
        "offering the Goals it stood under."
    ),
)


async def finished_cards(event: Committed) -> tuple[int, ...]:
    return (event.subject_id,)


async def time_tracking_request(session: AsyncSession, items: Sequence[int]) -> str | None:
    """The request about the Actions still Done, not archived and without a time, or nothing."""
    cards = list(
        await session.scalars(
            select(Card)
            .where(
                Card.id.in_([int(item) for item in items]),
                Card.kind == CardKind.ACTION.value,
                Card.effective_stage == CardStage.DONE.value,
                Card.archived_at.is_(None),
                Card.tracked_mins.is_(None),
            )
            .order_by(Card.id)
        )
    )
    if not cards:
        return None
    effort_tracking = await effort_tracking_on(session)
    lines = "\n".join(
        f"- #{card.id} «{card.title}»"
        + (f" ({effort_label(card.effort_points)} EP)" if effort_tracking else "")
        for card in cards
    )
    return TIME_TRACKING_REQUEST.format(cards=lines)


# Silent while Time tracking is off in the Profile, whatever its own switch says:
# `hook_switched_on` answers for it.
TIME_TRACKING_REMINDER_HOOK = HookSpec(
    name=TIME_TRACKING_REMINDER,
    owner="cards",
    on=(OnCommitted(kind=CARD_DONE),),
    evaluate=finished_cards,
    effect=Advise(prepare=time_tracking_request),
    title="Time tracking reminder",
    description="After an Action is Done without its time, asks how long it took.",
)


async def effort_tracking_request(session: AsyncSession, items: Sequence[int]) -> str | None:
    """The request about the Actions still Done, not archived and without an estimate."""
    cards = await session.scalars(
        select(Card)
        .where(
            Card.id.in_([int(item) for item in items]),
            Card.kind == CardKind.ACTION.value,
            Card.effective_stage == CardStage.DONE.value,
            Card.archived_at.is_(None),
            Card.effort_points.is_(None),
        )
        .order_by(Card.id)
    )
    lines = "\n".join(f"- #{card.id} «{card.title}»" for card in cards)
    return EFFORT_TRACKING_REQUEST.format(cards=lines) if lines else None


EFFORT_TRACKING_REMINDER_HOOK = HookSpec(
    name=EFFORT_TRACKING_REMINDER,
    owner="cards",
    on=(OnCommitted(kind=CARD_DONE),),
    evaluate=finished_cards,
    effect=Advise(prepare=effort_tracking_request),
    title="Effort Points reminder",
    description="After an Action is Done without an estimate, asks how many Effort Points it cost.",
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
    day = day_start.astimezone(await workspace_zone(session)).date()
    load = await plan_load(session, open_today, start_date=day, end_date=day)
    total = load.effort + (finished or 0)
    if total <= TODAY_CAPACITY_EP:
        return None
    lines = "\n".join(
        f"- #{card.id} «{card.title}» ("
        + (f"{load.counts[card.id] if load.counts[card.id] is not None else '?'} executions × " if load.counts[card.id] != 1 else "")
        + f"{effort_label(card.effort_points)} EP)"
        for card in open_today
    )
    if load.unknown_schedules:
        lines += "\nSchedule quantities are unknown; this total is a lower bound."
    return TODAY_OVERLOAD_REQUEST.format(
        total=effort_label(total),
        capacity=TODAY_CAPACITY_EP,
        done=effort_label(finished or 0),
        cards=lines,
    )


# Silent while Effort Points are off in the Profile, whatever its own switch says:
# `hook_switched_on` answers for it.
TODAY_OVERLOAD_HOOK = HookSpec(
    name=TODAY_OVERLOAD,
    owner="cards",
    on=(OnCommitted(kind=CARD_TODAY),),
    evaluate=entered_today,
    effect=Advise(prepare=today_overload_request),
    title="Today overload",
    description="After an Action enters Today, asks what to move back when the day holds more than it is meant to.",
)


async def plan_check_due(event: Committed | Tick) -> tuple[str, ...]:
    return (PLAN_CHECK,)


async def schedule_plan_request(
    session: AsyncSession, items: Sequence[str], *, now: datetime | None = None
) -> str | None:
    """The open Actions whose Schedule comes due before the plan holds them.

    Read on the workspace's local days as the question is about to be said: an appointment
    today or tomorrow outside Today, one by the Sprint's last day in Backlog, and a daily
    quota with executions left today outside Today. In Planning no Sprint runs, so only
    today and tomorrow count.
    """
    end = await active_sprint_end_date(session)
    tz = await workspace_zone(session)
    moment = now or utcnow()
    today = moment.astimezone(tz).date()
    near = today + timedelta(days=SCHEDULE_NOTICE_DAYS)
    cards = await session.scalars(
        select(Card)
        .where(
            Card.kind == CardKind.ACTION.value,
            Card.archived_at.is_(None),
            Card.effective_stage.not_in([stage.value for stage in TERMINAL_STAGES]),
            Card.period_start.is_not(None),
        )
        .order_by(Card.period_start, Card.id)
    )
    lines: list[str] = []
    for card in cards:
        stage = CardStage(card.effective_stage)
        rule = card.schedule_record.rule
        if card.scheduled_at is not None and card.scheduled_at >= moment:
            when = card.scheduled_at.astimezone(tz)
            if (when.date() <= near and stage is not CardStage.TODAY) or (
                end is not None and when.date() <= end and stage is CardStage.BACKLOG
            ):
                lines.append(
                    f"- #{card.id} «{card.title}»: {appointment_label(rule, when, tz, '%Y-%m-%d')}, "
                    f"in {stage.value.capitalize()}"
                )
        elif (
            rule["kind"] == "quota"
            and rule["period"] == "day"
            and stage is not CardStage.TODAY
            and card.period_start.astimezone(tz).date() <= today
        ):
            lines.append(
                f"- #{card.id} «{card.title}»: {rule['count']} times a day, in {stage.value.capitalize()}"
            )
    if not lines:
        return None
    return SCHEDULE_PLAN_REQUEST.format(cards="\n".join(lines))


SCHEDULE_PLAN_HOOK = HookSpec(
    name="cards.schedule_plan",
    owner="cards",
    on=(OnCommitted(kind=SPRINT_STARTED), OnTick(at=morning_time)),
    evaluate=plan_check_due,
    effect=Advise(prepare=schedule_plan_request),
    title="Schedule outside the plan",
    description="When a Sprint starts and each morning, asks about the scheduled Actions the plan does not hold.",
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
            Card.kind == CardKind.GOAL.value,
            Card.archived_at.is_(None),
            Card.created_at <= cutoff,
            ~select(CardTag.card_id)
            .join(Tag, Tag.id == CardTag.tag_id)
            .where(CardTag.card_id == Card.id, Tag.name == INBOX_TAG_NAME)
            .exists(),
        )
        .order_by(Card.id)
    )
    empty = [card for card in parents if not await branch_actions(session, card.id)]
    if not empty:
        return None
    above = await parents_of(session, [card.id for card in empty])
    lines = "\n".join(
        f"- #{card.id} «{card.title}» ({card_kind_name(card.kind, card.id in above)})" for card in empty
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


async def overdue_request(
    session: AsyncSession, items: Sequence[str], *, now: datetime | None = None
) -> str | None:
    """The open Actions whose appointment has passed and the open Goals whose Deadline has.

    Read as the question is about to be said, the oldest first. An appointment with no
    clock passes when its day ends.
    """
    tz = await workspace_zone(session)
    moment = now or utcnow()
    today = moment.astimezone(tz).date()
    cards = list(
        await session.scalars(
            select(Card)
            .where(
                Card.archived_at.is_(None),
                Card.effective_stage.not_in([stage.value for stage in TERMINAL_STAGES]),
                Card.period_start < moment,
            )
            .order_by(Card.period_start, Card.id)
        )
    )
    above = await parents_of(session, [card.id for card in cards])
    lines: list[str] = []
    for card in cards:
        if card.deadline_at is not None:
            lines.append(
                f"- #{card.id} «{card.title}» ({card_kind_name(card.kind, card.id in above)}, "
                f"Deadline {card.deadline_at.astimezone(tz):%Y-%m-%d %H:%M})"
            )
            continue
        when = card.scheduled_at
        rule = card.schedule_record.rule if card.schedule_record else {}
        if when is None or (rule.get("all_day") and when.astimezone(tz).date() >= today):
            continue
        lines.append(
            f"- #{card.id} «{card.title}» (Action, {appointment_label(rule, when, tz, '%Y-%m-%d')}"
            + (", repeats" if card.repeats() else "")
            + f", in {card.effective_stage.capitalize()})"
        )
    if not lines:
        return None
    return OVERDUE_REQUEST.format(cards="\n".join(lines))


OVERDUE_HOOK = HookSpec(
    name="cards.overdue",
    owner="cards",
    on=(OnTick(at=morning_time),),
    evaluate=check_due,
    effect=Advise(prepare=overdue_request),
    title="Overdue Schedules",
    description="Each morning, asks whether to move or remove each passed appointment or Deadline of an open Card.",
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
    for kind, held in carried.items():
        carried[kind] = await order_cards(session, held)
    return carried


async def _unlabeled_lines(session: AsyncSession) -> list[str]:
    """Each open Action in the Sprint with no Category or no Energy type, and what it lacks."""
    cards = await order_cards(session, await actions_on_stages(session, *PLANNED_STAGES))
    ids = [card.id for card in cards]
    with_category = set(
        await session.scalars(select(CardCategory.card_id).where(CardCategory.card_id.in_(ids)))
    )
    with_energy = set(
        await session.scalars(
            select(CardEnergyType.card_id).where(CardEnergyType.card_id.in_(ids))
        )
    )
    lines = []
    for card in cards:
        lacks = [
            label
            for label, held in (("no Category", with_category), ("no Energy type", with_energy))
            if card.id not in held
        ]
        if lacks:
            lines.append(f"- #{card.id} «{card.title}»: {', '.join(lacks)}")
    return lines


async def energy_balance_request(session: AsyncSession, items: Sequence[str]) -> str | None:
    """The request about each kind of energy, and rest, on no open Action in the Sprint while
    an open Backlog Action carries it, and about the open Actions in the Sprint with no
    Category or no Energy type — read as the question is about to be said."""
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
    parts = []
    if lines:
        parts.append(ENERGY_GAPS.format(kinds="\n".join(lines)))
    if unlabeled := await _unlabeled_lines(session):
        parts.append(UNLABELED_ACTIONS.format(cards="\n".join(unlabeled)))
    if not parts:
        return None
    return ENERGY_BALANCE_REQUEST.format(parts="\n".join(parts))


ENERGY_BALANCE_HOOK = HookSpec(
    name="cards.energy_balance",
    owner="cards",
    on=(OnCommitted(kind=SPRINT_STARTED),),
    evaluate=plan_check_due,
    effect=Advise(prepare=energy_balance_request),
    title="Energy balance",
    description=(
        "When a Sprint starts, asks about the kinds of energy, and the rest, it has no Action "
        "for while the Backlog does, and about its Actions with no Category or Energy type."
    ),
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
