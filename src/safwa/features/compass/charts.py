"""The Constellation drawn: a night sky with the Values glowing in it, and a short panel at its
right. The sky of Values, a Value's orbits and a Goal's branch share the sky, the heading, the
panel and the red ring around what Compass would ask about.

It writes in Segoe UI and Segoe UI Emoji where the machine has them, so a Value's emoji is
drawn in one colour, and in matplotlib's own DejaVu Sans everywhere; a character none of them
can draw is left out.
"""

from __future__ import annotations

import math
import textwrap
from functools import cache

from matplotlib import font_manager
from matplotlib.axes import Axes
from matplotlib.patches import Arc, Circle, Ellipse, FancyBboxPatch, RegularPolygon
from matplotlib.patheffects import withStroke

from ...foundation.charts import cut, drawable
from .constellation import ORBIT_SQUASH, PANEL_MARGIN, PANEL_WIDTH, Node, Picture, Star
from .effects import Canvas, Light, curve, dust, seed_of, star

STARLIGHT = "#eef1ff"
LABEL = "#b8bfd8"
DIM = "#8a90a8"
BRANCH = "#c9d3ff"
NEUTRAL = "#9fb0ff"
FLAG = "#ff5d73"
GOLD = "#ffd166"
# One colour a Value keeps, by its place among them all by name.
VALUE_COLOURS = (
    "#ffd166", "#7bdff2", "#f7a6c4", "#b8f28b", "#c3a6ff", "#ffa96b", "#6be3b8", "#f2f28b",
)
# A dark edge around every name, so a line passing under it does not cross its letters.
OUTLINE = [withStroke(linewidth=4, foreground="#03040c")]
# How much of a name each item writes, and a Goal's name in lines this long, at most two.
TITLE_CHARS = 34
GOAL_LINE_CHARS = 20
# A star this big next to the biggest has rays; the orbits' star has shorter ones, so they
# end before the nearest orbit.
RAYS_FROM = 0.6
CENTRE_RAYS = 0.6
LEGEND = {
    "sky": "Star size: the open work that carries a Value · Line: work that carries both · "
           "Red ring: what Compass would ask about",
    "orbits": "Solid line: carries the Value, as thick as the work it brings · "
              "Dashed: parent and child · Gold glow: Sprint or Today · Dots: other Values",
    "branch": "Dashed at the left: the Goals above · Arc: one-off Actions Done · "
              "Gold glow: Sprint or Today · ↻ repeats · Dots: Values",
}


@cache
def _families() -> tuple[str, ...]:
    installed = {font.name for font in font_manager.fontManager.ttflist}
    return (*(name for name in ("Segoe UI", "Segoe UI Emoji") if name in installed), "DejaVu Sans")


@cache
def _bold() -> tuple[str, ...]:
    installed = {font.name for font in font_manager.fontManager.ttflist}
    return (*(("Segoe UI Semibold",) if "Segoe UI Semibold" in installed else ()), *_families())


def _written(text: str, limit: int = TITLE_CHARS) -> str:
    return cut(drawable(text, _families()), limit)


def _colour(picture: Picture, value: int | None) -> str:
    if value is None or value not in picture.palette:
        return NEUTRAL
    return VALUE_COLOURS[picture.palette[value] % len(VALUE_COLOURS)]


def _panel_box(picture: Picture) -> tuple[float, float, float, float]:
    rows = len(picture.panel.rows) + (picture.panel.share is not None) * 0.6
    tall = 1.25 + rows * 0.46
    x1 = picture.width - PANEL_MARGIN
    y0 = max(0.9, (picture.height - tall) / 2)
    return x1 - PANEL_WIDTH, y0, x1, min(picture.height - 0.6, y0 + tall)


def draw(picture: Picture) -> bytes:
    canvas = Canvas(picture.width, picture.height)
    drawing = {"sky": _sky, "orbits": _orbits, "branch": _branch}[picture.kind]
    light, ink, lights = drawing(picture)
    box = _panel_box(picture)

    def framed(axes: Axes) -> None:
        ink(axes)
        _frame(axes, picture, box)

    return canvas.compose(
        canvas.sky(lights, seed_of(picture.title)),
        canvas.layer(lambda axes: (dust(axes, picture.width, picture.height, seed_of(picture.kind)),
                                   light(axes))),
        canvas.layer(framed),
        glass=[box],
    )


