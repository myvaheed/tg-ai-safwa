"""What the bot is doing right now, as three states that cannot be combined wrongly."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Idle:
    """Nothing is being written, so whatever the owner does is taken up at once."""


@dataclass(frozen=True, slots=True)
class Answering:
    """One request of the owner's, and the message in the chat that says so.

    `task` is the coroutine writing the answer, so cancelling stops the provider traffic
    rather than only marking the answer stale. `notice` is the message the owner can tap
    `/cancel` on, and it is taken out of the chat when this state ends.
    """

    source_message_id: int
    task: asyncio.Task[Any] | None = None
    notice: int | None = None


@dataclass(frozen=True, slots=True)
class BackgroundWork:
    """Work nobody asked for, which the owner always outranks.

    It carries no source message and no notice: nothing in the chat says it is happening,
    and there is nothing to take back out when it ends.
    """


type TurnState = Idle | Answering | BackgroundWork
