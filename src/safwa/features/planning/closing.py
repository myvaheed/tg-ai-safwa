"""What a Sprint adds up to when it closes, written down with its end and never again: the
effort it took and finished, the Actions it holds, how both fell by Category, by Energy
type, by the two together and by day, the Checks tied to a Value answered while it ran, and — when Time tracking
was on as it closed — the time its finished Actions took. The retro screen
shows this record and the retro analysis reads it; what happens to those Actions and
Checks afterwards is another Sprint's story."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.foundation.clock import utcnow

from ..cards.api import Card, CardStage
from ..cards.model import CardCategory, CardEnergyType, Category, EnergyType, TodayDay
from ..checks.model import Check, CheckOutcome
from ..profile.api import active_day_minutes, time_tracking_on
from ..values.model import CheckValue, Value
from .api import action_counts, effort_sums
from .model import Sprint, SprintCommitment

# The bucket an Action with no Category, or no Energy type, falls into: the shares of a
# Sprint's effort add up to the whole only when every Action is in one.
NONE_BUCKET = "none"
CATEGORY_BUCKETS = (*(kind.value for kind in Category), NONE_BUCKET)
ENERGY_BUCKETS = (*(kind.value for kind in EnergyType), NONE_BUCKET)
# How many of the longest finished Actions a Sprint's record names.
LONGEST_SHOWN = 3


@dataclass(frozen=True, slots=True)
class SeriesTally:
    """One Check series tied to a Value: how its answers fell while the Sprint ran."""

    title: str
    values: tuple[str, ...]
    passed: int
    missed: int


@dataclass(frozen=True, slots=True)
class Bucket:
    """One Category or Energy type: the effort and Actions taken in, and of those, finished.

    An Action carrying two Categories is in both buckets whole, so buckets add up to more
    than the Sprint; a share is read against the buckets' own sum.
    """

    effort: float = 0.0
    done_effort: float = 0.0
    count: int = 0
    done_count: int = 0
    # Of the finished ones, those that carry a time: the minutes, how many, and their effort.
    minutes: int = 0
    timed_count: int = 0
    timed_effort: float = 0.0


@dataclass(frozen=True, slots=True)
class TimedAction:
    """One finished Action and the minutes it took, titled as it was when the Sprint closed."""

    title: str
    minutes: int


@dataclass(frozen=True, slots=True)
class DayTally:
    """One local day of the Sprint: the Actions in Today that morning, the ones finished
    that day, and how the finished ones fell by Category and Energy type."""

    day: str
    planned: int
    done: int
    done_by_category: dict[str, int] = field(default_factory=dict)
    done_by_energy: dict[str, int] = field(default_factory=dict)
    unknown_schedules: int = 0


@dataclass(frozen=True, slots=True)
class RetroStatistics:
    # Effort points: what the Sprint took on — its initial plan and what joined along the
    # way — and, of that, what was finished and what was taken back out.
    taken: float
    done: float
    initial: float
    added: float
    removed: float
    # Actions the Sprint took on, and the ones it still holds: finished, still to do, and
    # of those the blocked ones.
    planned: int
    finished: int
    remaining: int
    blocked: int
    # The Actions the model marked its Success criterion as resting on, how many of them
    # finished, and the Actions it had not yet said about either way — so "0 of 0" with
    # nothing unknown is a Sprint with no key Action, not one the model never classified.
    key_total: int
    key_finished: int
    key_unknown: int
    by_category: dict[str, Bucket]
    by_energy: dict[str, Bucket]
    days: tuple[DayTally, ...]
    series: tuple[SeriesTally, ...]
    # Whether Time tracking was on as the Sprint closed, and the active day then; of its
    # finished Actions, the minutes of those that carry a time, how many do, their effort,
    # and the longest. A Sprint closed with it off, or before a record kept it, has none.
    time_tracking: bool = False
    active_day_minutes: int = 0
    minutes: int = 0
    timed: int = 0
    timed_effort: float = 0.0
    longest: tuple[TimedAction, ...] = ()
    unestimated: int = 0
    unknown_schedules: int = 0
    # Each Category's Actions by Energy type; a record written before it kept none.
    by_category_energy: dict[str, dict[str, Bucket]] = field(default_factory=dict)

    @property
    def day_share(self) -> int | None:
        """The time a day on average, as a whole percent of the active day; None untracked."""
        if not self.time_tracking or not self.active_day_minutes:
            return None
        return round(100 * self.minutes / (len(self.days) * self.active_day_minutes))

    @property
    def done_share(self) -> int:
        """The finished effort as a whole percent of what was taken on."""
        return round(100 * self.done / self.taken) if self.taken else 0

    @property
    def first_day(self) -> date:
        return date.fromisoformat(self.days[0].day)

    @property
    def last_day(self) -> date:
        return date.fromisoformat(self.days[-1].day)

    def as_record(self) -> dict[str, Any]:
        """The numbers as the Sprint row keeps them."""
        return asdict(self)

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> RetroStatistics:
        nested = {"series", "by_category", "by_energy", "by_category_energy", "days", "longest"}
        return cls(
            **{key: value for key, value in record.items() if key not in nested},
            by_category={name: Bucket(**bucket) for name, bucket in record["by_category"].items()},
            by_energy={name: Bucket(**bucket) for name, bucket in record["by_energy"].items()},
            days=tuple(DayTally(**day) for day in record["days"]),
            series=tuple(
                SeriesTally(
                    tally["title"], tuple(tally["values"]), tally["passed"], tally["missed"]
                )
                for tally in record["series"]
            ),
            longest=tuple(TimedAction(**action) for action in record.get("longest", ())),
            by_category_energy={
                category: {name: Bucket(**bucket) for name, bucket in energies.items()}
                for category, energies in record.get("by_category_energy", {}).items()
            },
        )


async def sprint_closing(
    session: AsyncSession, sprint: Sprint, *, timezone: str
) -> RetroStatistics:
    """The Sprint's numbers as they stand now: read once, as it closes."""
    commitments = list(
        await session.scalars(
            select(SprintCommitment).where(SprintCommitment.sprint_id == sprint.id)
        )
    )
    effort = effort_sums(commitments)
    counts = action_counts(commitments)
    finished = sum(1 for item in commitments if item.result == CardStage.DONE.value)
    open_ids = [
        item.card_id for item in commitments if item.result is None and item.removed_at is None
    ]
    blocked = 0
    if open_ids:
        blocked_ids = set(await session.scalars(select(Card.id).where(Card.id.in_(open_ids), Card.blocked.is_(True))))
        blocked = sum(item.quantity for item in commitments if item.card_id in blocked_ids)
    held = [item for item in commitments if item.removed_at is None]
    key = [item for item in held if item.key_action]
    labels = await _labels(session, [item.card_id for item in commitments])
    tz = ZoneInfo(timezone)
    tracking = await time_tracking_on(session)
    timed = await _timed_actions(session, commitments) if tracking else []
    minutes = {card_id: spent for card_id, _, spent in timed}
    effort_of = {item.card_id: item.effort_snapshot for item in commitments}
    return RetroStatistics(
        taken=effort["committed"] + effort["added"],
        done=effort["completed"],
        initial=effort["committed"],
        added=effort["added"],
        removed=effort["removed"],
        planned=counts["committed"] + counts["added"],
        finished=finished,
        remaining=sum(item.quantity for item in held if item.result is None),
        blocked=blocked,
        key_total=sum(item.quantity - item.removed_quantity for item in key),
        key_finished=sum(1 for item in key if item.result == CardStage.DONE.value),
        key_unknown=sum(item.quantity - item.removed_quantity for item in held if item.key_action is None),
        by_category=_buckets(commitments, labels[0], CATEGORY_BUCKETS, minutes),
        by_energy=_buckets(commitments, labels[1], ENERGY_BUCKETS, minutes),
        by_category_energy={
            category: _buckets(
                [item for item in commitments if category in labels[0][item.card_id]],
                labels[1],
                ENERGY_BUCKETS,
                minutes,
            )
            for category in CATEGORY_BUCKETS
        },
        days=await _day_tallies(session, sprint, commitments, labels, tz),
        series=await _series_tallies(session, sprint),
        time_tracking=tracking,
        active_day_minutes=await active_day_minutes(session) if tracking else 0,
        minutes=sum(minutes.values()),
        timed=len(timed),
        timed_effort=sum(effort_of[card_id] or 0 for card_id in minutes),
        unestimated=counts["unestimated"],
        unknown_schedules=counts["unknown_schedules"],
        longest=tuple(TimedAction(title, spent) for _, title, spent in timed[:LONGEST_SHOWN]),
    )


