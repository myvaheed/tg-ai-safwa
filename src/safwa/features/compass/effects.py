"""The light the Constellation is drawn with, which matplotlib cannot give on its own.

A picture is three layers. The sky is worked out in numpy: a night gradient, a nebula tinted by
the stars near it, and darker corners. Matplotlib draws the light over it — stars, rays, lines
and dust — and the ink — names and the panel. Matplotlib only covers one thing with another,
so Pillow blurs the light at several widths and adds it to the sky, which is what makes it
glow, and lays the ink on top sharp. Under the panel the picture is blurred like frosted glass.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Sequence
from io import BytesIO

import numpy as np
from matplotlib.axes import Axes
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.collections import LineCollection
from matplotlib.colors import to_rgb
from matplotlib.figure import Figure
from matplotlib.patches import Circle, Polygon
from PIL import Image, ImageChops, ImageDraw, ImageFilter

DPI = 170
SKY_TOP = "#0d1440"
SKY_BOTTOM = "#020309"
HAZE = "#3a2a7a"
# The nebula is worked out at a part of the picture's size and smoothed up to it.
NEBULA_SCALE = 4
# Each blur of the light, its radius in pixels and how much of it is added.
BLOOM = ((2, 0.8), (9, 0.75), (28, 0.55), (70, 0.35))
GLASS_BLUR = 22
GLASS_TINT = "#070a1c"
# Dust stars per square inch of sky.
DUST_DENSITY = 16

Light = tuple[float, float, str, float]


class Canvas:
    """One picture's size in inches, and the three layers it is made of."""

    def __init__(self, width: float, height: float) -> None:
        self.width, self.height = width, height
        self.px = (round(width * DPI), round(height * DPI))

    def to_px(self, x: float, y: float) -> tuple[float, float]:
        return x * DPI, (self.height - y) * DPI

    def layer(self, draw: Callable[[Axes], None]) -> Image.Image:
        """What `draw` puts on a clear sheet the size of the picture, one unit an inch."""
        figure = Figure(figsize=(self.width, self.height), dpi=DPI)
        figure.patch.set_alpha(0)
        axes = figure.add_axes((0, 0, 1, 1))
        axes.set_xlim(0, self.width)
        axes.set_ylim(0, self.height)
        axes.axis("off")
        axes.patch.set_alpha(0)
        draw(axes)
        canvas = FigureCanvasAgg(figure)
        canvas.draw()
        return Image.frombuffer(
            "RGBA", canvas.get_width_height(), canvas.buffer_rgba(), "raw", "RGBA", 0, 1
        ).resize(self.px)

    def sky(self, lights: Sequence[Light], seed: int) -> Image.Image:
        """The night behind everything: `lights` are (x, y, colour, strength) that tint the
        nebula near them."""
        width, height = (max(1, size // NEBULA_SCALE) for size in self.px)
        scale = DPI / NEBULA_SCALE
        rng = np.random.default_rng(seed)
        rise = np.linspace(1, 0, height, dtype=np.float32)[:, None, None]
        top, bottom = np.array(to_rgb(SKY_TOP)), np.array(to_rgb(SKY_BOTTOM))
        rgb = np.broadcast_to(bottom + (top - bottom) * rise, (height, width, 3)).copy()

        def octave(cells: tuple[int, int]) -> np.ndarray:
            noise = Image.fromarray((rng.random(cells) * 255).astype(np.uint8))
            return np.asarray(noise.resize((width, height), Image.BICUBIC), dtype=np.float32) / 255

        cloud = 0.6 * octave((6, 10)) + 0.3 * octave((14, 22)) + 0.1 * octave((40, 64))
        cloud = np.clip((cloud - 0.35) / 0.5, 0, 1) ** 1.6
        ys, xs = np.mgrid[0:height, 0:width].astype(np.float32)
        tint = np.zeros((height, width, 3), dtype=np.float32)
        for x, y, colour, strength in lights:
            px, py = x * scale, (self.height - y) * scale
            sigma = (0.9 + 0.9 * strength) * scale
            falloff = np.exp(-((xs - px) ** 2 + (ys - py) ** 2) / (2 * sigma**2))
            tint += np.array(to_rgb(colour), dtype=np.float32) * (falloff * strength)[..., None]
        rgb += tint * cloud[..., None] * 0.32
        rgb += np.array(to_rgb(HAZE), dtype=np.float32) * (cloud * 0.07)[..., None]
        reach = np.sqrt(((xs - width / 2) / (width / 2)) ** 2 + ((ys - height / 2) / (height / 2)) ** 2)
        rgb *= (1 - 0.45 * np.clip(reach - 0.55, 0, 1) ** 1.3)[..., None]
        small = Image.fromarray((np.clip(rgb, 0, 1) * 255).astype(np.uint8))
        return small.resize(self.px, Image.BICUBIC)

    def compose(
        self,
        sky: Image.Image,
        light: Image.Image,
        ink: Image.Image,
        glass: Sequence[tuple[float, float, float, float]] = (),
    ) -> bytes:
        """The PNG of the three layers; `glass` are (x0, y0, x1, y1) frosted under the ink."""
        black = Image.new("RGB", self.px)
        shine = Image.composite(light.convert("RGB"), black, light.getchannel("A"))
        glow = black
        for radius, gain in BLOOM:
            blurred = shine.filter(ImageFilter.GaussianBlur(radius))
            glow = ImageChops.add(glow, blurred.point(lambda level, gain=gain: level * gain))
        image = ImageChops.add(ImageChops.screen(sky, glow), shine)
        for x0, y0, x1, y1 in glass:
            left, top = self.to_px(x0, y1)
            right, bottom = self.to_px(x1, y0)
            box = (round(left), round(top), round(right), round(bottom))
            frosted = image.crop(box).filter(ImageFilter.GaussianBlur(GLASS_BLUR))
            frosted = Image.blend(frosted, Image.new("RGB", frosted.size, GLASS_TINT), 0.45)
            mask = Image.new("L", frosted.size, 0)
            ImageDraw.Draw(mask).rounded_rectangle(
                (0, 0, frosted.size[0] - 1, frosted.size[1] - 1), radius=round(0.14 * DPI), fill=255
            )
            image.paste(frosted, box[:2], mask)
        image = Image.alpha_composite(image.convert("RGBA"), ink).convert("RGB")
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()


def dust(axes: Axes, width: float, height: float, seed: int) -> None:
    """Faint stars of every size, most of them tiny, a few blue or warm."""
    count = round(width * height * DUST_DENSITY)
    rng = np.random.default_rng(seed)
    bright = rng.random(count)
    tints = rng.choice(["#ffffff", "#a9c1ff", "#ffdcb0"], size=count, p=[0.75, 0.15, 0.10])
    axes.scatter(
        rng.uniform(0, width, count), rng.uniform(0, height, count),
        s=0.15 + 9 * bright**7, c=list(tints), alpha=0.35 + 0.55 * bright,
        linewidths=0, zorder=1,
    )


def star(
    axes: Axes, x: float, y: float, radius: float, colour: str, *, rays: bool, reach: float = 1.0
) -> None:
    """A star: a coloured disc with a white core, and rays across it when it is big, `reach`
    times their usual length."""
    axes.add_patch(Circle((x, y), radius, color=colour, alpha=0.28, linewidth=0, zorder=4))
    axes.add_patch(Circle((x, y), radius * 0.55, color=colour, alpha=0.95, linewidth=0, zorder=5))
    axes.add_patch(Circle((x, y), radius * 0.28, color="white", linewidth=0, zorder=6))
    if not rays:
        return
    for angle in range(0, 360, 45):
        long = angle % 90 == 0
        a = math.radians(angle)
        base = radius * (0.16 if long else 0.1)
        length = radius * (3.4 if long else 1.6) * reach
        side = (math.cos(a + math.pi / 2) * base, math.sin(a + math.pi / 2) * base)
        axes.add_patch(Polygon(
            [(x + side[0], y + side[1]), (x + math.cos(a) * length, y + math.sin(a) * length),
             (x - side[0], y - side[1])],
            closed=True, color=colour, alpha=0.7 if long else 0.42, linewidth=0, zorder=4,
        ))


def curve(
    axes: Axes,
    start: tuple[float, float],
    end: tuple[float, float],
    colours: tuple[str, str],
    width: float,
    alpha: float,
    *,
    bend: float = 0.06,
    dashed: bool = False,
    zorder: int = 3,
) -> None:
    """A gently bent line from `start` to `end`, its colour passing from one to the other."""
    (x0, y0), (x1, y1) = start, end
    middle = ((x0 + x1) / 2 - (y1 - y0) * bend, (y0 + y1) / 2 + (x1 - x0) * bend)
    steps = np.linspace(0, 1, 48)
    xs = (1 - steps) ** 2 * x0 + 2 * (1 - steps) * steps * middle[0] + steps**2 * x1
    ys = (1 - steps) ** 2 * y0 + 2 * (1 - steps) * steps * middle[1] + steps**2 * y1
    first, last = np.array(to_rgb(colours[0])), np.array(to_rgb(colours[1]))
    if dashed:
        axes.plot(xs, ys, color=tuple((first + last) / 2), alpha=alpha, linewidth=width,
                  linestyle=(0, (2, 3)), dash_capstyle="round", zorder=zorder)
        return
    points = np.column_stack([xs, ys])
    axes.add_collection(LineCollection(
        np.stack([points[:-1], points[1:]], axis=1),
        colors=[(*(first + (last - first) * step), alpha) for step in steps[:-1]],
        linewidths=width, capstyle="round", zorder=zorder,
    ))


def seed_of(text: str) -> int:
    """The same sky for the same picture, every time it is drawn."""
    return random.Random(text).randrange(2**31)
