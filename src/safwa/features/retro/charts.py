"""The retro charts: pictures of what the chosen ended Sprints added up to.

Every number on them is read off the Sprints' records as they were written when each one
ended (RT-STATS-003); nothing here reads the database or Telegram, so a picture is drawn in a
worker thread. A chart is drawn only when the chosen Sprints carry what it shows
(RT-CHART-019), and a Category or an Energy type wears the same emoji and colour on every
one (RT-CHART-020). The emoji are Noto Emoji pictures kept beside this module: matplotlib
draws no colour font, and text the owner wrote is drawn without what its font lacks.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from functools import cache
from itertools import accumulate
from math import ceil
from pathlib import Path
from typing import Any

from matplotlib.axes import Axes
from matplotlib.figure import Figure
from matplotlib.image import imread
from matplotlib.lines import Line2D
from matplotlib.offsetbox import AnnotationBbox, OffsetImage
from matplotlib.patches import Patch, PathPatch, Rectangle
from matplotlib.path import Path as Curve
from matplotlib.ticker import MaxNLocator, PercentFormatter
from matplotlib.transforms import blended_transform_factory

from ...constants import WEEKDAY_NAMES
from ...foundation.charts import (
    CHART_DPI,
    GRID,
    INK,
    INK_2,
    MUTED,
    SURFACE,
    day_month,
    drawable,
    png,
)
from ..cards.api import minutes_label
from ..cards.model import Category
from ..cards.telegram import CATEGORY_COLORS, CATEGORY_EMOJIS, ENERGY_COLORS, ENERGY_EMOJIS
from ..planning.closing import NONE_BUCKET, Bucket, RetroStatistics
from ..planning.model import Sprint

# One picture as Telegram shows a photo: 1280 by 800 pixels.
CHART_SIZE = (8.0, 5.0)
# Up to this many Sprints a chart writes each one's numbers on it; the Retro list's charts
# are of this many, the newest.
CHART_SPRINTS_READABLE = 12
# How many Check series one chart lists, the most answered first.
CHECKS_SHOWN = 8
# Below a chart of what was taken, when a Sprint did not know how often an Action repeats.
LOWER_BOUNDS = "Schedule quantities are unknown; planned totals are lower bounds."

_BASELINE = "#c3c2b7"
_TAKEN = "#d3d1c7"
_PLAN = "#888780"
_DONE = "#2c2c2a"
_PASSED = "#0ca30c"
_MISSED = "#d03b3b"
# An Action with no Category or no Energy type.
NONE_COLOR = "#b4b2a9"

# The colour of each Category and Energy type as Cards gives it, and of none.
_CATEGORY_FILLS = {**CATEGORY_COLORS, NONE_BUCKET: NONE_COLOR}
_ENERGY_FILLS = {**ENERGY_COLORS, NONE_BUCKET: NONE_COLOR}
# Fills too light for white words on them.
_LIGHT_FILLS = frozenset(
    {
        CATEGORY_COLORS[Category.SELF.value],
        CATEGORY_COLORS[Category.WORK.value],
        CATEGORY_COLORS[Category.CONTRIBUTION.value],
        NONE_COLOR,
    }
)
PASSED_EMOJI = "✅"
MISSED_EMOJI = "❌"
_EMOJI_DIR = Path(__file__).with_name("emoji")


@dataclass(frozen=True, slots=True)
class ChartSprint:
    """One ended Sprint as the charts read it: its number, its record and the owner's mark."""

    number: str
    statistics: RetroStatistics
    met: bool | None
    capacity: float | None

    @classmethod
    def of(cls, sprint: Sprint) -> ChartSprint:
        return cls(
            sprint.number,
            RetroStatistics.from_record(sprint.retro),
            sprint.criterion_met,
            sprint.capacity_effort_points,
        )


@dataclass(frozen=True, slots=True)
class ChosenSprints:
    """The chosen Sprints, the oldest first, and what their charts count in."""

    sprints: tuple[ChartSprint, ...]
    effort_tracking: bool

    @property
    def in_points(self) -> bool:
        return counts_effort(self.sprints, effort_tracking=self.effort_tracking)

    @property
    def unit(self) -> str:
        return "Effort Points" if self.in_points else "Actions"

    @property
    def lower_bounds(self) -> bool:
        return any(s.statistics.unknown_schedules for s in self.sprints)

    @property
    def header(self) -> str:
        return covered(self.sprints)


