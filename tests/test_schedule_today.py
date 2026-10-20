from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from schedule_helpers import create_card
from sqlalchemy import select

from safwa.bootstrap.modules import REGISTRY
from safwa.features.cards.hooks import (
    SCHEDULE_PLAN_HOOK,
    schedule_midnight,
    schedule_plan_request,
)
from safwa.features.cards.model import Card
from safwa.features.cards.use_cases import archive_subtree, finish_action
from safwa.features.planning.api import SPRINT_STARTED
from safwa.features.planning.hooks import SPRINT_EXPIRY_HOOK
from safwa.features.planning.model import Sprint, SprintCommitment
from safwa.features.planning.use_cases import start_sprint
from safwa.features.profile.api import hook_switched_on, morning_time, set_hook_switch
from safwa.features.schedules.api import set_schedule
from safwa.foundation.workspace import Workspace
from tg_agent_shell.cues.initiatives import TickPoll, hand_on_start, queue_advice
from tg_agent_shell.cues.model import Cue
from tg_agent_shell.foundation.changes import Committed
from tg_agent_shell.foundation.clock import utcnow
from tg_agent_shell.hooks.contracts import OnStarted, OnTick, RunAndAdvise, RunContext, Tick
from tg_agent_shell.hooks.registry import HookRegistry
from tg_agent_shell.hooks.ticks import TickSchedule


@pytest.mark.parametrize("running", [False, True])
@pytest.mark.parametrize("after_restart", [False, True])
async def test_pl_hardtime_021_midnight_moves_today_and_queues_a_notification(
    sessions, monkeypatch, running, after_restart
):
    """PL-HARDTIME-021 — tests/brd/planning.feature"""
    assert SCHEDULE_PLAN_HOOK.on == (OnTick(at=schedule_midnight), OnStarted())
    assert isinstance(SCHEDULE_PLAN_HOOK.effect, RunAndAdvise)
    tz = ZoneInfo("Europe/Istanbul")
    today = utcnow().astimezone(tz).date() + timedelta(days=1)
    midnight_now = datetime.combine(today, time(0, 0), tzinfo=tz)
    now = midnight_now + timedelta(hours=6) if after_restart else midnight_now
    monkeypatch.setattr("safwa.features.cards.hooks.utcnow", lambda: now)

    async with sessions() as session:
        assert await schedule_midnight(session) == time(0, 0)
        if running:
            await create_card(session, kind="action", title="Planned", stage="sprint")
            await start_sprint(session, success_criteria="Ship v2", length_days=7)

        async def fixed(title, day, stage="backlog"):
            return await create_card(
                session, kind="action", title=title, stage=stage,
                schedule=f"{day:%d.%m.%Y} 00:00",
            )

        async def daily(title, stage, **bounds):
            card = await create_card(session, kind="action", title=title, stage=stage)
            await set_schedule(session, card, "three times a day", {
                "kind": "quota", "period": "day", "count": 3, **bounds,
            })
            return card

        due = [
            await fixed("Dentist", today),
            await fixed("Call mom", today, "sprint"),
            await daily("Water", "sprint"),
            await daily("Stretch", "backlog"),
        ]
        later = await fixed("Tomorrow", today + timedelta(days=1))
        past = await fixed("Yesterday", today)
        past.period_start = midnight_now - timedelta(days=1)
        already = await fixed("Already in Today", today, "today")
        paid = await fixed("Paid", today)
        await finish_action(session, paid.id)
        shelved = await fixed("Shelved", today)
        await finish_action(session, shelved.id)
        await archive_subtree(session, shelved.id)
        future = await daily("Future quota", "sprint", start=(today + timedelta(days=1)).isoformat())
        expired = await daily("Expired quota", "backlog", until=(today - timedelta(days=1)).isoformat())
        undated = await create_card(session, kind="action", title="No Schedule", stage="sprint")
        ids = [card.id for card in due]
        excluded = {card.id: card.effective_stage for card in (
            later, past, already, paid, shelved, future, expired, undated,
        )}
        await session.commit()

    async def no_publication(text, kind):
        raise AssertionError("the notification must use the Cue queue")

    work = RunContext(
        resources=None, sessions=sessions, still_current=lambda: True, publish=no_publication,
    )
    hooks = HookRegistry.of(
        (SCHEDULE_PLAN_HOOK,), owners=frozenset({"cards"}), policy=hook_switched_on,
    )

    async def fire():
        if after_restart:
            await hand_on_start(hooks, sessions, resources=None)
        else:
            await queue_advice(hooks, sessions, Tick("00:00", schedule_midnight), work=work)

    await queue_advice(hooks, sessions, Tick("09:00", morning_time), work=work)
    await queue_advice(hooks, sessions, Committed(SPRINT_STARTED, 1), work=work)
    async with sessions() as session:
        assert not list(await session.scalars(select(Cue)))
        for card_id in ids:
            assert (await session.get(Card, card_id)).effective_stage != "today"

        await set_hook_switch(session, SCHEDULE_PLAN_HOOK.name, on=False)
        await session.commit()
    await fire()
    async with sessions() as session:
        assert not list(await session.scalars(select(Cue)))
        for card_id in ids:
            assert (await session.get(Card, card_id)).effective_stage != "today"
        await set_hook_switch(session, SCHEDULE_PLAN_HOOK.name, on=True)
        await session.commit()

    clock = TickSchedule(now=midnight_now - timedelta(seconds=1), tz=tz)
    ticks = clock.due(midnight_now, {schedule_midnight: time(0, 0)})
    assert ticks == [Tick("00:00", schedule_midnight)]
    await fire()
    async with sessions() as session:
        for card_id in ids:
            assert (await session.get(Card, card_id)).effective_stage == "today"
        for card_id, stage in excluded.items():
            assert (await session.get(Card, card_id)).effective_stage == stage
        cues = list(await session.scalars(select(Cue)))
        assert len(cues) == 1
        assert cues[0].hook == SCHEDULE_PLAN_HOOK.name
        request = await schedule_plan_request(session, cues[0].payload)
        for title in ("Dentist", "Call mom", "Water", "Stretch"):
            assert title in request
        for title in ("Tomorrow", "Yesterday", "Already in Today", "Paid", "Shelved", "Future quota", "Expired quota", "No Schedule"):
            assert title not in request
        assert today.isoformat() in request
        assert "already moved them into Today" in request
        assert "do not ask for confirmation" in request
        assert await schedule_plan_request(session, []) is None

    await fire()
    async with sessions() as session:
        assert len(list(await session.scalars(select(Cue)))) == 1


