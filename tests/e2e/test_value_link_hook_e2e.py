from __future__ import annotations

from types import SimpleNamespace

import pytest
from advisor_e2e_helpers import mutation_turn, route_turn
from sqlalchemy import select

from safwa.bootstrap.modules import PROPOSALS
from safwa.features.checks.model import Check
from safwa.features.checks.use_cases import create_check
from safwa.features.values.hooks import VALUE_LINK_HOOK
from safwa.features.values.link_review import ValueLinkReviewer
from safwa.features.values.use_cases import create_value
from tg_agent_shell.cues.background import tick
from tg_agent_shell.cues.initiatives import bind_committed
from tg_agent_shell.cues.model import Cue
from tg_agent_shell.hooks.registry import HookRegistry
from tg_agent_shell.proposals.use_cases import approve_proposal

pytestmark = pytest.mark.e2e


async def test_saved_mutator_link_is_reviewed_and_note_can_be_corrected(e2e_harness):
    """VL-LINK-019 — tests/brd/values.feature"""
    sessions = e2e_harness.sessions
    calls = []

    async def complete(request):
        calls.append(request)
        return mutation_turn(("value_link_review", {"clear": False}))

    provider = SimpleNamespace(complete=complete)
    registry = HookRegistry.of(
        (VALUE_LINK_HOOK,), owners=frozenset({"values"}),
    )
    sink = bind_committed(sessions, registry, resources=SimpleNamespace(
        value_link_reviewer=ValueLinkReviewer(provider)
    ))
    try:
        async with sessions() as session:
            check = await create_check(session, title="Earned $4k?")
            value = await create_value(session, "Health", "What matters to me")
            await session.commit()
            check_id, value_id = check.id, value.id
        advisor, _ = e2e_harness.advisor([
            route_turn("workspace_mutator"),
            mutation_turn(("check", {"mode": "link", "id": check_id, "values": value_id})),
            "Proposed the link.",
        ])
        outcome = await advisor.handle("Link Health to the income Check")
        assert outcome.proposal_id is not None
        await sink.drain()
        assert calls == []
        async with sessions() as session:
            await approve_proposal(session, advisor.reviews, PROPOSALS, outcome.proposal_id)
            await session.commit()
        await sink.drain()
        async with sessions() as session:
            cues = list(await session.scalars(select(Cue)))
        assert len(calls) == 1
        assert len(cues) == 1
        assert cues[0].hook == VALUE_LINK_HOOK.name
        request = await registry.prepare(sessions, cues[0].hook, cues[0].payload)
        assert f"check:{check_id}" in request and "Note" in request

        reply = f"Clarify [{check.title}](check:{check_id}): how does it support Health?"
        advisor, _ = e2e_harness.advisor([
            mutation_turn(("query_data", {
                "sql": f"SELECT id, note, direct_values FROM ai_checks WHERE id={check_id}"
            }), content=""),
            reply,
        ])
        said = []

        async def speak(event_id, text, shown=(), passing=None):
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
            mutation_turn(("check", {
                "mode": "update", "id": check_id, "note": "Money pays for health care"
            })),
            "Proposed the explanation.",
        ])
        outcome = await advisor.handle("Set Note: Money pays for health care")
        async with sessions() as session:
            await approve_proposal(session, advisor.reviews, PROPOSALS, outcome.proposal_id)
            await session.commit()
            assert (await session.get(Check, check_id)).note == "Money pays for health care"
        await sink.drain()
        assert len(calls) == 1
    finally:
        await sink.close()