def _frame(axes: Axes, picture: Picture, box: tuple[float, float, float, float]) -> None:
    families, bold = list(_families()), list(_bold())
    top = picture.height
    heading = picture.stars[0] if picture.kind == "orbits" and picture.stars else None
    axes.text(0.45, top - 0.35, _written(picture.heading, 60), fontsize=26, va="top",
              color=_colour(picture, heading.value) if heading else STARLIGHT, family=bold,
              zorder=10)
    axes.text(0.47, top - 0.98, drawable(picture.subtitle, _families()), fontsize=11.5,
              color=DIM, family=families, va="top", zorder=10)
    axes.text(0.47, 0.28, drawable(LEGEND[picture.kind], _families()), fontsize=9.5, color=DIM,
              family=families, va="bottom", zorder=10)
    x0, y0, x1, y1 = box
    axes.add_patch(FancyBboxPatch(
        (x0, y0), x1 - x0, y1 - y0, boxstyle="round,pad=0,rounding_size=0.14",
        facecolor="none", edgecolor="#ffffff", alpha=0.18, linewidth=1, zorder=9,
    ))
    axes.text(x0 + 0.28, y1 - 0.3, picture.panel.heading, fontsize=14.5, color=STARLIGHT,
              family=bold, va="top", zorder=10)
    axes.plot([x0 + 0.28, x1 - 0.28], [y1 - 0.75] * 2, color="#ffffff", alpha=0.15,
              linewidth=0.8, zorder=10)
    row_y = y1 - 1.05
    share = picture.panel.share
    if share is not None:
        axes.add_patch(FancyBboxPatch(
            (x0 + 0.3, row_y - 0.05), x1 - x0 - 0.6, 0.1, boxstyle="round,pad=0,rounding_size=0.05",
            facecolor="#1d2550", edgecolor="none", zorder=10,
        ))
        if share:
            axes.add_patch(FancyBboxPatch(
                (x0 + 0.3, row_y - 0.05), (x1 - x0 - 0.6) * share, 0.1,
                boxstyle="round,pad=0,rounding_size=0.05", facecolor=GOLD, edgecolor="none",
                zorder=11,
            ))
        row_y -= 0.42
    for index, (mark, words) in enumerate(picture.panel.rows):
        colour = STARLIGHT if index < 2 else (DIM if mark == "·" else LABEL)
        axes.text(x0 + 0.34, row_y, drawable(mark, _families()), fontsize=12, color=colour,
                  family=families, va="center", ha="center", zorder=10)
        axes.text(x0 + 0.6, row_y, drawable(words, _families()), fontsize=11.5, color=colour,
                  family=families, va="center", zorder=10)
        row_y -= 0.46


def _ring(axes: Axes, x: float, y: float, radius: float) -> None:
    axes.add_patch(Circle((x, y), radius, facecolor="none", edgecolor=FLAG, linewidth=1.3,
                          linestyle=(0, (2, 2)), zorder=8))


def _dead(axes: Axes, star_: Star) -> None:
    axes.add_patch(Circle((star_.x, star_.y), 0.05, facecolor="#2a2e40", edgecolor=DIM,
                          linewidth=1, zorder=5))


# ----------------------------------------------------------------------- the sky of Values