@pytest.mark.parametrize("after_restart", [False, True])
async def test_pl_hardtime_021_expired_sprint_closes_before_moving_today(
    sessions, monkeypatch, after_restart
):
    """PL-HARDTIME-021 — tests/brd/planning.feature"""
    tz = ZoneInfo("Europe/Istanbul")
    async with sessions() as session:
        await create_card(session, kind="action", title="Planned", stage="sprint")
        sprint = await start_sprint(session, success_criteria="Ship v2")
        today = sprint.planned_end_date + timedelta(days=1)
        now = datetime.combine(today, time(6 if after_restart else 0, 0), tzinfo=tz)
        action = await create_card(
            session, kind="action", title="Today's work", stage="backlog",
            schedule=f"{today:%d.%m.%Y} 00:00",
        )
        sprint_id, card_id = sprint.id, action.id
        await session.commit()

    monkeypatch.setattr("safwa.features.cards.hooks.utcnow", lambda: now)
    monkeypatch.setattr("safwa.features.planning.use_cases.utcnow", lambda: now)
    hooks = HookRegistry.of(
        tuple(hook for hook in REGISTRY.hooks.specs if hook in (
            SCHEDULE_PLAN_HOOK, SPRINT_EXPIRY_HOOK,
        )),
        owners=frozenset({"cards", "planning"}), policy=hook_switched_on,
    )
    if after_restart:
        await hand_on_start(hooks, sessions, resources=None)
    else:
        poll = TickPoll(
            hooks, sessions, resources=None, timezone=tz.key, now=now - timedelta(seconds=1),
        )
        await poll.look(now)

    async with sessions() as session:
        assert (await session.get(Workspace, 1)).active_sprint_id is None
        assert (await session.get(Sprint, sprint_id)).status == "finished"
        assert (await session.get(Card, card_id)).effective_stage == "today"
        assert await session.scalar(select(SprintCommitment).where(
            SprintCommitment.sprint_id == sprint_id, SprintCommitment.card_id == card_id,
        )) is None
        assert len(list(await session.scalars(select(Cue)))) == 1
