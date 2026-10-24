"""Blueprint through Safwa's own chat: the Advisor opens it, it asks over several messages,
its plan comes back as one message, and "Create" has the workspace propose each line.

Real storage, real history and Safwa's own registry; only the model is scripted.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

import pytest
from agent_turns import mutation_turn, route_turn
from review_e2e_helpers import on_screen, resolve_queued_proposal
from sqlalchemy import select
from telegram_fakes import QueueTestMessage
from ui_harness import history_source, services_for

from conftest import ScriptedProvider
from llm_gateway import CompletionRequest, CompletionTurn, ToolCall
from safwa.features.cards.model import Card, CardParent
from safwa.features.values.model import CardValue
from safwa.features.values.use_cases import create_value
from tg_agent_shell.ai.messages import OPENED_FROM
from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.history import Subsession, current_subsession
from tg_agent_shell.telegram.dialogue import run_dialogue_turn
from tg_agent_shell.telegram.subsessions import EXIT_HINT

pytestmark = pytest.mark.e2e

OWNER_ID = 42
TITLE = "📐 Blueprint"
GOAL = "Run a half marathon"
DONE = "Done when I finish 21.1 km at an official race."
ACTION = "Run 3 km"


def call(name: str, arguments: dict[str, object]) -> CompletionTurn:
    return CompletionTurn(
        content="",
        tool_calls=(ToolCall(id=name, name=name, arguments_json=json.dumps(arguments)),),
    )


def plan(health: int) -> str:
    return (
        f"🎯 **{GOAL}** — new Goal · Note: {DONE} · Values: [Health](value:{health})\n"
        f"⭐️ **{ACTION}** — new Action, under **{GOAL}**\n"
        'Say "Create" to create it.'
    )


class LaterProvider(ScriptedProvider):
    """A script whose turn may be written only when it is asked for: an id a Save gives."""

    async def complete(self, request: CompletionRequest) -> CompletionTurn:
        if self.responses and callable(self.responses[0]):
            self.responses[0] = await self.responses[0]()
        return await super().complete(request)


class Chat:
    """The owner's private chat with Safwa: each message newer than all before it."""

    def __init__(self, services) -> None:
        self.services = services
        self.message = QueueTestMessage(owner_id=OWNER_ID, is_bot=False, answer_as_new=True)
        # A private chat is the owner's own: Telegram gives it the owner's id.
        self.message.chat.id = OWNER_ID

    @property
    def drawn(self) -> list[str]:
        return self.message.bot.drawn

    async def say(self, text: str) -> None:
        self.message.message_id = 1 + max(
            [self.message.message_id, *(sent.message_id for sent in self.message.sent)]
        )
        self.message.text = text
        await self.services.chat.keep(self.message, kind=MessageKind.DIALOGUE_USER.value)
        await run_dialogue_turn(self.message, self.services, text, self.message.message_id)


async def started(e2e_harness, responses: list[CompletionTurn | Callable[[], Awaitable]]):
    history = history_source(e2e_harness.sessions)
    advisor, provider = e2e_harness.advisor(
        responses,
        subagents=(
            e2e_harness.subagent("workspace_mutator"),
            e2e_harness.subagent("blueprint", history=history),
        ),
        provider_factory=LaterProvider,
    )
    services = services_for(e2e_harness.sessions, root=advisor)
    services.history = history
    return Chat(services), provider


async def health_value(e2e_harness) -> int:
    async with e2e_harness.sessions() as session:
        health = await create_value(session, "Health", "I sleep 7 hours and walk every day.")
        await session.commit()
        return health.id


def offered(provider, request: int) -> set[str]:
    return {tool["function"]["name"] for tool in provider.options[request]["tools"]}


def said(provider, request: int) -> str:
    return json.dumps(provider.calls[request], ensure_ascii=False)


async def test_the_advisor_opens_blueprint_and_blueprint_answers_from_the_conversation(e2e_harness):
    """BP-ENTER-001 — tests/brd/blueprint.feature"""
    chat, provider = await started(
        e2e_harness,
        [
            call("sub", {"name": "blueprint"}),
            call("get_more_about", {"topic": "goals"}),
            CompletionTurn(content="Which Value does it serve?"),
            CompletionTurn(content="What result shows it is done?"),
        ],
    )

    await chat.say("I want a goal about running")
    assert chat.drawn[-2:] == [f"{TITLE}\n{EXIT_HINT}", "Which Value does it serve?"]
    await chat.say("Health")
    assert chat.drawn[-1] == "What result shows it is done?"

    async with e2e_harness.sessions() as session:
        assert (await current_subsession(session, OWNER_ID)).kind == "blueprint"
    assert "sub" in offered(provider, 0)
    # Blueprint read the conversation it was opened from, in its own voice, and the method.
    assert OPENED_FROM in said(provider, 1) and "I want a goal about running" in said(provider, 1)
    assert "The Advisor handed this conversation to you." in said(provider, 1)
    assert offered(provider, 1) == {"query_data", "get_more_about", "finish"}
    assert "A clear Goal:" in said(provider, 2)
    # Its second answer reads its own stretch; the Advisor read none of it.
    assert '"role": "assistant", "content": "Which Value does it serve?"' in said(provider, 3)