async def _timed_actions(
    session: AsyncSession, commitments: list[SprintCommitment]
) -> list[tuple[int, str, int]]:
    """The finished Actions that carry a time, longest first; of two alike, the earlier finished."""
    finished = [item.card_id for item in commitments if item.result == CardStage.DONE.value]
    if not finished:
        return []
    rows = await session.execute(
        select(Card.id, Card.title, Card.tracked_mins)
        .where(Card.id.in_(finished), Card.tracked_mins.is_not(None))
        .order_by(Card.tracked_mins.desc(), Card.completed_at, Card.id)
    )
    return [(card_id, title, spent) for card_id, title, spent in rows]


Labels = tuple[dict[int, list[str]], dict[int, list[str]]]


async def _labels(session: AsyncSession, card_ids: list[int]) -> Labels:
    """Each Action's Categories and Energy types; an Action with none is in `NONE_BUCKET`."""
    categories: dict[int, list[str]] = {card_id: [] for card_id in card_ids}
    energies: dict[int, list[str]] = {card_id: [] for card_id in card_ids}
    if card_ids:
        for row in await session.execute(
            select(CardCategory.card_id, CardCategory.category).where(
                CardCategory.card_id.in_(card_ids)
            )
        ):
            categories[row.card_id].append(row.category)
        for row in await session.execute(
            select(CardEnergyType.card_id, CardEnergyType.energy_type).where(
                CardEnergyType.card_id.in_(card_ids)
            )
        ):
            energies[row.card_id].append(row.energy_type)
    for labelled in (categories, energies):
        for names in labelled.values():
            if not names:
                names.append(NONE_BUCKET)
    return categories, energies