def counts_effort(sprints: Sequence[ChartSprint], *, effort_tracking: bool) -> bool:
    """Effort Points are counted when they are on and every Sprint was estimated in full and
    knew its Schedule quantities; Actions otherwise."""
    return effort_tracking and not any(
        s.statistics.unestimated or s.statistics.unknown_schedules for s in sprints
    )


def check_totals(
    sprints: Sequence[ChartSprint],
) -> list[tuple[tuple[str, tuple[str, ...]], tuple[int, int]]]:
    """Each Check series by its title and Values, Passed and Missed added up over the
    Sprints, the most answered first."""
    totals: dict[tuple[str, tuple[str, ...]], tuple[int, int]] = {}
    for sprint in sprints:
        for tally in sprint.statistics.series:
            passed, missed = totals.get((tally.title, tally.values), (0, 0))
            totals[(tally.title, tally.values)] = (passed + tally.passed, missed + tally.missed)
    return sorted(totals.items(), key=lambda row: -sum(row[1]))


def covered(sprints: Sequence[ChartSprint]) -> str:
    """The Sprints a chart covers, the oldest first, and the days from the first one's start
    to the last one's end."""
    first, last = sprints[0], sprints[-1]
    period = _period(first.statistics.first_day, last.statistics.last_day)
    if len(sprints) == 1:
        return f"Sprint {first.number} · {period}"
    return f"{len(sprints)} Sprints, {first.number} to {last.number} · {period}"


def render_charts(
    sprints: Sequence[ChartSprint], *, effort_tracking: bool
) -> list[tuple[str, bytes]]:
    """Each chart the chosen Sprints carry, by name and as PNG, in the album's order."""
    chosen = ChosenSprints(tuple(sprints), effort_tracking)
    pictures = []
    for name, draw in CHARTS:
        figure = draw(chosen)
        if figure is not None:
            pictures.append((name, png(figure)))
    return pictures


def emoji_file(emoji: str) -> Path:
    """The Noto Emoji picture of this emoji."""
    return _EMOJI_DIR / ("emoji_u" + "_".join(f"{ord(ch):x}" for ch in emoji if ch != "️") + ".png")


# ------------------------------------------------------------------------------- drawing


def _period(first: date, last: date) -> str:
    if first.year == last.year:
        return f"{day_month(first)} – {day_month(last)} {last.year}"
    return f"{day_month(first)} {first.year} – {day_month(last)} {last.year}"


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _amount(value: float) -> str:
    return f"{round(value, 1):g}"


def _name(bucket: str) -> str:
    return bucket.capitalize()


def _figure(title: str, chosen: ChosenSprints, note: str = "", *, taken: bool = False) -> Figure:
    """A blank picture headed by its title and the Sprints and days it covers. One that shows
    what was taken says below it when that is only a lower bound."""
    figure = Figure(figsize=CHART_SIZE, dpi=CHART_DPI, facecolor=SURFACE)
    figure.text(0.045, 0.925, title, fontsize=16, fontweight="bold", color=INK)
    figure.text(0.045, 0.87, chosen.header, fontsize=10.5, color=MUTED)
    notes = [note] if note else []
    if taken and chosen.lower_bounds:
        notes.append(LOWER_BOUNDS)
    if notes:
        figure.text(0.045, 0.03, "\n".join(notes), fontsize=9, color=INK_2, va="bottom")
    return figure


def _axes(
    figure: Figure, rect: tuple[float, float, float, float] = (0.08, 0.13, 0.88, 0.62)
) -> Axes:
    axes = figure.add_axes(rect)
    axes.set_facecolor(SURFACE)
    for side in ("top", "right", "left"):
        axes.spines[side].set_visible(False)
    axes.spines["bottom"].set_color(_BASELINE)
    axes.tick_params(colors=MUTED, labelsize=9.5, length=0)
    axes.yaxis.grid(True, color=GRID, linewidth=0.8)
    axes.set_axisbelow(True)
    axes.yaxis.set_major_locator(MaxNLocator(integer=True, nbins=5))
    return axes