def _sky(picture: Picture):
    heaviest = max((star_.radius for star_ in picture.stars), default=0.0)
    widest = max((together for _, _, together in picture.shared), default=0.0)
    at = {star_.value: star_ for star_ in picture.stars}

    def light(axes: Axes) -> None:
        for first, second, together in picture.shared:
            a, b = at[first], at[second]
            curve(axes, (a.x, a.y), (b.x, b.y), (_colour(picture, first), _colour(picture, second)),
                  0.6 + 2.6 * together / widest, 0.38)
        for star_ in picture.stars:
            if star_.radius:
                star(axes, star_.x, star_.y, star_.radius, _colour(picture, star_.value),
                     rays=star_.radius >= RAYS_FROM * heaviest)

    def ink(axes: Axes) -> None:
        bold, families = list(_bold()), list(_families())
        for star_ in picture.stars:
            if not star_.radius:
                _dead(axes, star_)
            if star_.flagged:
                _ring(axes, star_.x, star_.y, max(star_.radius * 1.3, 0.3))
            rayed = star_.radius >= RAYS_FROM * heaviest
            below = star_.y - (star_.radius * 3.5 + 0.08 if rayed and star_.radius else
                               max(star_.radius * 1.9, 0.42))
            axes.text(star_.x, below, _written(star_.name, 24), ha="center", va="top",
                      fontsize=15, color=_colour(picture, star_.value) if star_.radius else DIM,
                      family=bold, path_effects=OUTLINE, zorder=8)
            axes.text(star_.x, below - 0.36, f"rank {star_.rank} · weight {star_.weight:.0f}",
                      ha="center", va="top", fontsize=10, color=DIM, family=families,
                      path_effects=OUTLINE, zorder=8)

    lights: list[Light] = [
        (star_.x, star_.y, _colour(picture, star_.value), 0.25 + star_.radius / heaviest)
        for star_ in picture.stars if star_.radius
    ]
    return light, ink, lights


# ----------------------------------------------------------------------- one Value's orbits


def _orbits(picture: Picture):
    [centre] = picture.stars
    hue = _colour(picture, centre.value)
    at = {node.item: node for node in picture.nodes}
    rayed = dict(picture.rays)
    widest = max(rayed.values(), default=1.0)
    squash = ORBIT_SQUASH

    def light(axes: Axes) -> None:
        for radius in picture.orbits:
            axes.add_patch(Ellipse((centre.x, centre.y), 2 * radius, 2 * radius * squash,
                                   fill=False, edgecolor=NEUTRAL, alpha=0.12, linewidth=0.9,
                                   zorder=2))
        for parent, child in picture.branches:
            a, b = at[parent], at[child]
            curve(axes, (a.x, a.y), (b.x, b.y), (BRANCH, BRANCH), 1.0, 0.45, dashed=True)
        for item, brings in picture.rays:
            node = at[item]
            curve(axes, (centre.x, centre.y), (node.x, node.y), (hue, hue),
                  0.5 + 4.5 * brings / widest, 0.42, bend=0.04)
        if centre.radius:
            star(axes, centre.x, centre.y, centre.radius, hue, rays=True, reach=CENTRE_RAYS)
        for node in picture.nodes:
            _point(axes, node, hue if node.item in rayed else "#24324d", glow=True)

    def ink(axes: Axes) -> None:
        if not centre.radius:
            _dead(axes, centre)
        if centre.flagged:
            _ring(axes, centre.x, centre.y, 0.5)
        for node in picture.nodes:
            if node.kind in ("goal", "subgoal") and node.item not in rayed:
                axes.add_patch(Circle((node.x, node.y), _size(node), facecolor="none",
                                      edgecolor=hue, linewidth=1.3, zorder=7))
            _marks(axes, picture, node)
            outward = math.cos(math.atan2((node.y - centre.y) / squash, node.x - centre.x))
            rising = (node.y - centre.y) / squash
            if abs(outward) < 0.35:
                ha, dx = "center", 0.0
            else:
                ha, dx = ("left", 0.24) if outward > 0 else ("right", -0.24)
            va = "bottom" if rising > 0.3 else ("top" if rising < -0.3 else "center")
            dy = 0.2 if va == "bottom" else (-0.2 if va == "top" else 0.0)
            _label(axes, node, node.x + dx, node.y + dy, ha, va)

    return light, ink, [(centre.x, centre.y, hue, 1.3 if centre.radius else 0.4)]


# ----------------------------------------------------------------------- one Goal's branch


