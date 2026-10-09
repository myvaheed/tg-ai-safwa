"""Real hook ticks, Cue turns, storage and unlock; only remote boundaries are fake."""

from __future__ import annotations

from datetime import timedelta
from zoneinfo import ZoneInfo

import pytest
from advisor_e2e_helpers import create_manual_card, mutation_turn, route_turn
from sqlalchemy import select
from telegram_fakes import QueueTestMessage
from test_access import incoming, keep, protected_services, set_word

from safwa.features.home.hooks import HOME_HOOK, HOME_LOOK_EVERY
from safwa.features.profile.model import UserProfile
from safwa.features.reminders.api import resolve
from safwa.features.reminders.firing import tick as fire_reminders
from safwa.features.reminders.model import Reminder
from safwa.features.reminders.use_cases import create_reminder
from safwa.features.schedules.use_cases import set_remind
from tg_agent_shell.access.model import DeferredDelivery
from tg_agent_shell.cues.background import tick
from tg_agent_shell.cues.initiatives import TickPoll
from tg_agent_shell.cues.model import Cue
from tg_agent_shell.cues.queue import add_cue, add_hook_cue
from tg_agent_shell.cues.runtime import CueRuntime, tick_chat
from tg_agent_shell.foundation.clock import utcnow
from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.history import TelegramMessage
from tg_agent_shell.hooks.contracts import Advise, HookSpec, OnCommitted
from tg_agent_shell.hooks.registry import HookRegistry

pytestmark = pytest.mark.e2e


async def test_an_empty_protected_session_locks_again_after_the_next_quiet_period(e2e_harness):
    """HM-LOCK-015 — tests/brd/home.feature"""
    sessions = e2e_harness.sessions
    await set_word(sessions, "off")
    services = protected_services(sessions)
    anchor = QueueTestMessage(answer_as_new=True)
    moment = utcnow()
    poll = TickPoll(
        HookRegistry.of((HOME_HOOK,), owners=frozenset({"home"})), sessions,
        resources=services.features, timezone="Europe/Istanbul", now=moment,
        chat=tick_chat(services, anchor),
    )
    await poll.look(moment + HOME_LOOK_EVERY)
    assert services.access.blocked and anchor.rendered == []
    await services.access.intercept(incoming(anchor, "off"))
    assert not services.access.blocked and anchor.rendered == []
    services.owner_acted_at = utcnow() - timedelta(hours=1)
    await poll.look(moment + HOME_LOOK_EVERY * 2)
    assert services.access.blocked and anchor.rendered == []


async def queued_turn(sessions, runtime):
    return await tick(
        sessions,
        gate=runtime.can_speak,
        speak=runtime.speak,
        delivered=runtime.delivered,
        release=runtime.release,
        prepare=runtime.prepare,
        allow_hooks=runtime.allow_hooks,
    )


async def enqueue(sessions, text):
    async with sessions() as session:
        await add_cue(session, text=text)
        await session.commit()