def _bare(axes: Axes) -> Axes:
    """Axes whose rows are labelled by hand: no ticks, no grid, no baseline."""
    axes.set_xticks([])
    axes.set_yticks([])
    axes.yaxis.grid(False)
    axes.spines["bottom"].set_visible(False)
    return axes


@cache
def _emoji_image(emoji: str) -> Any:
    return imread(emoji_file(emoji))


def _icon(axes: Axes, emoji: str, xy: tuple[float, float], *, xycoords: Any = "data",
          offset: tuple[float, float] = (0, 0)) -> None:
    axes.add_artist(
        AnnotationBbox(
            OffsetImage(_emoji_image(emoji), zoom=0.2),
            xy,
            xycoords=xycoords,
            xybox=offset,
            boxcoords="offset points",
            frameon=False,
            annotation_clip=False,
        )
    )


def _row_label(axes: Axes, y: float, text: str, emoji: str | None = None) -> None:
    """A row's name in the column left of the axes, its emoji first. A row of a list whose
    other rows carry one, "" for it, keeps the emoji's place empty."""
    where = blended_transform_factory(axes.transAxes, axes.transData)
    if emoji:
        _icon(axes, emoji, (0, y), xycoords=where, offset=(-122, 0))
    axes.annotate(
        text,
        (0, y),
        xycoords=where,
        xytext=(-108 if emoji is not None else -126, 0),
        textcoords="offset points",
        ha="left",
        va="center",
        fontsize=10.5,
        color=INK_2,
    )


def _legend(axes: Axes, handles: list[Any]) -> None:
    axes.legend(
        handles=handles,
        loc="lower left",
        bbox_to_anchor=(0, 1.01),
        ncol=len(handles),
        frameon=False,
        fontsize=9,
        labelcolor=INK_2,
        handlelength=1.6,
    )


def _sprint_ticks(axes: Axes, numbers: Sequence[str], positions: Sequence[float]) -> None:
    step = ceil(len(numbers) / 8)
    axes.set_xticks(list(positions)[::step], list(numbers)[::step])


def _amounts(buckets: Sequence[dict[str, Bucket]], order: Sequence[str], in_points: bool,
             ) -> dict[str, tuple[float, float]]:
    """Each bucket's taken and finished amount, added up over the Sprints, in its order."""
    totals = {}
    for name in order:
        held = [bucket[name] for bucket in buckets if name in bucket]
        taken = sum(b.effort if in_points else b.count for b in held)
        finished = sum(b.done_effort if in_points else b.done_count for b in held)
        if taken:
            totals[name] = (taken, finished)
    return totals


# -------------------------------------------------------------------------------- charts


def _burnup(chosen: ChosenSprints) -> Figure:
    figure = _figure("Burn-up: Actions finished day by day", chosen, taken=True)
    axes = _axes(figure)
    offset, top, centres = 0, 1, []
    for sprint in chosen.sprints:
        days, taken = sprint.statistics.days, sprint.statistics.planned
        left, right = offset - 0.5, offset + len(days) - 0.5
        xs = [offset + index for index in range(len(days))]
        finished = list(accumulate(day.done for day in days))
        if offset:
            axes.axvline(left, color=_BASELINE, linewidth=0.8, linestyle=(0, (2, 3)))
        axes.plot([left, right], [0, taken], color=MUTED, linewidth=1, linestyle=(0, (1, 3)))
        axes.hlines(taken, left, right, color=_PLAN, linewidth=1.2, linestyle=(0, (5, 3)))
        axes.bar(xs, [day.done for day in days], width=0.6, color=_TAKEN, linewidth=0)
        axes.plot([left, *xs], [0, *finished], color=_DONE, linewidth=2.2, solid_joinstyle="round")
        axes.plot(xs[-1], finished[-1], "o", color=_DONE, markersize=6,
                  markeredgecolor=SURFACE, markeredgewidth=1.5)
        if len(chosen.sprints) <= CHART_SPRINTS_READABLE:
            at_least = "≥" if sprint.statistics.unknown_schedules else ""
            axes.annotate(f"{finished[-1]}/{at_least}{taken}", (xs[-1], finished[-1]), xytext=(-4, 8),
                          textcoords="offset points", ha="right", fontsize=10,
                          fontweight="bold", color=INK)
        top = max(top, taken, finished[-1])
        centres.append((left + right) / 2)
        offset += len(days)
    axes.set_xlim(-0.5, offset - 0.5)
    axes.set_ylim(0, top * 1.18)
    if len(chosen.sprints) == 1:
        days = [date.fromisoformat(day.day) for day in chosen.sprints[0].statistics.days]
        step = ceil(len(days) / 14)
        axes.set_xticks(
            range(0, len(days), step),
            [day_month(day) if index == 0 or day.day == 1 else str(day.day)
             for index, day in enumerate(days)][::step],
        )
    else:
        _sprint_ticks(axes, [s.number for s in chosen.sprints], centres)
    _legend(axes, [
        Line2D([], [], color=_DONE, linewidth=2.2, label="finished"),
        Patch(color=_TAKEN, label="finished that day"),
        Line2D([], [], color=_PLAN, linestyle=(0, (5, 3)), label="taken"),
        Line2D([], [], color=MUTED, linestyle=(0, (1, 3)), label="steady pace"),
    ])
    return figure


