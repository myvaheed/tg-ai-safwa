"""A subsession of the example application, through the shell and nothing else.

The root opens one with `sub`, its agent answers message after message from its own stretch
of the chat, and `finish` hands one result back to the conversation, whose window never
holds the stretch. Its agent's proposals are decided as the root's are, and whatever ends one
of its requests ends that request and not the subsession. Nothing here imports Safwa.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import pytest
from agent_turns import mutation_turn
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy import select
from telegram_fakes import QueueTestMessage
from wallet import app
from wallet.ledger.model import Entry
from wallet_harness import (
    OWNER_ID,
    READ,
    TODAY,
    Press,
    Running,
    WalletHarness,
    query_turn,
    seed_lists,
    take_a_turn,
)

from llm_gateway import CompletionTurn, ToolCall
from tg_agent_shell.access.credentials import hash_secret_word
from tg_agent_shell.access.manager import AccessManager
from tg_agent_shell.ai.messages import OPENED_FROM
from tg_agent_shell.cues.runtime import chat_is_free
from tg_agent_shell.history import Subsession, close_subsession, current_subsession
from tg_agent_shell.registry import Registry
from tg_agent_shell.telegram import callback_token_handler, dialogue, expire_review
from tg_agent_shell.telegram.commands import command_cancel
from tg_agent_shell.telegram.dialogue import ordinary_text
from tg_agent_shell.telegram.model import CallbackToken
from tg_agent_shell.telegram.subsessions import (
    EXIT_HINT,
    EXIT_RESULT,
    NOTHING_TO_EXIT,
    command_exit,
)

RESULT = "Food: 300 USD a month."


def sub_turn(name: str) -> CompletionTurn:
    """The root opening a subsession, which a script says for itself."""
    return CompletionTurn(
        content="",
        tool_calls=(ToolCall(id=f"sub-{name}", name="sub", arguments_json=json.dumps({"name": name})),),
    )


def finish_call(result: str, call_id: str = "finish") -> ToolCall:
    return ToolCall(id=call_id, name="finish", arguments_json=json.dumps({"result": result}))


def finish_turn(result: str) -> CompletionTurn:
    return CompletionTurn(content="", tool_calls=(finish_call(result),))


async def started(wallet_bot, *turns: CompletionTurn) -> Running:
    running = await wallet_bot.start(*turns)
    # A private chat is the owner's own: Telegram gives it the owner's id.
    running.message.chat.id = OWNER_ID
    return running


async def say(running: Running, text: str) -> None:
    """The owner's next message, newer than everything in the chat, as Telegram numbers it."""
    _next_message(running)
    await take_a_turn(running, text)


async def say_over(running: Running, text: str) -> None:
    """The owner's next message, typed over whatever screen is open."""
    _next_message(running)
    running.message.text = text
    await ordinary_text(running.message, running.services)


def _next_message(running: Running) -> None:
    running.message.message_id = 1 + max(
        [running.message.message_id, *(sent.message_id for sent in running.message.sent)]
    )


async def seeded(wallet_bot: WalletHarness) -> dict[str, int]:
    seeding = await wallet_bot.start()
    return await seed_lists(seeding.sessions)


def lunch(ids: dict[str, int], amount_minor: int = 1250) -> CompletionTurn:
    """The auditor proposing one ledger entry, its plan in the same response."""
    return mutation_turn(
        (
            "entry",
            {
                "mode": "create",
                "wallet_id": ids["cash"],
                "category_id": ids["food"],
                "amount_minor": amount_minor,
                "happened_on": TODAY.isoformat(),
                "note": "Lunch",
            },
        )
    )


async def decide(running: Running, action: str) -> None:
    """Press `action` on the review open now. A frozen one keeps its buttons' tokens."""
    [batch] = running.root.reviews.open_batches
    head = batch.state.head.proposal_id
    async with running.sessions() as session:
        tokens = await session.scalars(
            select(CallbackToken).where(
                CallbackToken.action == action, CallbackToken.consumed_at.is_(None)
            )
        )
        token = next(token.token for token in tokens if token.payload.get("id") == head)
    await callback_token_handler(Press(token, running.screen), running.services)


