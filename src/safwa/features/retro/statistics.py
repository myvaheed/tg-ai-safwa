"""What a closed Sprint adds up to, read off its own record and nothing else: the effort
it took and finished, the Actions it holds, and the Checks tied to a Value answered while
it ran."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.foundation.clock import utcnow

from ..cards.api import Card, CardStage
from ..checks.model import Check, CheckOutcome
from ..planning.model import Sprint, SprintCommitment
from ..planning.use_cases import sprint_metrics
from ..values.model import CheckValue, Value


@dataclass(frozen=True, slots=True)
class SeriesTally:
    """One Check series tied to a Value: how its answers fell while the Sprint ran."""

    title: str
    values: tuple[str, ...]
    passed: int
    missed: int


@dataclass(frozen=True, slots=True)
class RetroStatistics:
    # Effort points: what the Sprint took on — its initial plan and what joined along the
    # way — and, of that, what was finished and what was taken back out.
    taken: float
    done: float
    initial: float
    added: float
    removed: float
    # Actions the Sprint still holds: finished, still to do, and of those the blocked ones.
    finished: int
    remaining: int
    blocked: int
    series: tuple[SeriesTally, ...]

    @property
    def done_share(self) -> int:
        """The finished effort as a whole percent of what was taken on."""
        return round(100 * self.done / self.taken) if self.taken else 0


async def retro_statistics(session: AsyncSession, sprint: Sprint) -> RetroStatistics:
    metrics = await sprint_metrics(session, sprint.id)
    commitments = list(
        await session.scalars(
            select(SprintCommitment).where(SprintCommitment.sprint_id == sprint.id)
        )
    )
    finished = sum(1 for item in commitments if item.result == CardStage.DONE.value)
    open_ids = [
        item.card_id for item in commitments if item.result is None and item.removed_at is None
    ]
    blocked = 0
    if open_ids:
        blocked = len(
            list(
                await session.scalars(
                    select(Card.id).where(Card.id.in_(open_ids), Card.blocked.is_(True))
                )
            )
        )
    return RetroStatistics(
        taken=metrics["committed"] + metrics["added"],
        done=metrics["completed"],
        initial=metrics["committed"],
        added=metrics["added"],
        removed=metrics["removed"],
        finished=finished,
        remaining=len(open_ids),
        blocked=blocked,
        series=await _series_tallies(session, sprint),
    )


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