def _velocity(chosen: ChosenSprints) -> Figure | None:
    sprints = chosen.sprints
    if len(sprints) < 2:
        return None
    figure = _figure("Velocity: Actions taken and finished", chosen,
                     "Above each Sprint: whether its Success criteria were met.", taken=True)
    axes = _axes(figure)
    xs = range(len(sprints))
    taken = [s.statistics.planned for s in sprints]
    finished = [s.statistics.finished for s in sprints]
    width = 0.36
    axes.bar([x - width / 2 for x in xs], taken, width, color=_TAKEN, linewidth=0)
    axes.bar([x + width / 2 for x in xs], finished, width, color=_DONE, linewidth=0)
    top = max(max(taken), 1)
    for x, sprint, done in zip(xs, sprints, finished, strict=True):
        if len(sprints) <= CHART_SPRINTS_READABLE:
            axes.annotate(str(done), (x + width / 2, done), xytext=(0, 3),
                          textcoords="offset points", ha="center", fontsize=9, color=INK)
        height = max(sprint.statistics.planned, done)
        if sprint.met is None:
            axes.annotate("—", (x, height), xytext=(0, 14), textcoords="offset points",
                          ha="center", va="center", fontsize=12, color=MUTED)
        else:
            _icon(axes, PASSED_EMOJI if sprint.met else MISSED_EMOJI, (x, height), offset=(0, 14))
    mean = sum(finished) / len(finished)
    axes.axhline(mean, color=INK_2, linewidth=1, linestyle=(0, (5, 3)))
    axes.annotate(f"mean {mean:.1f}", (1, mean), xycoords=("axes fraction", "data"),
                  xytext=(0, 4), textcoords="offset points", ha="right", fontsize=9.5,
                  color=INK_2)
    axes.set_ylim(0, top * 1.3)
    _sprint_ticks(axes, [s.number for s in sprints], xs)
    _legend(axes, [
        Patch(color=_TAKEN, label="taken"),
        Patch(color=_DONE, label="finished"),
        Line2D([], [], color=INK_2, linestyle=(0, (5, 3)), label="mean finished"),
    ])
    return figure


