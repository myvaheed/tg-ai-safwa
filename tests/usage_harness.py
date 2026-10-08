"""A controlled clock and recorder shared by usage tests and E2E flows."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ui_harness import FakeCallback, FakeMessage, services_for

from tg_agent_shell.usage.recorder import UsageRecorder

START = datetime(2026, 10, 8, 12, tzinfo=UTC)


class UsageClock:
    def __init__(self, at: datetime = START) -> None:
        self.at = at
        self.elapsed = 0.0

    def now(self) -> datetime:
        return self.at

    def ticks(self) -> float:
        return self.elapsed

    def advance(self, seconds: float) -> None:
        self.at += timedelta(seconds=seconds)
        self.elapsed += seconds


def tracked_services(sessions, monkeypatch, *, root=None, transcriber=None):
    monkeypatch.setattr("tg_agent_shell.telegram.services.Message", FakeMessage)
    monkeypatch.setattr("tg_agent_shell.telegram.services.CallbackQuery", FakeCallback)
    services = services_for(sessions, root=root, transcriber=transcriber)
    clock = UsageClock()
    services.usage = UsageRecorder(sessions, 42, clock=clock, ticks=clock.ticks)
    return services, clock


async def total(services) -> int:
    async with services.sessions() as session:
        return await services.usage.seconds(session)
