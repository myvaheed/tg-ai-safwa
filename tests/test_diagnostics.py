"""Diagnostics: the one command that describes Safwa rather than the owner's work."""

from __future__ import annotations

import re
from types import SimpleNamespace

from ui_harness import FakeMessage, services_for

from safwa.features.cards.use_cases import create_card
from safwa.features.diagnostics.telegram import command_status
from safwa.features.planning.use_cases import start_sprint


class _Memory:
    """What the status asks memory for, and nothing else: whether it can be read."""

    def __init__(self, error: str | None) -> None:
        self._error = error

    async def sync(self) -> SimpleNamespace:
        return SimpleNamespace(error=self._error)


def _revision(text: str) -> int:
    return int(re.search(r"Revision: (\d+)", text).group(1))


def _services(sessions, error: str | None = None):
    services = services_for(sessions)
    services.features = SimpleNamespace(memory=_Memory(error))
    return services


async def test_dg_status_001_the_status_names_the_mode_and_the_revision(sessions) -> None:
    """DG-STATUS-001 — tests/brd/diagnostics.feature"""
    message = FakeMessage(310, bot_message=True)

    await command_status(message, _services(sessions))

    text, markup = message.edits[-1]
    assert "Mode: planning" in text
    # Nothing to press: the status is read and left behind.
    assert markup is None
    revision = _revision(text)

    async with sessions() as session:
        await create_card(
            session, kind="action", title="Planned", stage="sprint", effort_points=2
        )
        await start_sprint(session, success_criteria="Ship v2")
        await session.commit()

    await command_status(message, _services(sessions))

    assert "Mode: sprint" in message.edits[-1][0]
    assert _revision(message.edits[-1][0]) > revision


async def test_dg_memory_002_a_memory_that_cannot_be_read_says_so(sessions) -> None:
    """DG-MEMORY-002 — tests/brd/diagnostics.feature"""
    message = FakeMessage(311, bot_message=True)

    await command_status(message, _services(sessions, "memory.md could not be read"))

    text = message.edits[-1][0]
    assert "Memory: memory.md could not be read" in text
    assert "Mode: planning" in text

    await command_status(message, _services(sessions))

    assert "Memory: OK" in message.edits[-1][0]