def _buckets(
    commitments: list[SprintCommitment],
    labelled: dict[int, list[str]],
    names: tuple[str, ...],
    minutes: dict[int, int],
) -> dict[str, Bucket]:
    sums: dict[str, Counter[str]] = {name: Counter() for name in names}
    for item in commitments:
        spent = minutes.get(item.card_id)
        for name in labelled[item.card_id]:
            sums[name].update(effort=(item.effort_snapshot or 0) * item.quantity, count=item.quantity)
            if item.result == CardStage.DONE.value:
                sums[name].update(done_effort=item.effort_snapshot or 0, done_count=1)
            if spent is not None:
                sums[name].update(minutes=spent, timed_count=1, timed_effort=item.effort_snapshot or 0)
    return {name: Bucket(**tally) for name, tally in sums.items()}


async def _day_tallies(
    session: AsyncSession,
    sprint: Sprint,
    commitments: list[SprintCommitment],
    labels: Labels,
    tz: ZoneInfo,
) -> tuple[DayTally, ...]:
    """One tally per local day the Sprint ran, first to last: the calendar is the Sprint's
    own, so a Sprint whose every Action was deleted still has its days.

    A Sprint that expires goes just past its last midnight, so the last day is its planned
    end at the latest; one finished early ends on the day it was finished; and one whose
    planned end was already behind it ran on the day it started.
    """
    ended = sprint.actual_ended_at or utcnow()
    first = sprint.actual_started_at.astimezone(tz).date()
    last = max(first, min(ended.astimezone(tz).date(), sprint.planned_end_date))
    card_ids = [item.card_id for item in commitments]
    mornings: dict[date, int] = {}
    unknown: dict[date, int] = {}
    by_day: dict[date, list[int]] = {}
    if card_ids:
        for row in await session.execute(
            select(TodayDay.day, TodayDay.planned_count).where(
                TodayDay.card_id.in_(card_ids), TodayDay.day >= first, TodayDay.day <= last
            )
        ):
            mornings[row.day] = mornings.get(row.day, 0) + (row.planned_count or 0)
            unknown[row.day] = unknown.get(row.day, 0) + (row.planned_count is None)
        finished = await session.execute(
            select(Card.id, Card.completed_at).where(
                Card.id.in_(card_ids), Card.completed_at.is_not(None)
            )
        )
        for card_id, completed_at in finished:
            by_day.setdefault(completed_at.astimezone(tz).date(), []).append(card_id)
    days = []
    day = first
    while day <= last:
        done_ids = by_day.get(day, [])
        days.append(
            DayTally(
                day=day.isoformat(),
                planned=mornings.get(day, 0),
                done=len(done_ids),
                done_by_category=_count_labels(done_ids, labels[0]),
                done_by_energy=_count_labels(done_ids, labels[1]),
                unknown_schedules=unknown.get(day, 0),
            )
        )
        day += timedelta(days=1)
    return tuple(days)


def _count_labels(card_ids: list[int], labelled: dict[int, list[str]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for card_id in card_ids:
        for name in labelled[card_id]:
            counts[name] = counts.get(name, 0) + 1
    return counts


async def _series_tallies(session: AsyncSession, sprint: Sprint) -> tuple[SeriesTally, ...]:
    """Every series tied to a Value, tallied over the answers given while the Sprint ran.

    A Value rides on the series' live instance, so the link names the series; the answers
    are then counted over every instance of it, by the first time each was resolved.
    """
    linked = await session.execute(
        select(Check, Value.name)
        .join(CheckValue, CheckValue.check_id == Check.id)
        .join(Value, Value.id == CheckValue.value_id)
        .order_by(Check.id, Value.name)
    )
    by_series: dict[int, tuple[str, list[str]]] = {}
    for check, value_name in linked:
        series_id = check.series_id or check.id
        title, names = by_series.setdefault(series_id, (check.title, []))
        names.append(value_name)
    if not by_series:
        return ()
    started, ended = sprint.actual_started_at, sprint.actual_ended_at or utcnow()
    answered = await session.scalars(
        select(Check).where(
            or_(Check.series_id.in_(by_series), Check.id.in_(by_series)),
            Check.outcome.is_not(None),
            Check.resolved_at >= started,
            Check.resolved_at <= ended,
        )
    )
    counts: dict[int, list[int]] = {}
    for check in answered:
        tally = counts.setdefault(check.series_id or check.id, [0, 0])
        tally[0 if check.outcome == CheckOutcome.PASSED.value else 1] += 1
    return tuple(
        SeriesTally(by_series[series_id][0], tuple(by_series[series_id][1]), passed, missed)
        for series_id, (passed, missed) in sorted(counts.items())
    )
