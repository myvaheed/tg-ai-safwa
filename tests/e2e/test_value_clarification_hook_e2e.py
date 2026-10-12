from __future__ import annotations

from types import SimpleNamespace

import pytest
from advisor_e2e_helpers import mutation_turn, route_turn
from sqlalchemy import select

from safwa.bootstrap.modules import PROPOSALS
from safwa.features.values.model import Value
from safwa.features.values.value_review import VALUE_CLARIFICATION_HOOK, ValueReviewer
from tg_agent_shell.cues.background import tick
from tg_agent_shell.cues.initiatives import bind_committed
from tg_agent_shell.cues.model import Cue
from tg_agent_shell.hooks.registry import HookRegistry
from tg_agent_shell.proposals.use_cases import approve_proposal

pytestmark = pytest.mark.e2e

ADVICE = "A means. Ask what it is for, and again, until a direction such as Security or Freedom."


async def test_saved_mutator_value_is_reviewed_and_its_description_written_asks_nothing_more(
    e2e_harness,
):
    """VL-CLARIFY-022 — tests/brd/values.feature"""
    sessions = e2e_harness.sessions
    calls = []

    async def complete(request):
        calls.append(request)
        return mutation_turn(("unclear", {"advice": ADVICE}))

    registry = HookRegistry.of((VALUE_CLARIFICATION_HOOK,), owners=frozenset({"values"}))
    sink = bind_committed(sessions, registry, resources=SimpleNamespace(
        value_reviewer=ValueReviewer(SimpleNamespace(complete=complete))
    ))
    try:
        advisor, _ = e2e_harness.advisor([
            route_turn("workspace_mutator"),
            mutation_turn(("value", {"mode": "create", "name": "Money"})),
            "Proposed the Value.",
        ])
        outcome = await advisor.handle("Add a Value: money")
        assert outcome.proposal_id is not None
        await sink.drain()
        assert calls == []
        async with sessions() as session:
            await approve_proposal(session, advisor.reviews, PROPOSALS, outcome.proposal_id)
            await session.commit()
            value_id = await session.scalar(select(Value.id).where(Value.name == "Money"))
        await sink.drain()
        async with sessions() as session:
            cues = list(await session.scalars(select(Cue)))
        assert len(calls) == 1
        assert [cue.hook for cue in cues] == [VALUE_CLARIFICATION_HOOK.name]

        reply = "What is «Money» for you? Security, perhaps, or Freedom?"
        advisor, _ = e2e_harness.advisor([
            mutation_turn(("query_data", {
                "sql": f"SELECT id, name, description FROM ai_values WHERE id={value_id}"
            }), content=""),
            reply,
        ])
        said = []

        async def speak(event_id, text, shown=(), passing=None):
            assert f"- [Money](value:{value_id}): {ADVICE}" in text
            said.append((await advisor.handle(text)).message)
            return True

        async def free():
            return True

        async def delivered(event_id):
            return False

        async def prepare(hook, items):
            return await registry.prepare(sessions, hook, items)

        assert await tick(sessions, gate=free, speak=speak, delivered=delivered, prepare=prepare)
        assert said == [reply]
        async with sessions() as session:
            assert list(await session.scalars(select(Cue))) == []

        advisor, _ = e2e_harness.advisor([
            route_turn("workspace_mutator"),
            mutation_turn(("value", {
                "mode": "update", "id": value_id, "description": "Savings that keep the family safe",
            })),
            "Proposed the description.",
        ])
        outcome = await advisor.handle("Security: savings that keep the family safe")
        async with sessions() as session:
            await approve_proposal(session, advisor.reviews, PROPOSALS, outcome.proposal_id)
            await session.commit()
            value = await session.get(Value, value_id)
            assert value.description == "Savings that keep the family safe"
        await sink.drain()
        assert len(calls) == 1
    finally:
        await sink.close()