def _capacity(chosen: ChosenSprints) -> Figure | None:
    if not chosen.effort_tracking or len(chosen.sprints) < 2:
        return None
    estimated = [
        s for s in chosen.sprints
        if not (s.statistics.unestimated or s.statistics.unknown_schedules)
    ]
    if not estimated:
        return None
    left_out = len(chosen.sprints) - len(estimated)
    note = (
        f"{left_out} of {len(chosen.sprints)} Sprints are left out: an estimate or a Schedule "
        "quantity was unknown."
        if left_out
        else ""
    )
    figure = _figure("Effort Points: plan, added, finished, capacity", chosen, note)
    axes = _axes(figure)
    width = 0.36
    top = 1.0
    for x, sprint in enumerate(estimated):
        statistics = sprint.statistics
        axes.bar(x - width / 2, statistics.initial, width, color=_PLAN, linewidth=0)
        axes.bar(x - width / 2, statistics.added, width, bottom=statistics.initial,
                 color=_TAKEN, linewidth=0)
        axes.bar(x + width / 2, statistics.done, width, color=_DONE, linewidth=0)
        if statistics.added:
            axes.annotate(f"+{_amount(statistics.added)}", (x - width / 2, statistics.taken),
                          xytext=(0, 3), textcoords="offset points", ha="center",
                          fontsize=9, color=INK_2)
        axes.annotate(_amount(statistics.done), (x + width / 2, statistics.done), xytext=(0, 3),
                      textcoords="offset points", ha="center", fontsize=9, color=INK)
        if sprint.capacity is not None:
            axes.hlines(sprint.capacity, x - 0.45, x + 0.45, color=_MISSED, linewidth=2.4)
        top = max(top, statistics.taken, statistics.done, sprint.capacity or 0)
    axes.set_ylim(0, top * 1.2)
    _sprint_ticks(axes, [s.number for s in estimated], range(len(estimated)))
    _legend(axes, [
        Patch(color=_PLAN, label="initial plan"),
        Patch(color=_TAKEN, label="added"),
        Patch(color=_DONE, label="finished"),
        Line2D([], [], color=_MISSED, linewidth=2.4, label="capacity"),
    ])
    return figure


def _taken_and_finished(chosen: ChosenSprints, kind: str, colors: dict[str, str],
                        emojis: dict[str, str], buckets: Callable[[RetroStatistics], dict[str, Bucket]],
                        ) -> Figure | None:
    totals = _amounts([buckets(s.statistics) for s in chosen.sprints], list(colors),
                      chosen.in_points)
    if not totals:
        return None
    figure = _figure(f"{kind}: {chosen.unit} taken and finished", chosen, taken=True)
    axes = _bare(_axes(figure, (0.25, 0.08, 0.7, 0.7)))
    widest = max(taken for taken, _ in totals.values())
    for y, (name, (taken, finished)) in enumerate(totals.items()):
        color = colors[name]
        axes.barh(y, taken, height=0.56, color=color, alpha=0.25, linewidth=0)
        axes.barh(y, finished, height=0.56, color=color, linewidth=0)
        label = (
            f"{_amount(finished)} of at least {_amount(taken)}"
            if chosen.lower_bounds
            else f"{_amount(finished)} of {_amount(taken)} · {round(100 * finished / taken)}%"
        )
        axes.annotate(
            label, (taken, y), xytext=(6, 0), textcoords="offset points", va="center",
            fontsize=10, color=INK,
        )
        _row_label(axes, y, _name(name), emojis.get(name, ""))
    axes.set_xlim(0, widest * 1.4)
    axes.set_ylim(len(totals) - 0.5, -0.5)
    return figure


def _category(chosen: ChosenSprints) -> Figure | None:
    return _taken_and_finished(chosen, "Category", _CATEGORY_FILLS, CATEGORY_EMOJIS,
                               lambda statistics: statistics.by_category)


def _energy(chosen: ChosenSprints) -> Figure | None:
    return _taken_and_finished(chosen, "Energy type", _ENERGY_FILLS, ENERGY_EMOJIS,
                               lambda statistics: statistics.by_energy)