@pytest.mark.parametrize("from_schedule", [False, True])
async def test_locked_reminders_arrive_without_links_and_hooks_wait_until_entry(
    e2e_harness, monkeypatch, from_schedule
):
    """HM-LOCK-015 — tests/brd/home.feature"""
    sessions = e2e_harness.sessions
    await set_word(sessions, "🔑")
    services = protected_services(sessions)
    moment = utcnow()
    tz = ZoneInfo("Europe/Istanbul")
    async with sessions() as session:
        card = await create_manual_card(session, title="Stretch", schedule="daily 20:00")
        if from_schedule:
            await set_remind(session, card, True)
            reminder = await session.scalar(select(Reminder))
        else:
            reminder = await create_reminder(
                session,
                instruction="Remind me to stretch.",
                schedule=resolve(now=moment, tz=tz, interval_minutes=120),
                tz=tz,
            )
        reminder.next_fire_at = moment
        await session.commit()
        card_id = card.id
    services.root, provider = e2e_harness.advisor(
        [f"Do [Stretch](card:{card_id}).", "New hook answer."], plan_required=False
    )
    prepared = []

    async def evaluate(event):
        return (1,)

    async def prepare(session, items):
        prepared.append(items)
        return "A hook asks a question after entry."

    services.hooks = HookRegistry.of(
        (
            HookSpec(
                name="test.waiting",
                owner="test",
                on=(OnCommitted("test.changed"),),
                evaluate=evaluate,
                effect=Advise(prepare),
                title="Test hook",
                description="Test hook",
            ),
        ),
        owners=frozenset({"test"}),
    )
    anchor = QueueTestMessage(answer_as_new=True)
    await keep(sessions, 1, "Old user words", MessageKind.DIALOGUE_USER)
    await keep(sessions, 2, "Unanswered old hook")
    poll = TickPoll(
        HookRegistry.of((HOME_HOOK,), owners=frozenset({"home"})),
        sessions,
        resources=services.features,
        timezone="Europe/Istanbul",
        now=moment,
        chat=tick_chat(services, anchor),
    )
    await poll.look(moment + HOME_LOOK_EVERY)
    assert services.access.blocked and anchor.rendered == []
    runtime = CueRuntime(services, anchor.bot, owner_id=42)
    monkeypatch.setattr(runtime, "_anchor", lambda: anchor)
    async with sessions() as session:
        await add_hook_cue(session, hook="test.waiting", items=[1])
        await session.commit()
    assert not await queued_turn(sessions, runtime)
    assert provider.calls == [] and prepared == []
    assert await fire_reminders(sessions, tz=tz, now=moment)
    assert await queued_turn(sessions, runtime)
    assert len(provider.calls) == 1 and prepared == []
    assert "Stretch" in anchor.rendered[-1] and "<a " not in anchor.rendered[-1]
    assert anchor.markups[-1] is None and services.access.blocked
    async with sessions() as session:
        assert [row.hook for row in await session.scalars(select(Cue))] == ["test.waiting"]
        assert len(list(await session.scalars(select(DeferredDelivery)))) == 1
        notification = await session.scalar(
            select(TelegramMessage).where(TelegramMessage.text.like("Do %"))
        )
        assert '<a href="' in notification.text
    await services.access.intercept(incoming(anchor, "wrong"))
    await services.access.intercept(incoming(anchor, "🔑", 4000))
    assert not services.access.blocked
    assert anchor.rendered[-3] == "Unanswered old hook"
    assert "Stretch" in anchor.rendered[-2] and '<a href="' in anchor.rendered[-2]
    assert anchor.rendered[-1] == "New hook answer." and len(provider.calls) == 2
    assert not any("🏠" in shown for shown in anchor.rendered)
    assert prepared == [[1]]
    async with sessions() as session:
        assert list(await session.scalars(select(Cue))) == []
    services.owner_acted_at = utcnow() - timedelta(hours=1)
    await poll.look(moment + HOME_LOOK_EVERY * 2)
    assert services.access.blocked


@pytest.mark.parametrize("source", ["reminder", "hook"])
async def test_locked_delivery_discards_a_proposal_without_an_unanswerable_review(
    e2e_harness, monkeypatch, source
):
    """TG-LOCK-031 — tests/brd/tg_agent_shell/telegram_history.feature"""
    sessions = e2e_harness.sessions
    await set_word(sessions)
    services = protected_services(sessions)
    services.root, provider = e2e_harness.advisor(
        [
            route_turn("profile"),
            mutation_turn(("profile", {"about_me": "Must not be saved"})),
            "The change was discarded.",
            "No changes were saved.",
        ],
        subagents=(e2e_harness.subagent("profile"),),
        plan_required=False,
    )
    anchor = QueueTestMessage(answer_as_new=True)
    await services.access.lock(anchor)
    runtime = CueRuntime(services, anchor.bot, owner_id=42)
    monkeypatch.setattr(runtime, "_anchor", lambda: anchor)
    if source == "reminder":
        await enqueue(sessions, "Set About me to Must not be saved.")
        assert await queued_turn(sessions, runtime)
        assert "No changes were saved." in anchor.rendered[-1]
    else:

        async def evaluate(event):
            return (1,)

        async def prepare(session, items):
            return "Set About me to Must not be saved."

        services.hooks = HookRegistry.of(
            (
                HookSpec(
                    name="test.proposal",
                    owner="test",
                    on=(OnCommitted("test.changed"),),
                    evaluate=evaluate,
                    effect=Advise(prepare),
                    title="Test proposal",
                    description="Test proposal",
                ),
            ),
            owners=frozenset({"test"}),
        )
        async with sessions() as session:
            await add_hook_cue(session, hook="test.proposal", items=[1])
            await session.commit()
        assert not await queued_turn(sessions, runtime)
        assert provider.calls == []
    assert not services.root.reviews.busy
    async with sessions() as session:
        assert (await session.get(UserProfile, 1)).about_me == ""
        assert len(list(await session.scalars(select(Cue)))) == (source == "hook")
    await services.access.intercept(incoming(anchor, "x"))
    assert "No changes were saved." in anchor.rendered[-1]
    assert any("discarded" in str(call) for call in provider.calls)
    assert not services.root.reviews.busy
