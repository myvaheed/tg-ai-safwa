"""The Life in weeks pictures: the whole grid, and a close-up of the years with records.

They draw what `measures` painted and nothing else. Nothing here reads the database or
Telegram, so a picture is drawn in a worker thread.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from matplotlib.axes import Axes
from matplotlib.collections import PatchCollection
from matplotlib.figure import Figure
from matplotlib.patches import FancyBboxPatch, Rectangle

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
from .measures import Paint, Picture, blend
from .weeks import LIFE_WEEKS, Cell, LifeGrid, heading, month_starts, ordinal

WIDTH = 8.0
_LIVED = "#cfcdc4"
_EMPTY = "#f1f0eb"
_EMPTY_EDGE = "#d9d7cf"
_ACCENT = "#2a78d6"
_FRAME = {"boxstyle": "round,pad=0.25", "facecolor": SURFACE, "edgecolor": "none"}

# The whole grid: inches a square's step takes, and the gap a decade ends with.
_UNIT = 6.4 / 52.8
_DECADE_GAP = 0.6
_SQUARE = 0.78
# The close-up: inches a step takes, and how far apart its rows are.
_CLOSE_UNIT = 7.04 / 52.6
_CLOSE_ROW = 1.3


def draw_life(
    picture: Picture, grid: LifeGrid, today: date, since: date
) -> list[tuple[str, bytes]]:
    """The whole grid and the close-up, by name and as PNG, in the album's order."""
    span = Span.of(grid, today, since)
    return [("life", png(_whole(picture, span))), ("close_up", png(_close_up(picture, span)))]


@dataclass(frozen=True, slots=True)
class Span:
    """The grid with today and the day the records begin in it."""

    grid: LifeGrid
    today: date
    since: date
    first: Cell
    now: Cell

    @classmethod
    def of(cls, grid: LifeGrid, today: date, since: date) -> Span:
        since = max(since, grid.born)
        return cls(grid, today, since, grid.cell(since), grid.cell(today))

    def look(self, cell: Cell, picture: Picture) -> tuple[str, str, Paint | None]:
        """A square's fill, its edge, and the paint it carries if it carries one."""
        if ordinal(cell) < ordinal(self.first):
            return _LIVED, _LIVED, None
        if ordinal(cell) > ordinal(self.now):
            return SURFACE, GRID, None
        paint = picture.paints.get(cell)
        if paint is None:
            return _EMPTY, _EMPTY_EDGE, None
        return paint.color, paint.color, paint


def _ink_on(color: str) -> str:
    red, green, blue = (int(color[index : index + 2], 16) for index in (1, 3, 5))
    return INK if 0.299 * red + 0.587 * green + 0.114 * blue > 165 else "white"


# ------------------------------------------------------------------------------- legend


@dataclass(frozen=True, slots=True)
class _Key:
    """One entry of a legend: a box, a ring, a star or a colour scale, and its words."""

    style: str
    label: str
    face: str = SURFACE
    edge: str = SURFACE
    stops: tuple[str, ...] = ()
    low: str = ""
    high: str = ""

    @property
    def width(self) -> float:
        return 0.34 if self.style == "scale" else 0.05 + 0.0075 * len(self.label)


def _own_keys(picture: Picture) -> list[_Key]:
    if picture.scale is not None:
        scale = picture.scale
        return [
            _Key("scale", drawable(scale.caption), stops=scale.stops, low=scale.low, high=scale.high)
        ]
    return [
        _Key("box", drawable(label), face=color, edge=color) for color, label in picture.swatches
    ]


def _common_keys(picture: Picture) -> list[_Key]:
    keys = [
        _Key("box", "lived before the records", _LIVED, _LIVED),
        _Key("box", picture.missing, _EMPTY, _EMPTY_EDGE),
        _Key("box", "ahead", SURFACE, GRID),
        _Key("ring", "this week", edge=_ACCENT),
    ]
    if picture.best is not None:
        keys.append(_Key("star", "best week"))
    return keys


def _lines(*groups: list[_Key]) -> list[list[_Key]]:
    """Each group on lines of its own, wrapped to the picture's width."""
    lines: list[list[_Key]] = []
    for group in groups:
        line: list[_Key] = []
        used = 0.0
        for key in group:
            if line and used + key.width > 0.88:
                lines.append(line)
                line, used = [], 0.0
            line.append(key)
            used += key.width
        if line:
            lines.append(line)
    return lines


_LEGEND_LINE = 0.27


