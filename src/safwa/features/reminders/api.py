"""How a clock the owner typed is read.

A door carries the vocabulary and what needs no operation. A Reminder another feature asks
for is written at the operations layer, in `use_cases.py`.
"""

from __future__ import annotations

from datetime import time

from .schedule import parse_clock


def parse_clock_or_off(raw: str) -> time | None:
    """A daily Settings clock. ``off`` is None, which is how that setting is switched off."""
    return None if raw.strip().lower() == "off" else parse_clock(raw)
