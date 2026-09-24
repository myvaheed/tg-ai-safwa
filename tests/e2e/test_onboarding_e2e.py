"""The onboarding subagent in a real Advisor turn: a tip, a question, and the proposal to stop.

The real registry, the real subagents bound from their own declarations, the real review
flow and autoapproval; only the provider is scripted.
"""

from __future__ import annotations

import json

import pytest
from advisor_e2e_helpers import mutation_turn, route_turn
from sqlalchemy import select

from llm_gateway import CompletionTurn as ProviderTurn
from llm_gateway import ToolCall as ProviderToolCall
from safwa.bootstrap.modules import AUTOAPPROVALS, PROPOSALS, REGISTRY
from safwa.features.cards.hooks import BLOCKER_HOOK
from safwa.features.cards.use_cases import create_card
from safwa.features.onboarding.hooks import ONBOARDING_HOOK
from safwa.features.profile.api import hook_switched_on, set_hook_switch
from safwa.features.values.use_cases import create_value
from safwa.foundation.workspace import Workspace
from telegram_llm import DialogueMessage
from tg_agent_shell.ai.autoapproval import AutoApprovalReviewer
from tg_agent_shell.ai.outcome import AIOutcomeKind
from tg_agent_shell.cues.model import Cue
from tg_agent_shell.cues.queue import add_hook_cue
from tg_agent_shell.proposals.materialize import SHOWN_AS_IS
from tg_agent_shell.proposals.model import BatchDecision
from tg_agent_shell.proposals.use_cases import approve_proposal

pytestmark = pytest.mark.e2e

TIP = (
    "Onboarding tip: you created the Value [Health](value:1).\n"
    "Put it on a Goal in ✏️ Full editing, so the Advisor weighs the work that serves it."
)
EXPLANATION = (
    "A Check asks whether something held: “posture straight?”.\n\n"
    "- It hangs on one Card, or on none.\n"
    "- Answer it ✅ Passed or ❌ Missed.\n"
    "- Answer it ✅ Passed or ❌ Missed."
)


def review_turn(name: str, reason: str) -> ProviderTurn:
    return ProviderTurn(
        content="",
        tool_calls=(
            ProviderToolCall(
                id=f"review-{name}", name=name, arguments_json=json.dumps({"reason": reason})
            ),
        ),
    )


def route_receipts(provider) -> list[dict]:
    seen: list[dict] = []
    for call in provider.calls:
        for item in call:
            if item.get("role") == "tool" and item.get("name") == "route":
                receipt = json.loads(str(item["content"]))
                if receipt not in seen:
                    seen.append(receipt)
    return seen


def subagents(harness):
    return (harness.subagent("workspace_mutator"), harness.subagent("onboarding"))


def a_cue_turn(text: str) -> list[DialogueMessage]:
    """What a turn from the Cue queue reads: the conversation, then the request as its newest."""
    return [
        DialogueMessage(role="user", content="[User]: Add a Value called Health"),
        DialogueMessage(role="assistant", content="Saved."),
        DialogueMessage(role="user", content=text),
    ]


async def test_ob_tip_002_the_tip_opens_the_message_in_the_subagents_own_words(e2e_harness):
    """OB-TIP-002 — tests/brd/onboarding.feature"""
    async with e2e_harness.sessions() as session:
        value = await create_value(session, "Health")
        await session.commit()
    request = await REGISTRY.hooks.prepare(
        e2e_harness.sessions, ONBOARDING_HOOK.name, [["value.created", value.id]]
    )
    advisor, provider = e2e_harness.advisor(
        [route_turn("onboarding"), TIP, "Anything else for today?"],
        subagents=subagents(e2e_harness),
    )

    outcome = await advisor.handle(request, dialogue=a_cue_turn(request))

    assert outcome.kind is AIOutcomeKind.ANSWER
    assert outcome.message == f"{TIP}\n\nAnything else for today?"
    assert route_receipts(provider) == [
        {"subagent": "onboarding", "outcome": "done", "did": [], "shown": [TIP],
         "text": SHOWN_AS_IS},
    ]
    # The subagent reads the request as the newest message, reads no data, and was not made
    # to open with its one tool.
    read = "\n".join(str(message["content"]) for message in provider.calls[1])
    assert read.rstrip().endswith(f"<User>{request}</User>\n</Conversation>")
    offered = [tool["function"]["name"] for tool in provider.options[1]["tools"]]
    assert offered == ["stop_onboarding"]
    assert provider.options[1]["tool_choice"] is None


async def test_ob_tip_003_the_tip_is_its_own_block_and_the_blocker_is_still_asked(e2e_harness):
    """OB-TIP-003 — tests/brd/onboarding.feature"""
    async with e2e_harness.sessions() as session:
        card = await create_card(
            session, kind="action", title="Call the bank", effort_points=1,
            blocked=True, blocked_description="Line is busy",
        )
        await session.commit()
    blocker = await REGISTRY.hooks.prepare(e2e_harness.sessions, BLOCKER_HOOK.name, [card.id])
    tip = await REGISTRY.hooks.prepare(
        e2e_harness.sessions, ONBOARDING_HOOK.name, [["card.created", card.id]]
    )
    request = f"{blocker}\n\n{tip}"
    question = f"Shall I set a Reminder to come back to [Call the bank](card:{card.id})?"
    advisor, _ = e2e_harness.advisor(
        [route_turn("onboarding"), TIP, question], subagents=subagents(e2e_harness)
    )

    outcome = await advisor.handle(request, dialogue=a_cue_turn(request))

    assert outcome.message == f"{TIP}\n\n{question}"


