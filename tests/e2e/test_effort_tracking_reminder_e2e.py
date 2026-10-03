from __future__ import annotations

from types import SimpleNamespace

import pytest
from advisor_e2e_helpers import create_manual_card, mutation_turn, route_turn
from sqlalchemy import select

from safwa.bootstrap.modules import PROPOSALS, REGISTRY
from safwa.features.cards.model import Card
from safwa.features.cards.use_cases import finish_action
from safwa.features.onboarding.hooks import ONBOARDING_HOOK
from safwa.features.profile.api import EFFORT_TRACKING_REMINDER, set_hook_switch
from safwa.features.profile.model import ProfileField
from safwa.features.profile.use_cases import set_profile_field
from tg_agent_shell.cues.background import tick
from tg_agent_shell.cues.initiatives import bind_committed
from tg_agent_shell.cues.model import Cue
from tg_agent_shell.cues.runtime import CueRuntime
from tg_agent_shell.proposals.use_cases import approve_proposal

pytestmark = pytest.mark.e2e


async def _pending(sessions):
    async with sessions() as session:
        return [(cue.hook, cue.payload) for cue in await session.scalars(select(Cue))]


@pytest.mark.parametrize("completion", ["manual", "proposal"])
async def test_done_actions_are_asked_about_together_and_the_answer_updates_the_finished_repeat(
    e2e_harness, completion,
):
    """CD-EFFORT-044 — tests/brd/cards.feature"""
    sessions = e2e_harness.sessions
    sink = bind_committed(sessions, REGISTRY.hooks)
    async with sessions() as session:
        await set_hook_switch(session, ONBOARDING_HOOK.name, on=False)
        await set_profile_field(session, ProfileField.EFFORT_TRACKING, True)
        run = await create_manual_card(
            session, title="Run", effort_points=None, schedule="after completion"
        )
        report = await create_manual_card(session, title="Write the report", effort_points=None)
        await session.commit()
        run_id, report_id = run.id, report.id
    await sink.drain()
    assert await _pending(sessions) == []
    async with sessions() as session:
        await finish_action(session, run_id)
        await session.rollback()
    await sink.drain()
    assert await _pending(sessions) == []

    if completion == "manual":
        async with sessions() as session:
            await finish_action(session, run_id)
            await session.commit()
    else:
        advisor, _ = e2e_harness.advisor([
            route_turn("workspace_mutator"),
            mutation_turn(("card", {"mode": "complete", "id": run_id})),
            "Proposed finishing the Action.",
        ])
        outcome = await advisor.handle("I finished running")
        assert outcome.proposal_id is not None
        await sink.drain()
        assert await _pending(sessions) == []
        async with sessions() as session:
            await approve_proposal(session, advisor.reviews, PROPOSALS, outcome.proposal_id)
            await session.commit()
    async with sessions() as session:
        await finish_action(session, report_id)
        await session.commit()
        successor_id = await session.scalar(select(Card.id).where(Card.source_instance_id == run_id))
    await sink.drain()
    assert await _pending(sessions) == [
        (EFFORT_TRACKING_REMINDER, [run_id]),
        (EFFORT_TRACKING_REMINDER, [report_id]),
    ]

    said = []

    async def speak(event_id, text, shown=()):
        said.append(text)
        return True

    async def gate():
        return True

    runtime = CueRuntime(
        SimpleNamespace(sessions=sessions, hooks=REGISTRY.hooks), bot=None, owner_id=42
    )
    assert await tick(
        sessions, gate=gate, speak=speak, delivered=runtime.delivered, prepare=runtime.prepare
    )
    assert len(said) == 1
    assert f"#{run_id} «Run»" in said[0] and f"#{report_id} «Write the report»" in said[0]
    assert f"#{successor_id}" not in said[0]
    assert await _pending(sessions) == []

    advisor, _ = e2e_harness.advisor([
        route_turn("workspace_mutator"),
        mutation_turn(("card", {"mode": "update", "id": run_id, "effort_points": 3, "tracked_mins": 30})),
        "Proposed the effort and time for the finished run.",
    ])
    outcome = await advisor.handle("The finished run was 3 EP and took 30 minutes")
    assert outcome.proposal_id is not None
    async with sessions() as session:
        await approve_proposal(session, advisor.reviews, PROPOSALS, outcome.proposal_id)
        await session.commit()
        finished = await session.get(Card, run_id)
        successor = await session.get(Card, successor_id)
        assert (finished.effort_points, finished.tracked_mins) == (3, 30)
        assert (successor.effort_points, successor.tracked_mins) == (None, None)
    await sink.drain()
    assert await _pending(sessions) == []


@pytest.mark.parametrize("switch", ["effort_points", "reminder"])
async def test_switching_off_suppresses_pending_questions_and_new_completions(e2e_harness, switch):
    """CD-EFFORT-044 — tests/brd/cards.feature"""
    sessions = e2e_harness.sessions
    sink = bind_committed(sessions, REGISTRY.hooks)
    async with sessions() as session:
        await set_hook_switch(session, ONBOARDING_HOOK.name, on=False)
        await set_profile_field(session, ProfileField.EFFORT_TRACKING, True)
        card = await create_manual_card(session, title="Run", effort_points=None)
        await finish_action(session, card.id)
        await session.commit()
    await sink.drain()
    assert await _pending(sessions) == [(EFFORT_TRACKING_REMINDER, [card.id])]
    async with sessions() as session:
        if switch == "effort_points":
            await set_profile_field(session, ProfileField.EFFORT_TRACKING, False)
        else:
            await set_hook_switch(session, EFFORT_TRACKING_REMINDER, on=False)
        another = await create_manual_card(session, title="Read", effort_points=None)
        await finish_action(session, another.id)
        await session.commit()
    await sink.drain()
    said = []

    async def speak(event_id, text, shown=()):
        said.append(text)
        return True

    async def gate():
        return True

    runtime = CueRuntime(
        SimpleNamespace(sessions=sessions, hooks=REGISTRY.hooks), bot=None, owner_id=42
    )
    await tick(sessions, gate=gate, speak=speak, delivered=runtime.delivered, prepare=runtime.prepare)
    assert said == []
    assert await _pending(sessions) == []
