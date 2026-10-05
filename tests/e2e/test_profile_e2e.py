"""The profile subagent in a real Advisor turn: fields set in words, and switches left alone.

The real registry, the profile subagent bound from its own declaration and the real
database; only the provider is scripted.
"""

from __future__ import annotations

import json
from datetime import time

import pytest
from advisor_e2e_helpers import mutation_turn, route_turn
from agent_turns import forward_turn

from safwa.bootstrap.modules import PROPOSALS, REGISTRY, routed_prompt
from safwa.features.cards.hooks import BLOCKER_HOOK
from safwa.features.profile.agent import PROFILE_AGENT, ProfileToolInput
from safwa.features.profile.api import set_hook_switch
from safwa.features.profile.model import ProfileField, UserProfile
from safwa.foundation.workspace import Workspace
from tg_agent_shell.ai.outcome import AIOutcomeKind
from tg_agent_shell.proposals.model import BatchDecision
from tg_agent_shell.proposals.use_cases import approve_proposal
from tg_agent_shell.telegram.manifest import AgentContext

pytestmark = pytest.mark.e2e


def _tool_result(call: list[dict], name: str) -> dict:
    [result] = [
        message for message in call if message.get("role") == "tool" and message.get("name") == name
    ]
    return json.loads(str(result["content"]))


async def test_ps_ai_019_two_fields_set_in_words_are_one_screen_and_one_change_each(
    e2e_harness,
):
    """PS-AI-019 — tests/brd/profile.feature"""
    async with e2e_harness.sessions() as session:
        revision = (await session.get(Workspace, 1)).revision
    advisor, provider = e2e_harness.advisor(
        [
            route_turn("profile"),
            mutation_turn(
                (
                    "profile",
                    {"home_after_minutes": 45, "morning_time": "08:00"},
                )
            ),
        ],
        subagents=(e2e_harness.subagent("profile"),),
    )

    screen = await advisor.handle("Home after 45 minutes, and mornings at 8")

    assert screen.kind is AIOutcomeKind.PROPOSAL
    async with e2e_harness.sessions() as session:
        drawn = await PROPOSALS.presenter("profile").screen(
            session, advisor.reviews.proposal(screen.proposal_id).changes
        )
    assert drawn.diffs == (
        "• Morning time: 09:00 → 08:00",
        "• Home after: 30 min → 45 min",
    )

    async with e2e_harness.sessions() as session:
        await approve_proposal(session, advisor.reviews, PROPOSALS, screen.proposal_id)
        await session.commit()
        profile = await session.get(UserProfile, 1)
        after = (await session.get(Workspace, 1)).revision
    assert (profile.home_after_minutes, profile.morning_time) == (45, time(8, 0))
    assert after == revision + 2
    provider.responses.extend(["Saved both.", "Anything else?"])
    await advisor.resolve_approval(
        screen.proposal_id, decision=BatchDecision.APPROVED, result={"affected_ids": []}
    )

    # Out of range: refused before any screen, with the range the Profile screen gives.
    advisor, provider = e2e_harness.advisor(
        [
            route_turn("profile"),
            mutation_turn(("profile", {"home_after_minutes": 2})),
            "Home after is 5 to 1440 minutes.",
            "How many minutes then?",
        ],
        subagents=(e2e_harness.subagent("profile"),),
    )

    outcome = await advisor.handle("Home after 2 minutes")

    assert outcome.kind is AIOutcomeKind.ANSWER
    refused = _tool_result(provider.calls[2], "profile")
    assert refused["code"] == "invalid_value"
    assert "between 5 and 1440 minutes" in refused["error"]
    async with e2e_harness.sessions() as session:
        assert (await session.get(UserProfile, 1)).home_after_minutes == 45


async def test_ps_ai_020_a_switch_is_read_and_left_to_the_profile_screen(e2e_harness):
    """PS-AI-020 — tests/brd/profile.feature"""
    async with e2e_harness.sessions() as session:
        await set_hook_switch(session, BLOCKER_HOOK.name, on=False)
        await session.commit()
    profile = PROFILE_AGENT.bind(
        AgentContext(
            owner_id=42,
            timezone="Europe/Istanbul",
            query_runner=e2e_harness.runner(),
            history=None,  # type: ignore[arg-type]
            sessions=e2e_harness.sessions,
            switches=REGISTRY.hooks.agent_related,
        ),
        prompt=routed_prompt(PROFILE_AGENT),
    )
    words = "Blocker follow-up is off now. Open ⚙️ Profile → 🔔 Hooks → Blocker follow-up for its switch."
    advisor, provider = e2e_harness.advisor(
        [route_turn("profile"), words, forward_turn("profile")], subagents=(profile,)
    )

    outcome = await advisor.handle("Ask me about blocked Actions again")

    assert outcome.kind is AIOutcomeKind.ANSWER
    assert outcome.message == words
    assert "- Blocker follow-up: off" in str(provider.calls[1][-1]["content"])
    # The one tool it holds has the ten fields and no switch.
    assert [tool["function"]["name"] for tool in provider.options[1]["tools"]] == ["profile"]
    assert set(ProfileToolInput.model_fields) - {"mode"} == {field.value for field in ProfileField}
