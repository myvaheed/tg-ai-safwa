"""The Life in weeks pictures: the whole grid, and a close-up of the years with records.

They draw what `measures` painted and nothing else. Nothing here reads the database or
Telegram, so a picture is drawn in a worker thread.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import ceil

from matplotlib.artist import Artist
from matplotlib.axes import Axes
from matplotlib.collections import PatchCollection
from matplotlib.colors import to_rgb
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.patches import FancyBboxPatch, Patch, Rectangle

from ...foundation.charts import (
    CHART_DPI,
    GRID,
    INK,
    INK_2,
    MUTED,
    SURFACE,
    cut,
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
    return [(name, png(draw(picture, span))) for name, draw in LIFE_ALBUM]


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

# How many of its own colours a picture's legend puts on a row and how long a name there may
# be; in inches, where its lowest row sits and how far apart its rows are.
_KEYS_A_ROW = 4
_KEY_NAME = 20
_KEYS_BOTTOM = 0.14
_KEY_ROW = 0.24


def _own_rows(picture: Picture) -> int:
    return 1 if picture.scale is not None else ceil(len(picture.swatches) / _KEYS_A_ROW)


def _keys(figure: Figure, handles: list[Artist], bottom: float, columns: int) -> None:
    """One legend of these keys, its lowest row `bottom` inches up the picture."""
    # A legend fills its columns top to bottom; read across, the keys keep their order.
    handles = [
        handles[index] for column in range(columns) for index in range(column, len(handles), columns)
    ]
    figure.legend(
        handles=handles, loc="lower left", bbox_to_anchor=(0.06, bottom / figure.get_figheight()),
        ncol=columns, frameon=False, fontsize=8, labelcolor=INK_2, borderaxespad=0, borderpad=0,
        handlelength=1, handleheight=1, handletextpad=0.6, columnspacing=1.8, labelspacing=1.0,
    )


def _box(face: str, edge: str, label: str, width: float = 0.7) -> Patch:
    return Patch(facecolor=face, edgecolor=edge, linewidth=width, label=label)


def _own_keys(figure: Figure, picture: Picture, bottom: float) -> None:
    """The picture's own colours, or the scale they run through."""
    scale = picture.scale
    if scale is None:
        handles = [
            _box(color, color, cut(drawable(label), _KEY_NAME)) for color, label in picture.swatches
        ]
        _keys(figure, handles, bottom, _KEYS_A_ROW)
        return
    height = figure.get_figheight()
    bar = figure.add_axes((0.08, bottom / height, 0.2, 0.12 / height))
    bar.imshow([[to_rgb(blend(scale.stops, step / 99)) for step in range(100)]], aspect="auto")
    bar.set_axis_off()
    middle = (bottom + 0.06) / height
    figure.text(0.074, middle, scale.low, ha="right", va="center", fontsize=7.5, color=MUTED)
    figure.text(0.288, middle, f"{scale.high}  {drawable(scale.caption)}", va="center",
                fontsize=8, color=INK_2)


def _common_keys(figure: Figure, picture: Picture) -> None:
    """What the grey, pale, empty and ringed weeks of every picture are, and its star."""
    handles: list[Artist] = [
        _box(_LIVED, _LIVED, "lived before the records"),
        _box(_EMPTY, _EMPTY_EDGE, picture.missing),
        _box(SURFACE, GRID, "ahead"),
        _box("none", _ACCENT, "this week", 1.8),
    ]
    if picture.best is not None:
        handles.append(Line2D([], [], linestyle="none", marker="*", markersize=9, color=INK,
                              label="best week"))
    _keys(figure, handles, _KEYS_BOTTOM, len(handles))


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
    head = _head_height(len(lines))
    foot = 0.25 + _KEY_ROW * (_own_rows(picture) + 1)
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
    _common_keys(figure, picture)
    _own_keys(figure, picture, _KEYS_BOTTOM + _KEY_ROW)
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
    # Its squares write their numbers, so a scale is left to the whole grid.
    keyed = picture.scale is None
    head = _head_height(1)
    foot = 0.2 + (_KEY_ROW * _own_rows(picture) if keyed else 0)
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
    if keyed:
        _own_keys(figure, picture, _KEYS_BOTTOM)
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


# The album's order: the whole life, then the years with records close up.
LIFE_ALBUM = (("life", _whole), ("close_up", _close_up))
