from __future__ import annotations

from types import SimpleNamespace

import pytest
from advisor_e2e_helpers import mutation_turn, route_turn
from sqlalchemy import select

from safwa.bootstrap.modules import PROPOSALS, REGISTRY
from safwa.features.cards.hooks import PARENT_COMPLETION_HOOK
from safwa.features.cards.model import Card, CardStage
from safwa.features.cards.use_cases import (
    create_card,
    delete_subtree,
    finish_action,
    finish_card,
    move_card,
)
from safwa.features.onboarding.hooks import ONBOARDING_HOOK
from safwa.features.profile.api import set_hook_switch
from tg_agent_shell.cues.background import tick
from tg_agent_shell.cues.initiatives import bind_committed
from tg_agent_shell.cues.model import Cue
from tg_agent_shell.cues.runtime import CueRuntime
from tg_agent_shell.proposals.use_cases import approve_proposal

pytestmark = pytest.mark.e2e


async def pending(sessions):
    async with sessions() as session:
        return [(cue.hook, cue.payload) for cue in await session.scalars(select(Cue))]


async def test_finished_actions_ask_once_and_the_owners_choice_closes_only_the_goal(e2e_harness):
    """CD-CLOSE-042 — tests/brd/cards.feature"""
    sessions = e2e_harness.sessions
    sink = bind_committed(sessions, REGISTRY.hooks)
    async with sessions() as session:
        await set_hook_switch(session, ONBOARDING_HOOK.name, on=False)
        goal = await create_card(session, kind="goal", title="Learn Spanish")
        subgoal = await create_card(session, kind="goal", title="Read a book", parent_id=goal.id)
        first = await create_card(
            session, kind="action", title="Buy the book", effort_points=1, parent_id=subgoal.id
        )
        last = await create_card(
            session, kind="action", title="Read it", effort_points=2, parent_id=subgoal.id
        )
        await session.commit()
        goal_id, subgoal_id, first_id, last_id = goal.id, subgoal.id, first.id, last.id
    await sink.drain()

    async with sessions() as session:
        await finish_action(session, first_id)
        await session.commit()
    await sink.drain()
    assert await pending(sessions) == []

    advisor, _ = e2e_harness.advisor(
        [
            route_turn("workspace_mutator"),
            mutation_turn(("action", {"mode": "complete", "id": last_id})),
            "Finished the Action.",
        ]
    )
    outcome = await advisor.handle("I finished reading it")
    assert outcome.proposal_id is not None
    await sink.drain()
    assert await pending(sessions) == []
    async with sessions() as session:
        await approve_proposal(session, advisor.reviews, PROPOSALS, outcome.proposal_id)
        await session.commit()
        assert (await session.get(Card, goal_id)).effective_stage != "done"
        assert (await session.get(Card, subgoal_id)).effective_stage != "done"
    await sink.drain()
    assert await pending(sessions) == [
        (PARENT_COMPLETION_HOOK.name, [subgoal_id]),
        (PARENT_COMPLETION_HOOK.name, [goal_id]),
    ]

    said = []

    async def speak(event_id, text, shown=(), passing=None):
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
    assert f"#{goal_id} «Learn Spanish»" in said[0]
    assert f"#{subgoal_id} «Read a book»" in said[0]
    assert "close each too or create a new Action" in said[0]
    assert "Change nothing until they answer" in said[0]
    assert await pending(sessions) == []
    assert not await tick(
        sessions, gate=gate, speak=speak, delivered=runtime.delivered, prepare=runtime.prepare
    )

    advisor, _ = e2e_harness.advisor(
        [
            route_turn("workspace_mutator"),
            mutation_turn(("goal", {"mode": "complete", "id": goal_id})),
            "Proposed closing the Goal.",
        ]
    )
    outcome = await advisor.handle("Close the Goal too")
    async with sessions() as session:
        assert (await session.get(Card, goal_id)).effective_stage != "done"
        await approve_proposal(session, advisor.reviews, PROPOSALS, outcome.proposal_id)
        await session.commit()
        assert (await session.get(Card, goal_id)).effective_stage == "done"
        assert (await session.get(Card, subgoal_id)).effective_stage != "done"

    advisor, _ = e2e_harness.advisor(
        [
            route_turn("workspace_mutator"),
            mutation_turn(("goal", {"mode": "reopen", "id": goal_id})),
            "Proposed reopening the Goal.",
        ]
    )
    outcome = await advisor.handle("Reopen the Goal")
    async with sessions() as session:
        await approve_proposal(session, advisor.reviews, PROPOSALS, outcome.proposal_id)
        await session.commit()
        assert (await session.get(Card, goal_id)).effective_stage != "done"
        assert (await session.get(Card, last_id)).effective_stage == "done"
        await finish_card(session, goal_id)
        await move_card(session, last_id, CardStage.TODAY)
        await session.commit()
        assert (await session.get(Card, goal_id)).effective_stage == "today"


@pytest.mark.parametrize(
    "change", ["new_action", "reopen_action", "close_parent", "delete", "switch_off"]
)
async def test_pending_question_is_rechecked_before_delivery(e2e_harness, change):
    """CD-CLOSE-042 — tests/brd/cards.feature"""
    sessions = e2e_harness.sessions
    sink = bind_committed(sessions, REGISTRY.hooks)
    async with sessions() as session:
        await set_hook_switch(session, ONBOARDING_HOOK.name, on=False)
        goal = await create_card(session, kind="goal", title="Health")
        action = await create_card(
            session, kind="action", title="Walk", effort_points=1, parent_id=goal.id
        )
        await session.commit()
        goal_id, action_id = goal.id, action.id
    async with sessions() as session:
        await finish_action(session, action_id)
        await session.rollback()
    await sink.drain()
    assert await pending(sessions) == []
    async with sessions() as session:
        await finish_action(session, action_id)
        await session.commit()
    await sink.drain()
    assert await pending(sessions) == [(PARENT_COMPLETION_HOOK.name, [goal_id])]
    async with sessions() as session:
        if change == "new_action":
            await create_card(
                session, kind="action", title="Swim", effort_points=1, parent_id=goal_id
            )
        elif change == "reopen_action":
            await move_card(session, action_id, CardStage.BACKLOG)
        elif change == "close_parent":
            await finish_card(session, goal_id)
        elif change == "delete":
            await delete_subtree(session, goal_id)
        else:
            await set_hook_switch(session, PARENT_COMPLETION_HOOK.name, on=False)
        await session.commit()
    runtime = CueRuntime(
        SimpleNamespace(sessions=sessions, hooks=REGISTRY.hooks), bot=None, owner_id=42
    )
    assert await runtime.prepare(PARENT_COMPLETION_HOOK.name, [goal_id]) is None
    if change == "switch_off":
        async with sessions() as session:
            await move_card(session, action_id, CardStage.BACKLOG)
            await finish_action(session, action_id)
            await session.commit()
        await sink.drain()
        assert await pending(sessions) == []
