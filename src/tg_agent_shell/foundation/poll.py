"""A loop that keeps going: one tick, whatever it raises, and the wait before the next.

Every long-running task in the application is this shape. It is written once because an
exception escaping one of them ends that task for good — asyncio swallows it, the feature
stops working, and nothing says so until the next restart. Cancellation is not caught:
`CancelledError` is a `BaseException`, so shutdown still ends the task at the `sleep`.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


async def run_poll(
    tick: Callable[[], Awaitable[object]], *, poll_seconds: float, name: str
) -> None:
    """Run `tick` for as long as the task lives, `poll_seconds` apart."""
    while True:
        try:
            await tick()
        except Exception:
            logger.exception("%s failed", name)
        await asyncio.sleep(poll_seconds)