def _mix(chosen: ChosenSprints) -> Figure | None:
    sprints = chosen.sprints
    if len(sprints) < 2:
        return None
    figure = _figure(f"Category mix of finished {chosen.unit}", chosen)
    axes = _axes(figure, (0.08, 0.13, 0.7, 0.62))
    shown: set[str] = set()
    for x, sprint in enumerate(sprints):
        totals = _amounts([sprint.statistics.by_category], list(_CATEGORY_FILLS),
                          chosen.in_points)
        whole = sum(finished for _, finished in totals.values())
        if not whole:
            axes.annotate("0", (x, 0), xytext=(0, 4), textcoords="offset points",
                          ha="center", fontsize=9, color=MUTED)
            continue
        bottom = 0.0
        for name, (_, finished) in totals.items():
            share = finished / whole
            if not share:
                continue
            color = _CATEGORY_FILLS[name]
            axes.bar(x, share, 0.62, bottom=bottom, color=color, edgecolor=SURFACE,
                     linewidth=1.5)
            if share >= 0.1 and len(sprints) <= CHART_SPRINTS_READABLE:
                axes.text(x, bottom + share / 2, f"{round(100 * share)}%", ha="center",
                          va="center", fontsize=9,
                          color=INK if color in _LIGHT_FILLS else "#ffffff")
            bottom += share
            shown.add(name)
    axes.set_ylim(0, 1)
    axes.yaxis.set_major_formatter(PercentFormatter(1, decimals=0))
    axes.set_yticks([0, 0.5, 1])
    _sprint_ticks(axes, [s.number for s in sprints], range(len(sprints)))
    for index, name in enumerate(name for name in _CATEGORY_FILLS if name in shown):
        y = 0.7 - index * 0.075
        figure.patches.append(
            Rectangle((0.8, y - 0.016), 0.018, 0.032, transform=figure.transFigure,
                      color=_CATEGORY_FILLS[name], figure=figure)
        )
        if name in CATEGORY_EMOJIS:
            _icon(axes, CATEGORY_EMOJIS[name], (0.84, y), xycoords="figure fraction")
        figure.text(0.86, y, _name(name), va="center", fontsize=10.5, color=INK_2)
    return figure


def _flow(chosen: ChosenSprints) -> Figure | None:
    """Category on the left, Energy type on the right, a band for each pair finished."""
    kept = [s for s in chosen.sprints if s.statistics.by_category_energy]
    if not kept:
        return None
    pairs = {
        category: {
            energy: sum(
                (b.done_effort if chosen.in_points else b.done_count)
                for s in kept
                if (b := s.statistics.by_category_energy.get(category, {}).get(energy))
            )
            for energy in _ENERGY_FILLS
        }
        for category in _CATEGORY_FILLS
    }
    lefts = {c: sum(row.values()) for c, row in pairs.items() if sum(row.values())}
    rights = {e: sum(pairs[c][e] for c in pairs) for e in _ENERGY_FILLS}
    rights = {e: total for e, total in rights.items() if total}
    whole = sum(lefts.values())
    if not whole:
        return None
    note = (
        f"Over {len(kept)} of {len(chosen.sprints)} Sprints: the others ended before this "
        "was kept. "
        if len(kept) < len(chosen.sprints)
        else ""
    ) + "An Action with two Categories or Energy types is in each."
    figure = _figure(f"Category → Energy type: finished {chosen.unit}", chosen, note)
    axes = figure.add_axes((0.04, 0.09, 0.92, 0.72))
    axes.set_axis_off()
    gap = 0.045 * whole
    height = whole + gap * (max(len(lefts), len(rights)) - 1)
    axes.set_xlim(0, 1)
    axes.set_ylim(height, 0)

    def stack(totals: dict[str, float]) -> dict[str, float]:
        y = (height - whole - gap * (len(totals) - 1)) / 2
        tops = {}
        for name, total in totals.items():
            tops[name] = y
            y += total + gap
        return tops

    left_tops, right_tops = stack(lefts), stack(rights)
    x0, x1 = 0.315, 0.685
    cursor = dict(right_tops)
    for category in lefts:
        y = left_tops[category]
        for energy in rights:
            amount = pairs[category][energy]
            if not amount:
                continue
            y1 = cursor[energy]
            middle = (x0 + x1) / 2
            band = Curve(
                [(x0, y), (middle, y), (middle, y1), (x1, y1), (x1, y1 + amount),
                 (middle, y1 + amount), (middle, y + amount), (x0, y + amount), (x0, y)],
                [Curve.MOVETO, Curve.CURVE4, Curve.CURVE4, Curve.CURVE4, Curve.LINETO,
                 Curve.CURVE4, Curve.CURVE4, Curve.CURVE4, Curve.CLOSEPOLY],
            )
            axes.add_patch(PathPatch(band, facecolor=_CATEGORY_FILLS[category], alpha=0.45,
                                     linewidth=0))
            y += amount
            cursor[energy] += amount
    for name, total in lefts.items():
        top = left_tops[name]
        axes.add_patch(_node(x0 - 0.015, top, total, _CATEGORY_FILLS[name]))
        middle = top + total / 2
        if name in CATEGORY_EMOJIS:
            _icon(axes, CATEGORY_EMOJIS[name], (x0 - 0.04, middle))
        axes.text(x0 - 0.065, middle, f"{_name(name)}  {_amount(total)}", ha="right",
                  va="center", fontsize=10.5, color=INK_2)
    for name, total in rights.items():
        top = right_tops[name]
        axes.add_patch(_node(x1, top, total, _ENERGY_FILLS[name]))
        middle = top + total / 2
        if name in ENERGY_EMOJIS:
            _icon(axes, ENERGY_EMOJIS[name], (x1 + 0.04, middle))
        axes.text(x1 + 0.065, middle, f"{_name(name)}  {_amount(total)}", ha="left",
                  va="center", fontsize=10.5, color=INK_2)
    return figure