async def test_an_agreed_plan_comes_back_as_one_message_and_the_stretch_leaves(e2e_harness):
    """BP-PLAN-004 — tests/brd/blueprint.feature"""
    health = await health_value(e2e_harness)
    chat, provider = await started(
        e2e_harness,
        [
            call("sub", {"name": "blueprint"}),
            CompletionTurn(content="Which Value does it serve?"),
            CompletionTurn(content=f"{plan(health)}\nWhat should change?"),
            call("finish", {"result": plan(health)}),
            CompletionTurn(content="Shall I create it?"),
        ],
    )

    await chat.say("I want a goal about running")
    await chat.say("Health, a half marathon")
    await chat.say("Yes")

    async with e2e_harness.sessions() as session:
        closed = await session.scalar(select(Subsession))
        assert await current_subsession(session, OWNER_ID) is None
    assert closed.result == plan(health)
    stretch = set(range(closed.after_message_id + 1, closed.through_message_id + 1))
    assert stretch <= set(chat.message.bot.deleted)
    result = chat.drawn[-1]
    assert result.startswith(f"<b>{TITLE}</b>\n🎯 <b>{GOAL}</b>")
    assert 'Say "Create" to create it.' in result or "Say &quot;Create&quot; to create it." in result
    # The conversation is the owner's first words and the plan, nothing of the talk.
    words = [entry.text for entry in await chat.services.history.recent(OWNER_ID)]
    assert words[0] == "I want a goal about running"
    assert len(words) == 2 and GOAL in words[1]
    assert "What should change?" not in " ".join(words)

    # The next message goes to the Advisor, which reads the plan and none of the talk.
    await chat.say("Looks good")
    assert chat.drawn[-1] == "Shall I create it?"
    assert GOAL in said(provider, 4) and "What should change?" not in said(provider, 4)


async def test_create_after_a_plan_proposes_each_line_and_a_child_under_the_saved_goal(e2e_harness):
    """BP-CREATE-005 — tests/brd/blueprint.feature"""
    health = await health_value(e2e_harness)

    async def under_the_saved_goal() -> CompletionTurn:
        async with e2e_harness.sessions() as session:
            goal = await session.scalar(select(Card).where(Card.title == GOAL))
        return mutation_turn(
            ("action", {"mode": "create", "title": ACTION, "parent": goal.id}), prefix="again"
        )

    chat, provider = await started(
        e2e_harness,
        [
            call("sub", {"name": "blueprint"}),
            CompletionTurn(content="Which Value does it serve?"),
            call("finish", {"result": plan(health)}),
            route_turn("workspace_mutator"),
            # The Action names a Goal that is not saved yet, so only the Goal reaches a screen.
            mutation_turn(
                ("goal", {"mode": "create", "title": GOAL, "note": DONE, "values": ["Health"]}),
                ("action", {"mode": "create", "title": ACTION, "parent": GOAL}),
            ),
            under_the_saved_goal,
            CompletionTurn(content="Saved the Goal and the Action under it."),
            CompletionTurn(content="Both are created."),
        ],
    )

    await chat.say("I want a goal about running")
    await chat.say("Health. That is the plan.")
    await chat.say("Create")

    # The workspace read the plan in the conversation, as the chat shows it: the bold is
    # gone and the link is a citation again. Nothing is saved before a Save.
    assert f"new Action, under {GOAL}" in said(provider, 4)
    assert f"[Health](value:{health})" in said(provider, 4)
    async with e2e_harness.sessions() as session:
        assert await session.scalar(select(Card).where(Card.title == GOAL)) is None
    goal_screen = on_screen(e2e_harness)
    await resolve_queued_proposal(
        e2e_harness, chat.services, chat.message, goal_screen, "proposal_approve"
    )
    # The refused Action came back with the hint to wait for the Goal's id.
    assert "wait for that result and use the ID it returns" in said(provider, 5)
    action_screen = on_screen(e2e_harness)
    assert action_screen not in (None, goal_screen)
    await resolve_queued_proposal(
        e2e_harness, chat.services, chat.message, action_screen, "proposal_approve"
    )

    async with e2e_harness.sessions() as session:
        goal = await session.scalar(select(Card).where(Card.title == GOAL))
        action = await session.scalar(select(Card).where(Card.title == ACTION))
        parents = list(
            await session.scalars(
                select(CardParent.parent_id).where(CardParent.card_id == action.id)
            )
        )
        values = list(
            await session.scalars(select(CardValue.value_id).where(CardValue.card_id == goal.id))
        )
    assert goal.note == DONE
    assert parents == [goal.id]
    assert values == [health]
    assert chat.drawn[-1].endswith("Both are created.")