async def entries(running: Running) -> list[int]:
    async with running.sessions() as session:
        return [entry.amount_minor for entry in await session.scalars(select(Entry))]


async def open_subsession(running: Running) -> Subsession | None:
    async with running.sessions() as session:
        return await current_subsession(session, OWNER_ID)


def said(running: Running, request: int) -> str:
    """Everything one request to the model carried, as one text."""
    return json.dumps(running.provider.requests[request].messages, ensure_ascii=False)


def offered(running: Running, request: int) -> list[str]:
    return [tool["function"]["name"] for tool in running.provider.requests[request].tools]


async def test_a_subsession_answers_until_it_finishes_and_leaves_one_result(wallet_bot):
    """AG-SUB-072 — tests/brd/tg_agent_shell/agents.feature"""
    running = await started(
        wallet_bot,
        sub_turn("coach"),
        CompletionTurn(content="What is the budget for?"),
        CompletionTurn(content="How much a month?"),
        finish_turn(RESULT),
        CompletionTurn(content="Noted."),
    )

    await say(running, "Let us plan a budget")
    assert running.chat[-2:] == [f"💬 Budget talk\n{EXIT_HINT}", "What is the budget for?"]
    await say(running, "Food")
    assert running.chat[-1] == "How much a month?"
    await say(running, "300 a month")

    async with running.sessions() as session:
        closed = await session.scalar(select(Subsession))
        assert await current_subsession(session, OWNER_ID) is None
    assert closed.result == RESULT
    assert running.chat[-1] == f"<b>💬 Budget talk</b>\n{RESULT}"
    # The whole stretch left the chat: the frame, both sides of the talk, every turn notice.
    stretch = set(range(closed.after_message_id + 1, closed.through_message_id + 1))
    assert stretch <= set(running.message.bot.deleted)
    assert [entry.text for entry in await running.services.history.recent(OWNER_ID)] == [
        "Let us plan a budget", f"💬 Budget talk\n{RESULT}"
    ]

    await say(running, "Thanks")
    assert running.chat[-1] == "Noted."

    # The root opened it; the coach read the conversation it was opened from as data, then
    # its own stretch in its own roles; the root reads the result and nothing of the talk.
    assert "sub" in offered(running, 0) and offered(running, 1) == ["finish"]
    assert OPENED_FROM in said(running, 1) and "Let us plan a budget" in said(running, 1)
    assert '"role": "assistant", "content": "What is the budget for?"' in said(running, 2)
    assert RESULT in said(running, 4)
    assert "How much a month?" not in said(running, 4)
    # Every answer is one request: nothing was asked twice.
    assert len(running.provider.requests) == 5


async def test_finish_shares_its_response_with_nothing(wallet_bot):
    """AG-SUB-072 — tests/brd/tg_agent_shell/agents.feature"""
    running = await started(
        wallet_bot,
        sub_turn("coach"),
        CompletionTurn(content="What is the budget for?"),
        CompletionTurn(content="", tool_calls=(finish_call(RESULT), finish_call(RESULT, "again"))),
        CompletionTurn(content="Anything else?"),
    )

    await say(running, "Let us plan a budget")
    await say(running, "That is all")

    assert running.chat[-1] == "Anything else?"
    assert "finish_is_not_shared" in said(running, 3)
    async with running.sessions() as session:
        assert await current_subsession(session, OWNER_ID) is not None