def _legend(figure: Figure, lines: list[list[_Key]], height: float) -> None:
    box_w, box_h = 0.13 / WIDTH, 0.13 / height
    for index, line in enumerate(lines):
        y = (0.18 + _LEGEND_LINE * (len(lines) - 1 - index)) / height
        x = 0.07
        for key in line:
            if key.style == "scale":
                step = 0.2 / 11
                for part in range(11):
                    figure.patches.append(Rectangle(
                        (x + part * step, y), step * 0.92, box_h,
                        facecolor=blend(key.stops, part / 10), linewidth=0,
                        transform=figure.transFigure, figure=figure,
                    ))
                figure.text(x - 0.006, y + box_h / 2, key.low, ha="right", va="center",
                            fontsize=7.5, color=MUTED)
                figure.text(x + 0.205, y + box_h / 2, f"{key.high}  {key.label}",
                            va="center", fontsize=8, color=INK_2)
            else:
                if key.style == "box":
                    figure.patches.append(Rectangle(
                        (x, y), box_w, box_h, facecolor=key.face, edgecolor=key.edge,
                        linewidth=0.7, transform=figure.transFigure, figure=figure,
                    ))
                elif key.style == "ring":
                    figure.patches.append(Rectangle(
                        (x, y), box_w, box_h, facecolor="none", edgecolor=key.edge,
                        linewidth=1.8, transform=figure.transFigure, figure=figure,
                    ))
                else:
                    figure.text(x + box_w / 2, y + box_h / 2, "★", ha="center", va="center",
                                fontsize=9, color=INK)
                figure.text(x + box_w + 0.008, y + box_h / 2, key.label, va="center",
                            fontsize=8, color=INK_2)
            x += key.width


def _head(figure: Figure, height: float, title: str, lines: tuple[str, ...]) -> None:
    figure.text(0.06, 1 - 0.42 / height, drawable(title), fontsize=16, fontweight="bold",
                color=INK)
    for index, line in enumerate(lines):
        figure.text(0.06, 1 - (0.7 + 0.21 * index) / height, drawable(line), fontsize=9.5,
                    color=MUTED if index == 0 else INK_2)


def _head_height(lines: int) -> float:
    return 0.7 + 0.21 * lines


# ---------------------------------------------------------------------------- the grid