def _node(x: float, top: float, height: float, color: str) -> Rectangle:
    return Rectangle((x, top), 0.015, height, color=color, linewidth=0)


def _time(chosen: ChosenSprints) -> Figure | None:
    tracked = [s for s in chosen.sprints if s.statistics.time_tracking]
    minutes = sum(s.statistics.minutes for s in tracked)
    if not minutes:
        return None
    days = sum(len(s.statistics.days) for s in tracked)
    active = [len(s.statistics.days) * s.statistics.active_day_minutes for s in tracked]
    summary = f"{minutes_label(minutes)} tracked · {minutes_label(round(minutes / days))} a day"
    if all(active):
        summary += f" · {round(100 * minutes / sum(active))}% of the active day"
    note = (
        f"Over {len(tracked)} of {len(chosen.sprints)} Sprints: Time tracking was off in the "
        "others."
        if len(tracked) < len(chosen.sprints)
        else ""
    )
    figure = _figure("Time: where it went", chosen, note)
    figure.text(0.045, 0.79, summary, fontsize=12.5, fontweight="bold", color=INK)
    timed: dict[str, tuple[int, int]] = {}
    for name in _CATEGORY_FILLS:
        held = [s.statistics.by_category[name] for s in tracked if name in s.statistics.by_category]
        spent, count = sum(b.minutes for b in held), sum(b.timed_count for b in held)
        if count:
            timed[name] = (spent, count)
    rows = sorted(timed.items(), key=lambda row: -row[1][0])
    axes = _bare(_axes(figure, (0.25, 0.37, 0.7, 0.34)))
    for y, (name, (spent, count)) in enumerate(rows):
        axes.barh(y, spent, height=0.6, color=_CATEGORY_FILLS[name], linewidth=0)
        axes.annotate(f"{minutes_label(spent)} · {minutes_label(round(spent / count))} an Action",
                      (spent, y), xytext=(6, 0), textcoords="offset points", va="center",
                      fontsize=10, color=INK)
        _row_label(axes, y, _name(name), CATEGORY_EMOJIS.get(name, ""))
    axes.set_xlim(0, max(spent for spent, _ in timed.values()) * 1.6 if timed else 1)
    axes.set_ylim(max(len(rows), 1) - 0.5, -0.5)
    longest = sorted((a for s in tracked for a in s.statistics.longest),
                     key=lambda action: -action.minutes)[:3]
    if longest:
        figure.text(0.045, 0.3, "Longest Actions", fontsize=10.5, fontweight="bold",
                    color=INK_2)
        lower = _bare(_axes(figure, (0.25, 0.08, 0.7, 0.2)))
        for y, action in enumerate(longest):
            lower.barh(y, action.minutes, height=0.6, color=_TAKEN, linewidth=0)
            lower.annotate(minutes_label(action.minutes), (action.minutes, y), xytext=(6, 0),
                           textcoords="offset points", va="center", fontsize=10, color=INK)
            _row_label(lower, y, _cut(drawable(action.title), 18))
        lower.set_xlim(0, longest[0].minutes * 1.6)
        lower.set_ylim(len(longest) - 0.5, -0.5)
    return figure