@pytest.mark.parametrize(
    ("action", "written", "result"),
    [
        ("proposal_approve", [1250], "Lunch, 12.50 from Cash, is written down."),
        ("proposal_reject", [], "Nothing was written down."),
    ],
)
async def test_a_subsession_s_proposal_is_decided_and_its_agent_finishes_after(
    wallet_bot, action, written, result
):
    """AG-SUB-073 — tests/brd/tg_agent_shell/agents.feature"""
    ids = await seeded(wallet_bot)
    running = await started(
        wallet_bot,
        sub_turn("auditor"),
        CompletionTurn(content="Which entry?"),
        query_turn(READ),
        lunch(ids),
        finish_turn(result),
    )

    await say(running, "Let us go through the ledger")
    await say(running, "Add lunch, 12.50 from Cash")
    assert "Ledger entry" in running.chat[-1]
    await decide(running, action)

    assert await entries(running) == written
    assert await open_subsession(running) is None
    assert running.chat[-1] == f"<b>🧾 Ledger review</b>\n{result}"
    assert [entry.text for entry in await running.services.history.recent(OWNER_ID)] == [
        "Let us go through the ledger", f"🧾 Ledger review\n{result}"
    ]
    # The decision resumed the same request, which finished: nothing was asked twice.
    assert len(running.provider.requests) == 5


async def test_the_same_change_in_a_later_request_of_a_subsession_is_proposed_again(wallet_bot):
    """PR-RESULT-011 — tests/brd/tg_agent_shell/proposals.feature"""
    ids = await seeded(wallet_bot)
    running = await started(
        wallet_bot,
        sub_turn("auditor"),
        CompletionTurn(content="Which entry?"),
        lunch(ids),
        CompletionTurn(content="Lunch is written down."),
        lunch(ids),
        CompletionTurn(content="The second lunch is written down."),
    )
    await say(running, "Let us go through the ledger")
    await say(running, "Add lunch, 12.50 from Cash")
    await decide(running, "proposal_approve")

    # A second lunch is a change of its own: the Save of the first was another request's.
    await say(running, "Add another lunch, 12.50 from Cash")
    assert "Ledger entry" in running.chat[-1]
    await decide(running, "proposal_approve")

    assert await entries(running) == [1250, 1250]


async def test_words_over_a_subsession_s_review_end_its_request_and_the_subsession_goes_on(
    wallet_bot,
):
    """AG-SUB-073 — tests/brd/tg_agent_shell/agents.feature"""
    ids = await seeded(wallet_bot)
    running = await started(
        wallet_bot,
        sub_turn("auditor"),
        CompletionTurn(content="Which entry?"),
        lunch(ids),
        lunch(ids, 1500),
        CompletionTurn(content="Lunch is written down at 15.00."),
    )
    await say(running, "Let us go through the ledger")
    await say(running, "Add lunch, 12.50 from Cash")

    await say_over(running, "No, it was 15")
    assert "Ledger entry" in running.chat[-1]
    # A new request of the subsession's agent, reading the refused review in its stretch.
    assert "No, it was 15" in said(running, 3)
    await decide(running, "proposal_approve")

    assert await entries(running) == [1500]
    assert running.chat[-1].endswith("Lunch is written down at 15.00.")
    assert await open_subsession(running) is not None


async def test_a_subsession_s_review_out_of_time_ends_its_request_and_not_the_subsession(
    wallet_bot,
):
    """AG-SUB-073 — tests/brd/tg_agent_shell/agents.feature"""
    ids = await seeded(wallet_bot)
    running = await started(
        wallet_bot,
        sub_turn("auditor"),
        CompletionTurn(content="Which entry?"),
        lunch(ids),
        CompletionTurn(content="The lunch was not written down. Shall I propose it again?"),
    )
    await say(running, "Let us go through the ledger")
    await say(running, "Add lunch, 12.50 from Cash")
    [batch] = running.root.reviews.open_batches

    await expire_review(running.screen, running.services, batch.state.head.proposal_id)
    assert await entries(running) == []
    assert await open_subsession(running) is not None

    await say(running, "Is it there?")
    assert running.chat[-1] == "The lunch was not written down. Shall I propose it again?"
    assert len(running.provider.requests) == 4