def _branch(picture: Picture):
    at = {node.item: node for node in picture.nodes}

    def hue(node: Node) -> str:
        return _colour(picture, node.values[0]) if node.values else NEUTRAL

    def light(axes: Axes) -> None:
        for parent, child in picture.branches:
            a, b = at[parent], at[child]
            ghost = a.ghost or b.ghost
            curve(axes, (a.x, a.y), (b.x, b.y), (BRANCH, BRANCH), 1.0 if ghost else 1.3,
                  0.4 if ghost else 0.5, bend=0.0, dashed=ghost)
        for node in picture.nodes:
            if not node.ghost:
                _point(axes, node, hue(node), glow=True)

    def ink(axes: Axes) -> None:
        for node in picture.nodes:
            if node.ghost:
                axes.add_patch(Circle((node.x, node.y), 0.1, facecolor="none", edgecolor=DIM,
                                      linewidth=1, linestyle=(0, (2, 2)), zorder=7))
                _label(axes, node, node.x, node.y + 0.22, "center", "bottom", colour=DIM)
                continue
            if node.progress is not None:
                size = _size(node) + 0.08
                axes.add_patch(Circle((node.x, node.y), size, facecolor="none",
                                      edgecolor="#ffffff", alpha=0.14, linewidth=2.2, zorder=7))
                if node.progress:
                    axes.add_patch(Arc((node.x, node.y), 2 * size, 2 * size,
                                       theta1=90 - 360 * node.progress, theta2=90,
                                       edgecolor=GOLD, linewidth=2.2, zorder=8))
            _marks(axes, picture, node)
            if node.kind in ("goal", "subgoal"):
                _label(axes, node, node.x, node.y + _size(node) + 0.16, "center", "bottom")
            else:
                _label(axes, node, node.x + 0.2, node.y, "left", "center")

    root = next((node for node in picture.nodes if node.level == 0 and not node.ghost), None)
    lights: list[Light] = [(root.x, root.y, hue(root), 0.9)] if root else []
    return light, ink, lights


# ----------------------------------------------------------------------- the items


def _size(node: Node) -> float:
    if node.kind in ("goal", "subgoal"):
        return 0.15 if node.level <= 0 else 0.11
    return 0.06


def _point(axes: Axes, node: Node, colour: str, *, glow: bool) -> None:
    if node.moving and glow:
        axes.add_patch(Circle((node.x, node.y), 0.14, color=GOLD, alpha=0.5, linewidth=0, zorder=5))
    if node.kind in ("goal", "subgoal"):
        axes.add_patch(Circle((node.x, node.y), _size(node), color=colour, alpha=0.95,
                              linewidth=0, zorder=6))
    elif node.kind == "action":
        axes.add_patch(Circle((node.x, node.y), 0.05, color=colour, alpha=0.95, linewidth=0,
                              zorder=6))
    else:
        axes.add_patch(RegularPolygon((node.x, node.y), 4, radius=0.08, color=colour,
                                      linewidth=0, zorder=6))


def _marks(axes: Axes, picture: Picture, node: Node) -> None:
    """The red ring, and a dot for each Value the item carries that the picture does not
    already say."""
    if node.flagged:
        _ring(axes, node.x, node.y, _size(node) + 0.14)
    for index, value in enumerate(node.values):
        axes.add_patch(Circle(
            (node.x + _size(node) + 0.1 + 0.12 * index, node.y - _size(node) - 0.06), 0.042,
            color=_colour(picture, value), zorder=8,
        ))


def _label(
    axes: Axes, node: Node, x: float, y: float, ha: str, va: str, *, colour: str | None = None
) -> None:
    goal = node.kind in ("goal", "subgoal")
    title = _written(node.title)
    if goal:
        lines = textwrap.wrap(title, GOAL_LINE_CHARS)
        title = "\n".join([*lines[:1], cut(" ".join(lines[1:]), GOAL_LINE_CHARS)] if len(lines) > 1 else lines)
    if node.repeats:
        title += " ↻"
    if node.blocked:
        title += " · Blocked"
    axes.text(x, y, title, ha=ha, va=va, fontsize=12 if goal else 10.5, linespacing=1.1,
              color=colour or (STARLIGHT if goal else LABEL),
              family=list(_bold() if goal else _families()), path_effects=OUTLINE, zorder=9)