def _row_y(age: int) -> float:
    return age + (age // 10) * _DECADE_GAP


def _whole(picture: Picture, span: Span) -> Figure:
    rows = span.grid.rows(span.today)
    top_units = 2.4
    units = _row_y(rows - 1) + 1 + top_units
    lines = (heading(span.grid, span.today, span.since), *picture.stats)
    legend = _lines(_own_keys(picture), _common_keys(picture))
    head = _head_height(len(lines))
    foot = 0.25 + _LEGEND_LINE * len(legend)
    height = head + units * _UNIT + foot
    figure = Figure(figsize=(WIDTH, height), dpi=CHART_DPI, facecolor=SURFACE)
    _head(figure, height, f"Life in weeks · {picture.title}", lines)
    axes = figure.add_axes((0.08, foot / height, 52.8 * _UNIT / WIDTH, units * _UNIT / height))
    axes.set_facecolor(SURFACE)
    axes.set_xlim(-0.6, 52.2)
    axes.set_ylim(_row_y(rows - 1) + 1, -top_units)
    axes.axis("off")

    squares, faces, edges = [], [], []
    for age in range(rows):
        y = _row_y(age)
        for week in range(LIFE_WEEKS):
            face, edge, _ = span.look((age, week), picture)
            squares.append(Rectangle((week, y), _SQUARE, _SQUARE))
            faces.append(face)
            edges.append(edge)
        if age % 5 == 0:
            axes.text(-0.9, y + _SQUARE / 2, str(age), ha="right", va="center", fontsize=8,
                      color=INK_2 if age % 10 == 0 else MUTED,
                      fontweight="bold" if age % 10 == 0 else "normal")
    axes.add_collection(PatchCollection(squares, facecolors=faces, edgecolors=edges,
                                        linewidths=0.5))
    for x, month in month_starts(span.grid.born):
        axes.text(x, -0.9, month, ha="left", va="bottom", fontsize=7.5, color=MUTED)
    born = span.grid.born
    axes.text(0, -1.9, f"born {day_month(born)} {born.year}", ha="left",
              va="bottom", fontsize=7.5, color=INK_2)
    if picture.best is not None:
        age, week = picture.best
        color = picture.paints[picture.best].color
        axes.text(week + _SQUARE / 2, _row_y(age) + _SQUARE / 2 + 0.05, "★", ha="center",
                  va="center", fontsize=6.5, color=_ink_on(color), zorder=6)
    _marks(axes, span, rows)
    _legend(figure, legend, height)
    return figure


def _marks(axes: Axes, span: Span, rows: int) -> None:
    """Where the records begin, and this week."""
    age, week = span.first
    y = _row_y(age)
    axes.plot([week - 0.11, week - 0.11], [y - 0.15, y + _SQUARE + 0.15], color=INK, lw=1.4,
              zorder=5)
    since = span.since
    axes.annotate(
        f"records since {day_month(since)} {since.year}",
        xy=(week - 0.11, y), xytext=(min(week, 34), y - 6.5 if age >= 8 else y + 8.5),
        fontsize=8.5, color=INK, ha="left", bbox=_FRAME, zorder=7,
        arrowprops={"arrowstyle": "-", "color": INK, "lw": 0.8,
                    "connectionstyle": "angle,angleA=0,angleB=90"},
    )
    age, week = span.now
    y = _row_y(age)
    axes.add_patch(Rectangle((week - 0.22, y - 0.22), _SQUARE + 0.44, _SQUARE + 0.44,
                             facecolor="none", edgecolor=_ACCENT, lw=2.2, zorder=6))
    below = age + 7 < rows
    axes.annotate(
        "You are here", xy=(week + _SQUARE + 0.3, y + _SQUARE / 2),
        xytext=(min(week + 6, 42), y + 5.5 if below else y - 5),
        fontsize=9, fontweight="bold", color=_ACCENT, ha="left", bbox=_FRAME, zorder=7,
        arrowprops={"arrowstyle": "-|>", "color": _ACCENT, "lw": 1.2,
                    "connectionstyle": f"arc3,rad={0.3 if below else -0.3}"},
    )


# --------------------------------------------------------------------------- the close-up


def _close_up(picture: Picture, span: Span) -> Figure:
    ages = range(span.first[0], span.now[0] + 1)
    units = 1.0 + len(ages) * _CLOSE_ROW
    legend = _lines(_own_keys(picture)) if picture.scale is None else []
    head = _head_height(1)
    foot = 0.2 + _LEGEND_LINE * len(legend)
    height = head + units * _CLOSE_UNIT + foot
    figure = Figure(figsize=(WIDTH, height), dpi=CHART_DPI, facecolor=SURFACE)
    _head(figure, height, f"Close-up · {picture.title}", (picture.note,))
    axes = figure.add_axes((0.08, foot / height, 52.6 * _CLOSE_UNIT / WIDTH,
                            units * _CLOSE_UNIT / height))
    axes.set_xlim(-0.3, 52.3)
    axes.set_ylim(units - 1.0, -1.0)
    axes.axis("off")
    for x, month in month_starts(span.grid.born):
        axes.text(x, -0.3, month, fontsize=8, color=MUTED)
    for row, age in enumerate(ages):
        y = row * _CLOSE_ROW
        axes.text(-0.5, y + 0.45, str(age), ha="right", va="center", fontsize=9,
                  color=INK_2, fontweight="bold")
        for week in range(LIFE_WEEKS):
            _close_square(axes, (age, week), week, y, picture, span)
    _legend(figure, legend, height)
    return figure


def _close_square(
    axes: Axes, cell: Cell, x: float, y: float, picture: Picture, span: Span
) -> None:
    face, edge, paint = span.look(cell, picture)
    box = {"boxstyle": "round,pad=0,rounding_size=0.12"}
    if paint is not None and paint.mix:
        bottom = y + 0.9
        for color, share in paint.mix:
            axes.add_patch(Rectangle((x + 0.05, bottom - 0.9 * share), 0.85, 0.9 * share,
                                     facecolor=color, linewidth=0))
            bottom -= 0.9 * share
    else:
        axes.add_patch(FancyBboxPatch((x + 0.05, y), 0.85, 0.9, facecolor=face,
                                      edgecolor=edge, linewidth=0.6, **box))
    if paint is not None and (paint.label or cell == picture.best):
        axes.text(x + 0.475, y + 0.47, "★" if cell == picture.best else paint.label,
                  ha="center", va="center", fontsize=5.5, fontweight="bold",
                  color=_ink_on(paint.color))
    if cell == span.now:
        axes.add_patch(FancyBboxPatch((x - 0.1, y - 0.15), 1.15, 1.2,
                                      boxstyle="round,pad=0,rounding_size=0.18",
                                      facecolor="none", edgecolor=_ACCENT, linewidth=2))