async def test_cancelling_the_answer_after_a_save_keeps_the_save_and_the_subsession(wallet_bot):
    """AG-SUB-073 — tests/brd/tg_agent_shell/agents.feature"""
    ids = await seeded(wallet_bot)
    running = await started(
        wallet_bot,
        sub_turn("auditor"),
        CompletionTurn(content="Which entry?"),
        lunch(ids),
        CompletionTurn(content="It is written down."),
    )
    await say(running, "Let us go through the ledger")
    await say(running, "Add lunch, 12.50 from Cash")
    # The answer after the Save is asked for and never comes.
    asked, scripted = asyncio.Event(), running.provider.complete

    async def held(request):
        if len(running.provider.requests) == 3:
            running.provider.requests.append(request)
            asked.set()
            await asyncio.Event().wait()
        return await scripted(request)

    running.provider.complete = held
    pressing = asyncio.create_task(decide(running, "proposal_approve"))
    await asked.wait()
    await command_cancel(running.message, running.services)
    await asyncio.gather(pressing, return_exceptions=True)

    assert await entries(running) == [1250]
    assert await open_subsession(running) is not None
    await say(running, "Is it there?")
    assert running.chat[-1] == "It is written down."


def ended(title: str = "💬 Budget talk") -> str:
    return f"<b>{title}</b>\n{EXIT_RESULT}"


async def talk_opened(wallet_bot, *after: CompletionTurn) -> Running:
    """A bot whose owner has just opened a talk with the coach, scripted on with `after`."""
    running = await started(
        wallet_bot, sub_turn("coach"), CompletionTurn(content="What is the budget for?"), *after
    )
    await say(running, "Let us plan a budget")
    return running


async def command(running: Running, handler) -> None:
    _next_message(running)
    running.message.text = "/exit"
    await handler(running.message, running.services)


async def test_exit_ends_the_subsession_without_a_result(wallet_bot):
    """TG-SUB-036 — tests/brd/tg_agent_shell/telegram_history.feature"""
    running = await talk_opened(wallet_bot)

    await command(running, command_exit)

    assert await open_subsession(running) is None
    assert running.chat[-1] == ended()
    assert [entry.text for entry in await running.services.history.recent(OWNER_ID)] == [
        "Let us plan a budget", f"💬 Budget talk\n{EXIT_RESULT}"
    ]
    await command(running, command_exit)
    assert running.chat[-1] == NOTHING_TO_EXIT
    assert len(running.provider.requests) == 2


async def test_exit_is_published_only_where_an_agent_opens_a_subsession(wallet_bot):
    """TG-SUB-036 — tests/brd/tg_agent_shell/telegram_history.feature"""
    running = await wallet_bot.start()
    assert "exit" in [screen.command for screen in running.services.commands]
    plain = Registry.of(
        tuple(
            replace(module, agents=tuple(agent for agent in module.agents if agent.title is None))
            for module in app.MODULES
        ),
        world=app.ledger_world,
    )
    assert "exit" not in [screen.command for screen in plain.commands]


async def test_a_clear_ends_the_open_subsession_first(wallet_bot):
    """TG-SUB-036 — tests/brd/tg_agent_shell/telegram_history.feature"""
    running = await talk_opened(wallet_bot, CompletionTurn(content="Hello again."))

    async def no_word(session):
        return None

    _next_message(running)
    await AccessManager(running.services, no_word).clear(running.message)

    async with running.sessions() as session:
        closed = await session.scalar(select(Subsession))
    assert closed.through_message_id is not None and closed.result == EXIT_RESULT
    await say(running, "Hi")
    assert running.chat[-1] == "Hello again."
    assert "What is the budget for?" not in said(running, 2)


async def test_a_result_that_never_reached_the_chat_is_delivered_once_at_the_next_turn(
    wallet_bot,
):
    """TG-SUB-037 — tests/brd/tg_agent_shell/telegram_history.feature"""
    running = await talk_opened(
        wallet_bot, CompletionTurn(content="Noted."), CompletionTurn(content="Fine.")
    )
    # The process stopped after closing it and before saying so.
    async with running.sessions() as session:
        talk = await current_subsession(session, OWNER_ID)
        assert await close_subsession(session, talk.id, RESULT)
        await session.commit()

    await say(running, "Thanks")
    await say(running, "Bye")

    assert running.chat.count(f"<b>💬 Budget talk</b>\n{RESULT}") == 1
    assert running.chat[-1] == "Fine."
    # Delivered before the conversation was read, so the root read it with the owner's words.
    assert RESULT in said(running, 2) and "What is the budget for?" not in said(running, 2)
    assert len(running.provider.requests) == 4


