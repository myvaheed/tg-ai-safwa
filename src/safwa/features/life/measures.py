"""What each Life in weeks picture counts, week by week, and what its heading says.

Nothing here reads the database or draws. The records go in; out come a paint for each week
that has something to show and the lines above the grid, so every number is checked apart
from the picture.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta
from enum import StrEnum

from tg_agent_shell.foundation.errors import DomainError

from ...foundation.charts import SURFACE
from ..cards.telegram import CATEGORY_COLORS, ENERGY_COLORS
from .records import LifeAction, LifeRecords
from .weeks import Cell, LifeGrid, cells, day_label, ordinal

# How many weeks a trend compares: the last ones, against as many before them.
LIFE_TREND_WEEKS = 4
# How many Values the picture by Value offers and colours, the most served first.
LIFE_VALUES_SHOWN = 8


class LifeChart(StrEnum):
    FEELING = "feeling"
    ACTIONS = "actions"
    EFFORT = "effort"
    SPRINTS = "sprints"
    CATEGORY = "category"
    ENERGY = "energy"
    VALUE = "value"


# The pictures that show what the finished Actions carried, as a mix or as one share.
GROUP_CHARTS = (LifeChart.CATEGORY, LifeChart.ENERGY, LifeChart.VALUE)

FEELING_STOPS = ("#d03b3b", "#eb6834", "#eda100", "#8cc43c", "#0ca30c")
ACTION_STOPS = ("#c6e9c9", "#3fa34d", "#14532d")
EFFORT_STOPS = ("#fde2c8", "#eb6834", "#8a2c0b")
MET_COLORS = {True: "#0ca30c", False: "#d03b3b", None: "#888780"}
RUNNING_COLOR = "#2a78d6"
VALUE_COLORS = (
    "#2a78d6", "#eb6834", "#1baf7a", "#e87ba4", "#4a3aa7", "#eda100", "#008300", "#e34948"
)
OTHER_COLOR = "#b4b2a9"
_SPRINT_MARKS = {True: "✓", False: "✗", None: "?"}
_TITLES = {
    LifeChart.FEELING: "Feeling",
    LifeChart.ACTIONS: "Actions",
    LifeChart.EFFORT: "Effort Points",
    LifeChart.SPRINTS: "Sprints",
    LifeChart.CATEGORY: "Categories",
    LifeChart.ENERGY: "Energy",
    LifeChart.VALUE: "Values",
}
_ONE = {LifeChart.CATEGORY: "Category", LifeChart.ENERGY: "Energy", LifeChart.VALUE: "Value"}
_MISSING = {
    LifeChart.CATEGORY: "no Category",
    LifeChart.ENERGY: "no Energy type",
    LifeChart.VALUE: "no Value",
}


@dataclass(frozen=True, slots=True)
class Paint:
    """One week as drawn: its colour, the number the close-up writes in it, or the shares
    the close-up stacks in it instead."""

    color: str
    label: str = ""
    mix: tuple[tuple[str, float], ...] = ()


@dataclass(frozen=True, slots=True)
class Scale:
    """A legend running through these colours from `low` to `high`."""

    stops: tuple[str, ...]
    low: str
    high: str
    caption: str


@dataclass(frozen=True, slots=True)
class Picture:
    title: str
    stats: tuple[str, ...]
    paints: Mapping[Cell, Paint]
    # What a pale week since the records began lacked.
    missing: str
    # What the close-up's squares say.
    note: str
    scale: Scale | None = None
    swatches: tuple[tuple[str, str], ...] = ()
    best: Cell | None = None


def _rgb(color: str) -> tuple[int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def blend(stops: Sequence[str], share: float) -> str:
    """The colour `share` of the way through `stops`."""
    share = min(max(share, 0.0), 1.0) * (len(stops) - 1)
    index = min(int(share), len(stops) - 2)
    low, high = _rgb(stops[index]), _rgb(stops[index + 1])
    part = share - index
    return "#" + "".join(f"{round(a + (b - a) * part):02x}" for a, b in zip(low, high, strict=True))


def tint(color: str, share: float) -> str:
    """`color` as deep as `share`, never quite the paper."""
    return blend((SURFACE, color), 0.15 + 0.85 * share)


def _many(count: int, word: str) -> str:
    return f"{count} {word}{'s' * (count != 1)}"


def _top(values: Mapping[Cell, float]) -> Cell:
    """The week with the most; the later one of two that tie."""
    return max(values, key=lambda cell: (values[cell], ordinal(cell)))


def _longest_run(flags: Sequence[bool]) -> int:
    longest = run = 0
    for flag in flags:
        run = run + 1 if flag else 0
        longest = max(longest, run)
    return longest


@dataclass(frozen=True, slots=True)
class _Weeks:
    """The weeks since the records began, up to this one."""

    grid: LifeGrid
    today: date
    floor: date
    recorded: tuple[Cell, ...]

    @classmethod
    def of_records(cls, grid: LifeGrid, records: LifeRecords) -> _Weeks:
        floor = max(records.since, grid.born)
        return cls(
            grid, records.today, floor,
            tuple(cells(grid.cell(floor), grid.cell(records.today))),
        )

    def week(self, day: date) -> Cell | None:
        return self.grid.cell(day) if self.floor <= day <= self.today else None

    def label(self, cell: Cell) -> str:
        return day_label(self.grid.start(cell), self.today)

    def trend(self, value: Callable[[Sequence[Cell]], float | None]) -> tuple[float, float] | None:
        """`value` over the last weeks, and over as many before them."""
        last = self.recorded[-LIFE_TREND_WEEKS:]
        before = self.recorded[-2 * LIFE_TREND_WEEKS : -LIFE_TREND_WEEKS]
        now, then = value(last), value(before) if before else None
        return None if now is None or then is None else (now, then)


def offered(records: LifeRecords) -> list[LifeChart]:
    """The pictures that have something recorded to colour the weeks with."""
    actions = records.actions
    present = {
        LifeChart.FEELING: bool(records.feelings),
        LifeChart.ACTIONS: bool(actions),
        LifeChart.EFFORT: records.effort_tracking
        and any(action.effort is not None for action in actions),
        LifeChart.SPRINTS: bool(records.sprints),
        LifeChart.CATEGORY: any(action.categories for action in actions),
        LifeChart.ENERGY: any(action.energy_types for action in actions),
        LifeChart.VALUE: any(action.values for action in actions),
    }
    return [chart for chart in LifeChart if present[chart]]


def _carried(chart: LifeChart, action: LifeAction) -> frozenset[str]:
    if chart is LifeChart.CATEGORY:
        return action.categories
    if chart is LifeChart.ENERGY:
        return action.energy_types
    return action.values


def _served(records: LifeRecords) -> Counter[str]:
    return Counter(name for action in records.actions for name in action.values)


def _order(chart: LifeChart, records: LifeRecords) -> tuple[str, ...]:
    """Every one the picture can show, in the order it stacks and lists them."""
    if chart is LifeChart.CATEGORY:
        return tuple(CATEGORY_COLORS)
    if chart is LifeChart.ENERGY:
        return tuple(ENERGY_COLORS)
    served = _served(records)
    return tuple(sorted(served, key=lambda name: (-served[name], name.casefold())))


def focuses(chart: LifeChart, records: LifeRecords) -> tuple[str, ...]:
    """What the picture offers to show one at a time: those the finished Actions carried."""
    carried = {name for action in records.actions for name in _carried(chart, action)}
    shown = tuple(name for name in _order(chart, records) if name in carried)
    return shown[:LIFE_VALUES_SHOWN] if chart is LifeChart.VALUE else shown


def name_of(chart: LifeChart, key: str) -> str:
    return key if chart is LifeChart.VALUE else key.capitalize()


def _colors(chart: LifeChart, order: Sequence[str]) -> dict[str, str]:
    if chart is LifeChart.CATEGORY:
        return dict(CATEGORY_COLORS)
    if chart is LifeChart.ENERGY:
        return dict(ENERGY_COLORS)
    return {
        name: VALUE_COLORS[index] if index < LIFE_VALUES_SHOWN else OTHER_COLOR
        for index, name in enumerate(order)
    }


def picture(
    chart: LifeChart, records: LifeRecords, grid: LifeGrid, focus: str | None = None
) -> Picture:
    """What `chart` paints, and of the pictures by what the Actions carried, `focus` alone."""
    if chart not in offered(records):
        raise DomainError(f"Nothing is recorded yet to draw {_TITLES[chart]} with.")
    weeks = _Weeks.of_records(grid, records)
    if chart is LifeChart.FEELING:
        return _feeling(weeks, records)
    if chart is LifeChart.ACTIONS:
        return _actions(weeks, records)
    if chart is LifeChart.EFFORT:
        return _effort(weeks, records)
    if chart is LifeChart.SPRINTS:
        return _sprints(weeks, records)
    return _group(chart, weeks, records, focus)


def _feeling(weeks: _Weeks, records: LifeRecords) -> Picture:
    scores: dict[Cell, list[int]] = defaultdict(list)
    for day, score in records.feelings.items():
        if (cell := weeks.week(day)) is not None:
            scores[cell].append(score)
    means = {cell: sum(day) / len(day) for cell, day in scores.items()}
    if not means:
        raise DomainError("No feeling was written in the Diary since the records began.")
    best = _top(means)
    lowest = min(means, key=lambda cell: (means[cell], ordinal(cell)))
    stats = [
        f"Mean {sum(means.values()) / len(means):.1f} over {_many(len(means), 'week')} · "
        f"best {means[best]:.1f}, week of {weeks.label(best)} · "
        f"lowest {means[lowest]:.1f}, week of {weeks.label(lowest)}"
    ]

    def mean(window: Sequence[Cell]) -> float | None:
        known = [means[cell] for cell in window if cell in means]
        return sum(known) / len(known) if known else None

    if (trend := weeks.trend(mean)) is not None:
        stats.append(
            f"Last {LIFE_TREND_WEEKS} weeks {trend[0]:.1f}, "
            f"the {LIFE_TREND_WEEKS} before {trend[1]:.1f}"
        )
    return Picture(
        title=_TITLES[LifeChart.FEELING],
        stats=tuple(stats),
        paints={
            cell: Paint(blend(FEELING_STOPS, value / 10), f"{value:.0f}")
            for cell, value in means.items()
        },
        missing="no feeling in the Diary",
        note="Each square: the mean feeling the Diary wrote that week, 0 to 10",
        scale=Scale(FEELING_STOPS, "0", "10", "feeling"),
        best=best,
    )


def _actions(weeks: _Weeks, records: LifeRecords) -> Picture:
    counts = Counter(
        cell for action in records.actions if (cell := weeks.week(action.day)) is not None
    )
    if not counts:
        raise DomainError("No Action was finished since the records began.")
    peak = max(counts.values())
    best = _top(counts)
    total = sum(counts.values())
    run = _longest_run([cell in counts for cell in weeks.recorded])
    return Picture(
        title=_TITLES[LifeChart.ACTIONS],
        stats=(
            f"{_many(total, 'Action')} finished · {total / len(weeks.recorded):.1f} a week · "
            f"most {peak}, week of {weeks.label(best)} · "
            f"longest run {_many(run, 'week')} with one finished",
        ),
        paints={cell: Paint(blend(ACTION_STOPS, count / peak), str(count))
                for cell, count in counts.items()},
        missing="nothing finished",
        note="Each square: how many Actions were finished that week",
        scale=Scale(ACTION_STOPS, "0", str(peak), "Actions finished"),
        best=best,
    )


def _effort(weeks: _Weeks, records: LifeRecords) -> Picture:
    sums: dict[Cell, float] = defaultdict(float)
    finished: set[Cell] = set()
    for action in records.actions:
        if (cell := weeks.week(action.day)) is None:
            continue
        finished.add(cell)
        if action.effort is not None:
            sums[cell] += action.effort
    if not sums:
        raise DomainError("No finished Action carries Effort Points since the records began.")
    peak = max(sums.values())
    best = _top(sums)
    total = sum(sums.values())
    line = (
        f"{total:g} EP finished · {total / len(weeks.recorded):.1f} a week · "
        f"most {peak:g} EP, week of {weeks.label(best)}"
    )
    if unestimated := len(finished - sums.keys()):
        line += f" · {_many(unestimated, 'week')} with finished Actions and no estimate"
    return Picture(
        title=_TITLES[LifeChart.EFFORT],
        stats=(line,),
        paints={cell: Paint(blend(EFFORT_STOPS, value / peak), f"{value:g}")
                for cell, value in sums.items()},
        missing="no Effort Points",
        note="Each square: the Effort Points finished that week",
        scale=Scale(EFFORT_STOPS, "0", f"{peak:g} EP", "Effort Points finished"),
        best=best,
    )


def _sprints(weeks: _Weeks, records: LifeRecords) -> Picture:
    days: dict[Cell, Counter[int]] = defaultdict(Counter)
    for index, sprint in enumerate(records.sprints):
        day = sprint.first
        while day <= sprint.last:
            if (cell := weeks.week(day)) is not None:
                days[cell][index] += 1
            day += timedelta(days=1)
    # A week is the Sprint's that had most of its days; of two that tie, the later one's.
    owner = {cell: max(count, key=lambda index: (count[index], index))
             for cell, count in days.items()}
    paints: dict[Cell, Paint] = {}
    marked: set[int] = set()
    for cell in sorted(owner, key=ordinal):
        index = owner[cell]
        sprint = records.sprints[index]
        color = RUNNING_COLOR if sprint.running_day else MET_COLORS[sprint.met]
        if index % 2:
            # Neighbouring Sprints alternate in depth, so where one ends shows.
            color = blend((SURFACE, color), 0.6)
        first = index not in marked
        marked.add(index)
        mark = "•" if sprint.running_day else _SPRINT_MARKS[sprint.met]
        paints[cell] = Paint(color, mark if first else "")
    ended = [sprint for sprint in records.sprints if sprint.running_day is None]
    line = (
        f"{_many(len(ended), 'Sprint')} ended · criteria met in "
        f"{sum(sprint.met is True for sprint in ended)} · longest run met "
        f"{_longest_run([sprint.met is True for sprint in ended])}"
    )
    running = next((sprint for sprint in records.sprints if sprint.running_day), None)
    if running is not None and running.running_day is not None:
        day, length = running.running_day
        line += f" · Sprint {running.number} running, day {day} of {length}"
    kinds = {
        "running" if sprint.running_day else sprint.met for sprint in records.sprints
    }
    swatches = tuple(
        (color, label)
        for kind, color, label in (
            (True, MET_COLORS[True], "criteria met"),
            (False, MET_COLORS[False], "not met"),
            (None, MET_COLORS[None], "not marked"),
            ("running", RUNNING_COLOR, "running"),
        )
        if kind in kinds
    )
    return Picture(
        title=_TITLES[LifeChart.SPRINTS],
        stats=(line,),
        paints=paints,
        missing="no Sprint",
        note="On each Sprint's first week: ✓ met, ✗ not met, ? not marked, • running",
        swatches=swatches,
    )


def _group(
    chart: LifeChart, weeks: _Weeks, records: LifeRecords, focus: str | None
) -> Picture:
    finished: Counter[Cell] = Counter()
    carried: dict[Cell, Counter[str]] = defaultdict(Counter)
    for action in records.actions:
        if (cell := weeks.week(action.day)) is None:
            continue
        finished[cell] += 1
        carried[cell].update(_carried(chart, action))
    totals: Counter[str] = Counter()
    for count in carried.values():
        totals.update(count)
    order = _order(chart, records)
    colors = _colors(chart, order)
    if focus is None:
        if not totals:
            raise DomainError(
                f"No finished Action carried any {_ONE[chart]} since the records began."
            )
        return _mix(chart, weeks, records, carried, totals, order, colors)
    if chart is LifeChart.VALUE:
        key = next((name for name in records.values if name.casefold() == focus.casefold()), None)
    else:
        key = focus if focus in order else None
    if key is None:
        raise DomainError(f"No {_ONE[chart]} is called {focus}.")
    return _share(chart, weeks, finished, carried, key, colors.get(key, VALUE_COLORS[0]))


def _mix(
    chart: LifeChart,
    weeks: _Weeks,
    records: LifeRecords,
    carried: Mapping[Cell, Counter[str]],
    totals: Counter[str],
    order: Sequence[str],
    colors: Mapping[str, str],
) -> Picture:
    paints: dict[Cell, Paint] = {}
    led: Counter[str] = Counter()
    for cell, count in carried.items():
        present = [name for name in order if count[name]]
        if not present:
            continue
        # Of two that tie, the one listed first leads.
        lead = max(present, key=lambda name: (count[name], -order.index(name)))
        led[lead] += 1
        whole = sum(count.values())
        paints[cell] = Paint(
            colors[lead], mix=tuple((colors[name], count[name] / whole) for name in present)
        )
    finished = sum(1 for action in records.actions if weeks.week(action.day) is not None)
    lead = max(order, key=lambda name: (totals[name], -order.index(name)))

    def share(name: str) -> str:
        return f"{_many(totals[name], 'Action')}, {round(100 * totals[name] / finished)}%"

    if chart is LifeChart.VALUE:
        stats = [f"{lead} served most: {share(lead)} of all finished"]
        now = ordinal(weeks.recorded[-1])
        last = {
            name: max(ordinal(cell) for cell, count in carried.items() if count[name])
            for name in totals
        }
        stale = min(last, key=lambda name: (last[name], name.casefold()))
        notes = []
        if ago := now - last[stale]:
            notes.append(f"{stale}: last served {_many(ago, 'week')} ago")
        if never := sum(1 for name in records.values if name not in totals):
            notes.append(f"{_many(never, 'Value')} never served")
        stats += [" · ".join(notes)] if notes else []
    else:
        least = min(order, key=lambda name: (totals[name], order.index(name)))
        stats = [
            f"{name_of(chart, lead)} leads: {share(lead)} · "
            f"{name_of(chart, least)} least: {share(least)}",
            "Weeks led: " + " · ".join(
                f"{name_of(chart, name)} {weeks_led}" for name, weeks_led in led.most_common()
            ),
        ]
    swatches = [
        (colors[name], name_of(chart, name))
        for name in order[:LIFE_VALUES_SHOWN]
        if totals[name]
    ]
    if len(order) > LIFE_VALUES_SHOWN:
        swatches.append((OTHER_COLOR, "other Values"))
    return Picture(
        title=_TITLES[chart],
        stats=tuple(stats),
        paints=paints,
        missing=_MISSING[chart],
        note="Each square: the mix of what that week's finished Actions carried",
        swatches=tuple(swatches),
    )


def _share(
    chart: LifeChart,
    weeks: _Weeks,
    finished: Counter[Cell],
    carried: Mapping[Cell, Counter[str]],
    key: str,
    color: str,
) -> Picture:
    shares = {cell: carried[cell][key] / count for cell, count in finished.items() if count}
    name = name_of(chart, key)
    count = sum(carried[cell][key] for cell in finished)
    picture = Picture(
        title=f"{_ONE[chart]}: {name}",
        stats=(f"{name}: no finished Action carried it yet",),
        paints={cell: Paint(tint(color, share), f"{round(100 * share)}")
                for cell, share in shares.items()},
        missing="nothing finished",
        note=f"Each square: the % of that week's finished Actions that carried {name}",
        scale=Scale((tint(color, 0), color), "0%", "100%", f"share that carried {name}"),
    )
    if count == 0:
        return picture
    best = _top({cell: share for cell, share in shares.items() if share})
    if chart is LifeChart.VALUE:
        served = [carried.get(cell, Counter())[key] > 0 for cell in weeks.recorded]
        ago = len(served) - 1 - max(index for index, flag in enumerate(served) if flag)
        stats = [
            f"{name}: {_many(count, 'Action')} over {_many(sum(served), 'week')} · "
            f"highest {round(100 * shares[best])}%, week of {weeks.label(best)}",
            ("Served this week" if not ago else f"Last served {_many(ago, 'week')} ago")
            + f" · longest gap {_many(_longest_run([not flag for flag in served]), 'week')}",
        ]
    else:
        absent = sum(1 for share in shares.values() if not share)
        stats = [
            f"{name}: {_many(count, 'Action')}, "
            f"{round(100 * count / sum(finished.values()))}% of all · "
            f"highest {round(100 * shares[best])}%, week of {weeks.label(best)} · "
            f"absent {absent} of {_many(len(shares), 'week')}"
        ]

        def window(cells_in: Sequence[Cell]) -> float | None:
            whole = sum(finished[cell] for cell in cells_in)
            return sum(carried.get(cell, Counter())[key] for cell in cells_in) / whole if whole else None

        if (trend := weeks.trend(window)) is not None:
            stats.append(
                f"Last {LIFE_TREND_WEEKS} weeks {round(100 * trend[0])}%, "
                f"the {LIFE_TREND_WEEKS} before {round(100 * trend[1])}%"
            )
    return replace(picture, stats=tuple(stats), best=best)