async def test_ob_ask_004_a_question_is_answered_in_the_subagents_words_on_or_off(e2e_harness):
    """OB-ASK-004 — tests/brd/onboarding.feature"""
    for on in (True, False):
        async with e2e_harness.sessions() as session:
            await set_hook_switch(session, ONBOARDING_HOOK.name, on=on)
            await session.commit()
        advisor, _ = e2e_harness.advisor(
            [route_turn("onboarding"), EXPLANATION, "Want one on a Card?"],
            subagents=subagents(e2e_harness),
        )

        outcome = await advisor.handle("What is a Check?")

        # Paragraphs and the repeated line intact, and the Advisor's own words after it.
        assert outcome.message == f"{EXPLANATION}\n\nWant one on a Card?", on


async def test_ob_ask_004_explaining_and_adding_a_check_explains_once_the_screen_is_answered(
    e2e_harness,
):
    """OB-ASK-004 — tests/brd/onboarding.feature"""
    advisor, provider = e2e_harness.advisor(
        [
            route_turn("onboarding"),
            EXPLANATION,
            route_turn("workspace_mutator"),
            mutation_turn(("check", {"mode": "create", "title": "Posture straight?"})),
        ],
        subagents=subagents(e2e_harness),
    )

    screen = await advisor.handle("Explain Checks and add one about posture")

    assert screen.kind is AIOutcomeKind.PROPOSAL
    assert "Already saved" not in json.dumps(provider.calls[3], ensure_ascii=False)
    async with e2e_harness.sessions() as session:
        affected = await approve_proposal(session, advisor.reviews, PROPOSALS, screen.proposal_id)
        await session.commit()
    provider.responses.extend(["Added the Check.", "It is on your list."])
    answered = await advisor.resolve_approval(
        screen.proposal_id, decision=BatchDecision.APPROVED, result={"affected_ids": affected}
    )

    assert answered is not None and answered.message.startswith(f"{EXPLANATION}\n\n")
    assert answered.message.endswith("It is on your list.")


STOP = mutation_turn(("stop_onboarding", {}), prefix="stop", content="I turn onboarding off.")
OFF = "Onboarding is off. Turn it back on in ⚙️ Profile."


async def _owed_tip(harness) -> int:
    async with harness.sessions() as session:
        await add_hook_cue(session, hook=ONBOARDING_HOOK.name, items=[["value.created", 1]])
        await session.commit()
        return (await session.get(Workspace, 1)).revision


async def test_ob_stop_005_an_unambiguous_request_is_saved_with_no_screen(e2e_harness):
    """OB-STOP-005 — tests/brd/onboarding.feature"""
    revision = await _owed_tip(e2e_harness)
    advisor, provider = e2e_harness.advisor(
        [
            route_turn("onboarding"),
            STOP,
            review_turn("autoapprove", "The user asked to stop the onboarding."),
            OFF,
            "Done.",
        ],
        subagents=subagents(e2e_harness),
        autoapprove=True,
    )

    outcome = await advisor.handle("Stop the onboarding, I know my way around")

    assert outcome.kind is AIOutcomeKind.ANSWER
    assert outcome.message.startswith(f"{OFF}\n\n")
    assert "⚡ Auto-saved — Turn onboarding off" in outcome.message
    reviewed = json.loads(str(provider.calls[2][1]["content"]))
    assert reviewed["user_request"] == "Stop the onboarding, I know my way around"
    async with e2e_harness.sessions() as session:
        assert await hook_switched_on(session, ONBOARDING_HOOK.name) is False
        assert list(await session.scalars(select(Cue))) == []
        assert (await session.get(Workspace, 1)).revision == revision


@pytest.mark.parametrize("reviewer", ["doubts", "unreachable"])
async def test_ob_stop_005_doubt_or_no_reviewer_takes_the_screen(e2e_harness, reviewer):
    """OB-STOP-005 — tests/brd/onboarding.feature"""
    await _owed_tip(e2e_harness)
    script = [route_turn("onboarding"), STOP]
    if reviewer == "doubts":
        script.append(review_turn("require_review", "Enough for today, or for good?"))
    advisor, _ = e2e_harness.advisor(
        script, subagents=subagents(e2e_harness), autoapprove=True
    )
    if reviewer == "unreachable":

        class Unreachable:
            async def complete(self, _request):
                raise RuntimeError("the reviewer is unreachable")

        advisor.materializer.autoapproval = AutoApprovalReviewer(Unreachable(), AUTOAPPROVALS)

    outcome = await advisor.handle("Enough for today")

    assert outcome.kind is AIOutcomeKind.PROPOSAL
    async with e2e_harness.sessions() as session:
        assert await hook_switched_on(session, ONBOARDING_HOOK.name) is True
        assert [cue.hook for cue in await session.scalars(select(Cue))] == [ONBOARDING_HOOK.name]


async def test_ob_stop_005_a_turn_safwa_took_on_its_own_is_reviewed_against_its_request(
    e2e_harness,
):
    """OB-STOP-005 — tests/brd/onboarding.feature"""
    async with e2e_harness.sessions() as session:
        value = await create_value(session, "Health")
        await session.commit()
    request = await REGISTRY.hooks.prepare(
        e2e_harness.sessions, ONBOARDING_HOOK.name, [["value.created", value.id]]
    )
    assert request is not None
    advisor, provider = e2e_harness.advisor(
        [
            route_turn("onboarding"),
            STOP,
            review_turn("require_review", "Nobody asked to stop anything."),
        ],
        subagents=subagents(e2e_harness),
        autoapprove=True,
    )

    outcome = await advisor.handle(request, dialogue=a_cue_turn(request))

    # What the reviewer weighs is the request the turn was about, where no stop was asked.
    reviewed = json.loads(str(provider.calls[2][1]["content"]))
    assert reviewed["user_request"] == request
    assert outcome.kind is AIOutcomeKind.PROPOSAL