async def test_a_stretch_telegram_refuses_to_delete_stays_out_of_the_conversation(wallet_bot):
    """TG-SUB-034 — tests/brd/tg_agent_shell/telegram_history.feature"""
    running = await talk_opened(
        wallet_bot, finish_turn(RESULT), CompletionTurn(content="Noted."), CompletionTurn(content="Fine.")
    )

    async def refuse(*, chat_id, message_ids):
        raise TelegramBadRequest(method=None, message="Bad Request: message can't be deleted")

    running.message.bot.delete_messages = refuse
    await say(running, "Food, 300 a month")
    await say(running, "Thanks")
    await say(running, "Bye")

    assert running.chat.count(f"<b>💬 Budget talk</b>\n{RESULT}") == 1
    assert "What is the budget for?" not in said(running, 3)
    assert [entry.text for entry in await running.services.history.recent(OWNER_ID)][:2] == [
        "Let us plan a budget", f"💬 Budget talk\n{RESULT}"
    ]
    assert len(running.provider.requests) == 5


async def test_a_subsession_whose_agent_is_gone_ends_at_the_next_message(wallet_bot):
    """TG-SUB-036 — tests/brd/tg_agent_shell/telegram_history.feature"""
    running = await talk_opened(wallet_bot, CompletionTurn(content="Hello."))
    # Started again without the feature that declared it.
    del running.root.talks["coach"]

    await say(running, "Hi")

    assert running.chat.count(ended("coach")) == 1
    assert running.chat[-1] == "Hello."
    assert await open_subsession(running) is None


async def test_work_nobody_asked_for_waits_while_a_subsession_is_open(wallet_bot):
    """AG-SUB-074 — tests/brd/tg_agent_shell/agents.feature"""
    running = await talk_opened(wallet_bot, finish_turn(RESULT))
    assert not await chat_is_free(running.services)

    await say(running, "Food, 300 a month")

    assert await chat_is_free(running.services)


async def test_the_work_around_a_turn_is_the_conversation_s_alone(wallet_bot, monkeypatch):
    """AG-SUB-074 — tests/brd/tg_agent_shell/agents.feature"""
    ran: list[str] = []

    async def before(*_):
        ran.append("before")

    async def after(*_):
        ran.append("after")

    monkeypatch.setattr(dialogue, "run_before_turn", before)
    monkeypatch.setattr(dialogue, "run_after_turn", after)
    running = await talk_opened(wallet_bot, finish_turn(RESULT), CompletionTurn(content="Noted."))
    # The turn that opened it has nothing after it.
    assert ran == ["before"]

    await say(running, "Food, 300 a month")
    assert ran == ["before"]
    await say(running, "Thanks")
    assert ran == ["before", "before", "after"]


async def test_locking_and_opening_the_chat_leave_the_subsession_to_go_on(wallet_bot):
    """TG-SUB-038 — tests/brd/tg_agent_shell/telegram_history.feature"""
    running = await talk_opened(wallet_bot, CompletionTurn(content="How much a month?"))
    verifier = hash_secret_word("🔑")

    async def read_verifier(session):
        return verifier

    access = AccessManager(running.services, read_verifier)
    running.services.access = access
    await access.lock(running.message)
    _next_message(running)
    word = QueueTestMessage(
        message_id=running.message.message_id,
        text="🔑",
        is_bot=False,
        answer_as_new=True,
        parent=running.message,
    )
    await access.intercept(word)
    assert not access.blocked and await open_subsession(running) is not None

    await say(running, "Food")

    assert running.chat[-1] == "How much a month?"
    # The clears took the stretch out of the chat, not out of the subsession.
    assert '"role": "assistant", "content": "What is the budget for?"' in said(running, 2)
