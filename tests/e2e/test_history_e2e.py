"""The next turn reads what the last one did: its calls, their results and its words.

The real Advisor, workspace mutator, review queue, rendering and kept chat; the provider is
scripted and Telegram is a fake.
"""

from __future__ import annotations

import json

import pytest
from agent_turns import mutation_turn, route_turn
from ui_harness import FakeMessage, history_source, services_for

from llm_gateway import CompletionTurn, ToolCall
from safwa.bootstrap.modules import PROPOSALS
from safwa.features.profile.model import ProfileField
from safwa.features.profile.use_cases import set_profile_field
from tg_agent_shell.ai.conversation import CLEARED_READ
from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.proposals.model import BatchDecision
from tg_agent_shell.proposals.telegram.answer import render_ai_outcome
from tg_agent_shell.proposals.use_cases import approve_proposal

pytestmark = pytest.mark.e2e

CHAT = 42
READ = "SELECT id, title FROM ai_cards WHERE title LIKE '%milk%'"
THOUGHT = {"reasoning_content": "Find the milk Card first, then hand the change on."}


def read_turn() -> CompletionTurn:
    return CompletionTurn(
        content="",
        tool_calls=(ToolCall(id="read-1", name="query_data", arguments_json=json.dumps({"sql": READ})),),
        extensions=THOUGHT,
    )


async def two_requests(e2e_harness) -> tuple[list[dict], FakeMessage, int, list[dict]]:
    """A request that read and saved a Card, answered; then the request after it.

    Returns what the model was sent for the second, where the first answer was drawn, the
    Card's id, and what the model was sent right after the first request's read.
    """
    async with e2e_harness.sessions() as session:
        # The card it proposes carries an estimate, which only Effort Points on keeps.
        await set_profile_field(session, ProfileField.EFFORT_TRACKING, True)
        await session.commit()
    services = services_for(e2e_harness.sessions)
    history = history_source(e2e_harness.sessions)
    first = FakeMessage(1, text="Add a card to buy milk", bot_message=False, chat_id=CHAT)
    await services.chat.keep(first, kind=MessageKind.DIALOGUE_USER.value)

    advisor, provider = e2e_harness.advisor(
        [
            read_turn(),
            route_turn("workspace_mutator"),
            mutation_turn(
                ("card", {"mode": "create", "kind": "action", "title": "Buy milk", "effort_points": 1})
            ),
        ]
    )
    outcome = await advisor.handle(
        first.text, source_message_id=1, dialogue=await history.dialogue(CHAT)
    )
    after_read = provider.calls[1]
    async with e2e_harness.sessions() as session:
        affected = await approve_proposal(session, advisor.reviews, PROPOSALS, outcome.proposal_id)
        await session.commit()
    provider.responses.extend(
        ["Created it.", f"Done — [Buy milk](card:{affected[0]}) is in your Backlog."]
    )
    answer = await advisor.resolve_approval(
        outcome.proposal_id, decision=BatchDecision.APPROVED, result={"affected_ids": affected}
    )
    anchor = FakeMessage(2, bot_message=False, chat_id=CHAT, answer_as_new=True)
    await render_ai_outcome(anchor, services, answer)
    second = FakeMessage(5_000, text="And eggs too", bot_message=False, chat_id=CHAT)
    await services.chat.keep(second, kind=MessageKind.DIALOGUE_USER.value)

    follow_up, provider = e2e_harness.advisor(["On it."])
    await follow_up.handle(
        second.text, source_message_id=5_000, dialogue=await history.dialogue(CHAT)
    )
    return provider.calls[0], anchor, affected[0], after_read


async def test_the_next_request_carries_the_calls_the_last_answer_made(e2e_harness):
    """TG-TOOLS-013 — tests/brd/tg_agent_shell/telegram_history.feature"""
    request, _, card, _ = await two_requests(e2e_harness)

    shape = [
        (
            message["role"],
            [call["function"]["name"] for call in message.get("tool_calls") or ()],
            message.get("name"),
        )
        for message in request[2:]
    ]
    assert shape == [
        ("assistant", ["query_data"], None),
        ("tool", [], "query_data"),
        ("assistant", ["route"], None),
        ("tool", [], "route"),
        ("assistant", [], None),
        ("user", [], None),
    ]
    assert request[-2]["content"] == f"Done — [Buy milk](card:{card}) is in your Backlog."
    assert request[-1]["content"].startswith("And eggs too")
    # The read's rows are gone and its call stays.
    assert json.loads(request[3]["content"]) == CLEARED_READ


async def test_what_the_change_did_comes_back_as_the_result_of_its_call(e2e_harness):
    """TG-RECEIPT-009 — tests/brd/tg_agent_shell/telegram_history.feature"""
    request, anchor, _, _ = await two_requests(e2e_harness)

    route = next(message for message in request if message.get("name") == "route")
    assert json.loads(route["content"])["did"] == ["✅ Saved — New Action “Buy milk” (1 EP)"]
    # The owner read the receipt above the answer; the answer's own words do not carry it.
    assert anchor.sent_messages[0].text.startswith("✅ Saved — New Action")
    assert "✅" not in request[-2]["content"]


async def test_the_reasoning_goes_back_between_calls_and_is_never_kept(e2e_harness):
    """TG-THINK-017 — tests/brd/tg_agent_shell/telegram_history.feature"""
    request, _, _, after_read = await two_requests(e2e_harness)

    read = next(message for message in after_read if message.get("tool_calls"))
    assert read["reasoning_content"] == THOUGHT["reasoning_content"]
    assert not any("reasoning_content" in message for message in request)
