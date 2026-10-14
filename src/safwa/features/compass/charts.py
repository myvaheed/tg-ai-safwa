"""The Constellation drawn: a night sky, the Values as glowing stars across its top, the tree
hanging below them, and what Compass would ask about ringed in red.

It writes in Segoe UI and Segoe UI Emoji where the machine has them, so a Value's emoji is
drawn in one colour, and in matplotlib's own DejaVu Sans everywhere; a character none of them
can draw is left out.
"""

from __future__ import annotations

import random
import textwrap
from datetime import date
from functools import cache
from io import BytesIO

import numpy as np
from matplotlib import font_manager
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.figure import Figure
from matplotlib.patches import PathPatch
from matplotlib.path import Path
from matplotlib.patheffects import withStroke

from ...foundation.charts import cut, day_month, drawable
from .constellation import Item, Sky, Star

SKY_TOP = "#0a1030"
SKY_BOTTOM = "#03040c"
STARLIGHT = "#eef1ff"
LABEL = "#b8bfd8"
DIM = "#8a90a8"
BRANCH = "#c9d3ff"
FLAG = "#ff5d73"
# One colour a Value keeps, by its place among them all by name.
VALUE_COLOURS = (
    "#ffd166", "#7bdff2", "#f7a6c4", "#b8f28b", "#c3a6ff", "#ffa96b", "#6be3b8", "#f2f28b",
)
SKY_DPI = 170
# How many inches one leaf of the tree takes across, and one unit up; the picture's width
# stays within these, and a wider tree is drawn closer together.
LEAF_INCHES = 0.85
UNIT_INCHES = 0.75
WIDTH_INCHES = (16.0, 64.0)
# The room below the Actions for their slanted names, and above the Values for the title.
BELOW = 3.6
ABOVE = 2.8
TITLE_CHARS = {"value": 28, "goal": 54, "subgoal": 54, "action": 30, "check": 30}
# A Goal's name stands over it in lines this long, at most this many.
GOAL_LINE_CHARS = 18
GOAL_LINES = 3
# A dark edge around every name, so a line passing under it does not cross its letters.
OUTLINE = [withStroke(linewidth=3, foreground=SKY_BOTTOM)]
STAR_SEED = 7


@cache
def _families() -> tuple[str, ...]:
    installed = {font.name for font in font_manager.fontManager.ttflist}
    return (*(name for name in ("Segoe UI", "Segoe UI Emoji") if name in installed), "DejaVu Sans")


def _written(text: str, kind: str) -> str:
    return cut(drawable(text, _families()), TITLE_CHARS[kind])


def _colour(sky: Sky, hue: int | None) -> str:
    return DIM if hue is None else VALUE_COLOURS[sky.palette[hue] % len(VALUE_COLOURS)]


