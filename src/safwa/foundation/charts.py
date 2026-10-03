"""What every picture Safwa draws shares: its resolution, its paper and ink, how it writes a
day, and which characters its font can draw.

The font is matplotlib's own DejaVu Sans. It draws Latin and Cyrillic but no emoji, and a
character it has no shape for comes out as an empty box, so text the owner wrote goes through
`drawable` first.
"""

from __future__ import annotations

from datetime import date
from functools import cache
from io import BytesIO

from matplotlib import font_manager
from matplotlib.figure import Figure
from matplotlib.ft2font import FT2Font

CHART_DPI = 160
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def day_month(day: date) -> str:
    """26 Sep: the day first, as Safwa's screens write a date."""
    return f"{day.day} {MONTHS[day.month - 1]}"


@cache
def _shapes() -> frozenset[int]:
    return frozenset(FT2Font(font_manager.findfont(font_manager.FontProperties())).get_charmap())


def drawable(text: str) -> str:
    """`text` without the characters the font has no shape for, such as emoji."""
    shapes = _shapes()
    return " ".join("".join(ch for ch in text if ch.isspace() or ord(ch) in shapes).split())


def png(figure: Figure) -> bytes:
    buffer = BytesIO()
    figure.savefig(buffer, format="png", dpi=CHART_DPI, facecolor=SURFACE)
    return buffer.getvalue()
