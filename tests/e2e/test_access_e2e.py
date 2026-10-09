"""Real hook ticks, Cue turns, storage and unlock; only remote boundaries are fake."""

from __future__ import annotations

from datetime import timedelta

import pytest
from advisor_e2e_helpers import mutation_turn, route_turn
from sqlalchemy import select
from telegram_fakes import QueueTestMessage
from test_access import incoming, keep, protected_services, set_word

from safwa.features.home.hooks import HOME_HOOK, HOME_LOOK_EVERY
from safwa.features.profile.model import UserProfile
from tg_agent_shell.access.model import DeferredDelivery
from tg_agent_shell.cues.background import tick
from tg_agent_shell.cues.initiatives import TickPoll
from tg_agent_shell.cues.model import Cue
from tg_agent_shell.cues.queue import add_cue
from tg_agent_shell.cues.runtime import CueRuntime, tick_chat
from tg_agent_shell.foundation.clock import utcnow
from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.hooks.registry import HookRegistry

pytestmark = pytest.mark.e2e


async def queued_turn(sessions, runtime):
    return await tick(
        sessions,
        gate=runtime.can_speak,
        speak=runtime.speak,
        delivered=runtime.delivered,
        release=runtime.release,
        prepare=runtime.prepare,
    )


async def enqueue(sessions, text):
    async with sessions() as session:
        await add_cue(session, text=text)
        await session.commit()


async def test_auto_clear_prepares_new_hook_answers_while_locked_then_restores_them(
    e2e_harness, monkeypatch
):
    """HM-LOCK-015 — tests/brd/home.feature"""
    sessions = e2e_harness.sessions
    await set_word(sessions, "🔑")
    services = protected_services(sessions)
    services.root, provider = e2e_harness.advisor(["Prepared hook answer."], plan_required=False)
    anchor = QueueTestMessage(answer_as_new=True)
    await keep(sessions, 1, "Old user words", MessageKind.DIALOGUE_USER)
    await keep(sessions, 2, "Unanswered old hook")
    moment = utcnow()
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
    await enqueue(sessions, "A scheduled hook asks a question.")
    assert await queued_turn(sessions, runtime)
    assert len(provider.calls) == 1 and anchor.rendered == []
    async with sessions() as session:
        assert list(await session.scalars(select(Cue))) == []
        assert len(list(await session.scalars(select(DeferredDelivery)))) == 2
    await services.access.intercept(incoming(anchor, "wrong"))
    await services.access.intercept(incoming(anchor, "🔑", 4000))
    assert not services.access.blocked
    assert anchor.rendered[-3:-1] == ["Unanswered old hook", "Prepared hook answer."]
    assert "🏠" in anchor.rendered[-1] and len(provider.calls) == 1
    services.owner_acted_at = utcnow() - timedelta(hours=1)
    await poll.look(moment + HOME_LOOK_EVERY * 2)
    assert services.access.blocked


async def test_a_locked_cue_discards_a_proposal_and_stores_the_continuation(
    e2e_harness, monkeypatch
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
    await enqueue(sessions, "Set About me to Must not be saved.")
    assert await queued_turn(sessions, runtime)
    assert anchor.rendered == [] and not services.root.reviews.busy
    async with sessions() as session:
        assert (await session.get(UserProfile, 1)).about_me == ""
        assert list(await session.scalars(select(Cue))) == []
    assert any("discarded" in str(call) for call in provider.calls)
    await services.access.intercept(incoming(anchor, "x"))
    assert "No changes were saved." in anchor.rendered[-2]
    assert "🏠" in anchor.rendered[-1]