def _counted(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def draw_sky(sky: Sky, today: date) -> bytes:
    families = list(_families())
    left, right = -1.5, sky.width + 0.5
    bottom, top = -BELOW, sky.top + ABOVE
    width = min(max((right - left) * LEAF_INCHES, WIDTH_INCHES[0]), WIDTH_INCHES[1])
    height = (top - bottom) * UNIT_INCHES
    figure = Figure(figsize=(width, height))
    axes = figure.add_axes((0, 0, 1, 1))
    axes.set_xlim(left, right)
    axes.set_ylim(bottom, top)
    axes.axis("off")

    night = LinearSegmentedColormap.from_list("night", [SKY_BOTTOM, SKY_TOP])
    axes.imshow(
        np.linspace(0, 1, 256).reshape(-1, 1), aspect="auto", cmap=night,
        extent=(left, right, bottom, top), origin="lower", zorder=0,
    )
    dust = random.Random(STAR_SEED)
    count = int(width * height * 4)
    axes.scatter(
        [dust.uniform(left, right) for _ in range(count)],
        [dust.uniform(bottom, top) for _ in range(count)],
        s=[dust.uniform(0.2, 3) for _ in range(count)],
        color="white", alpha=0.45, linewidths=0, zorder=1,
    )

    at = {star.item: star for star in sky.stars}

    def curve(start: tuple[float, float], end: tuple[float, float], colour: str,
              alpha: float, width: float, zorder: int) -> None:
        (x0, y0), (x1, y1) = start, end
        middle = (y0 + y1) / 2
        path = Path(
            [(x0, y0), (x0, middle), (x1, middle), (x1, y1)],
            [Path.MOVETO, Path.CURVE4, Path.CURVE4, Path.CURVE4],
        )
        axes.add_patch(PathPatch(
            path, facecolor="none", edgecolor=colour, alpha=alpha, linewidth=width,
            zorder=zorder, capstyle="round",
        ))

    def point(item: Item) -> tuple[float, float]:
        return at[item].x, at[item].y

    for parent, child in sky.branches:
        faint = at[child].done or at[parent].done
        curve(point(parent), point(child), BRANCH, 0.12 if faint else 0.28, 1.0, 2)
    for value, item in sky.links:
        curve(point(("value", value)), point(item), _colour(sky, value),
              0.15 if at[item].done else 0.42, 1.5, 3)

    for star in sky.stars:
        _draw(axes, sky, star, families)

    flagged = [star for star in sky.stars if star.flagged]
    axes.scatter(
        [star.x for star in flagged], [star.y for star in flagged],
        s=[2600 if star.kind == "value" else 1500 for star in flagged],
        facecolors="none", edgecolors=FLAG, linewidths=1.4, linestyles=(0, (2, 2)), zorder=9,
    )

    kinds = [star.kind for star in sky.stars]
    counts = (
        (kinds.count("value"), "Value"),
        (kinds.count("goal") + kinds.count("subgoal"), "Goal"),
        (kinds.count("action"), "Action"),
        (kinds.count("check"), "Check"),
    )
    summary = " · ".join((
        f"{day_month(today)} {today.year}",
        *(_counted(count, noun) for count, noun in counts if count),
    ))
    axes.text(left + 0.3, top - 0.35, drawable(sky.title, _families()), fontsize=22,
              color=STARLIGHT, family=families, va="top", zorder=10)
    axes.text(left + 0.3, top - 1.2, summary, fontsize=11, color=DIM, family=families,
              va="top", zorder=10)
    axes.text(
        left + 0.3, bottom + 0.3,
        "★ Value    ● Goal, bigger above    • Action    ◆ Check    "
        "red ring: what Compass would ask about",
        fontsize=10, color=DIM, family=families, va="bottom", zorder=10,
    )
    buffer = BytesIO()
    figure.savefig(buffer, format="png", dpi=SKY_DPI, facecolor=SKY_BOTTOM)
    return buffer.getvalue()


def _draw(axes, sky: Sky, star: Star, families: list[str]) -> None:
    colour = _colour(sky, star.hue)
    seen = 0.4 if star.done else 1.0
    title = _written(star.title, star.kind)
    if star.kind == "value":
        dim = 0.4 if star.flagged else 1.0
        for size, alpha in ((5200, 0.03), (2600, 0.06), (1200, 0.12), (450, 0.25)):
            axes.scatter(star.x, star.y, s=size, color=colour, alpha=alpha * dim, linewidths=0,
                         zorder=4)
        axes.scatter(star.x, star.y, s=520, marker=(4, 1, 0), color=colour, alpha=dim,
                     linewidths=0, zorder=5)
        axes.scatter(star.x, star.y, s=260, marker=(4, 1, 45), color="white", alpha=0.95 * dim,
                     linewidths=0, zorder=5)
        axes.text(star.x, star.y + 0.6, title, ha="center", va="bottom", fontsize=14,
                  color=colour, alpha=max(dim, 0.6), family=families, zorder=6,
                  path_effects=OUTLINE)
    elif star.kind in ("goal", "subgoal"):
        size = 180 + 160 * star.level
        carried = star.hue is not None and any(
            item == star.item for _, item in sky.links
        )
        axes.scatter(star.x, star.y, s=size * 3.2, color=colour, alpha=0.12 * seen,
                     linewidths=0, zorder=6)
        axes.scatter(star.x, star.y, s=size, color=colour if carried else "none",
                     edgecolors="white" if carried else colour, alpha=seen, linewidths=1.2,
                     zorder=7)
        lines = textwrap.wrap(title, GOAL_LINE_CHARS)
        if len(lines) > GOAL_LINES:
            lines = [*lines[: GOAL_LINES - 1], cut(" ".join(lines[GOAL_LINES - 1:]), GOAL_LINE_CHARS)]
        axes.text(star.x, star.y + 0.3 + 0.04 * star.level, "\n".join(lines), ha="center",
                  va="bottom", fontsize=10.5, linespacing=1.15, color=STARLIGHT, alpha=seen,
                  family=families, zorder=8, path_effects=OUTLINE)
    elif star.kind == "action":
        axes.scatter(star.x, star.y, s=46, color=colour, alpha=0.9 * seen, linewidths=0, zorder=7)
        _slanted(axes, star, title, seen, families)
    else:
        axes.scatter(star.x, star.y, s=60, marker="D", color=colour, edgecolors="white",
                     linewidths=0.6, zorder=7)
        _slanted(axes, star, title, seen, families)


def _slanted(axes, star: Star, title: str, seen: float, families: list[str]) -> None:
    """A name under its star, slanted, so the names of a row of neighbours do not meet."""
    axes.text(star.x, star.y - 0.28, title, ha="right", va="top", rotation=60,
              rotation_mode="anchor", fontsize=8.5, color=LABEL, alpha=seen,
              family=families, zorder=8, path_effects=OUTLINE)