def _checks(chosen: ChosenSprints) -> Figure | None:
    rows = check_totals(chosen.sprints)
    if not rows:
        return None
    more = len(rows) - CHECKS_SHOWN
    rows = rows[:CHECKS_SHOWN]
    figure = _figure("Checks tied to Values", chosen,
                     f"And {more} more Checks, answered less often." if more > 0 else "")
    axes = _bare(_axes(figure, (0.4, 0.14, 0.56, 0.64)))
    where = blended_transform_factory(axes.transAxes, axes.transData)
    widest = max(max(passed, missed) for _, (passed, missed) in rows) or 1
    for y, ((title, values), (passed, missed)) in enumerate(rows):
        axes.barh(y, -missed, height=0.56, color=_MISSED, linewidth=0)
        axes.barh(y, passed, height=0.56, color=_PASSED, linewidth=0)
        if missed:
            axes.annotate(str(missed), (-missed, y), xytext=(-5, 0), textcoords="offset points",
                          ha="right", va="center", fontsize=9.5, color=INK)
        if passed:
            axes.annotate(str(passed), (passed, y), xytext=(5, 0), textcoords="offset points",
                          va="center", fontsize=9.5, color=INK)
        for line, rise, size, color in ((title, 1, 10, INK_2), (", ".join(values), -1, 8.5, MUTED)):
            axes.annotate(_cut(drawable(line), 30), (0, y), xycoords=where, xytext=(-205, rise),
                          textcoords="offset points", ha="left",
                          va="bottom" if rise > 0 else "top", fontsize=size, color=color)
    axes.axvline(0, color=_BASELINE, linewidth=1)
    axes.set_xlim(-widest * 1.3, widest * 1.3)
    axes.set_ylim(len(rows) - 0.5, -0.5)
    _icon(axes, MISSED_EMOJI, (0.5, 0), xycoords="axes fraction", offset=(-78, -16))
    axes.annotate("Missed", (0.5, 0), xycoords="axes fraction", xytext=(-66, -16),
                  textcoords="offset points", va="center", fontsize=10, color=INK_2)
    _icon(axes, PASSED_EMOJI, (0.5, 0), xycoords="axes fraction", offset=(18, -16))
    axes.annotate("Passed", (0.5, 0), xycoords="axes fraction", xytext=(30, -16),
                  textcoords="offset points", va="center", fontsize=10, color=INK_2)
    return figure


def _week(chosen: ChosenSprints) -> Figure:
    finished, counted = [0] * 7, [0] * 7
    for sprint in chosen.sprints:
        for day in sprint.statistics.days:
            weekday = date.fromisoformat(day.day).weekday()
            finished[weekday] += day.done
            counted[weekday] += 1
    means = [done / count if count else None for done, count in zip(finished, counted, strict=True)]
    days = sum(counted)
    figure = _figure("Week rhythm: Actions finished a day", chosen,
                     f"The mean of each weekday over {days} days.")
    axes = _axes(figure)
    best = max((mean for mean in means if mean is not None), default=0)
    for x, mean in enumerate(means):
        if mean is None:
            axes.annotate("—", (x, 0), xytext=(0, 4), textcoords="offset points", ha="center",
                          fontsize=10, color=MUTED)
            continue
        top = best and mean == best
        axes.bar(x, mean, 0.62, color=_DONE if top else _TAKEN, linewidth=0)
        axes.annotate(f"{mean:.1f}", (x, mean), xytext=(0, 3), textcoords="offset points",
                      ha="center", fontsize=9.5, color=INK if top else INK_2,
                      fontweight="bold" if top else "normal")
    axes.set_xticks(range(7), WEEKDAY_NAMES)
    axes.set_ylim(0, max(best, 1) * 1.2)
    axes.yaxis.set_major_locator(MaxNLocator(nbins=5))
    return figure


# The album's order: how the Sprints went, then what they were made of, then time and Checks.
CHARTS: tuple[tuple[str, Callable[[ChosenSprints], Figure | None]], ...] = (
    ("burnup", _burnup),
    ("velocity", _velocity),
    ("capacity", _capacity),
    ("category", _category),
    ("mix", _mix),
    ("energy", _energy),
    ("category_energy", _flow),
    ("time", _time),
    ("checks", _checks),
    ("week", _week),
)
