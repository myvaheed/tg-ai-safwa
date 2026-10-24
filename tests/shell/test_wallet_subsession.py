"""A subsession of the example application, through the shell and nothing else.

The root opens one with `sub`, its agent answers message after message from its own stretch
of the chat, and `finish` hands one result back to the conversation, whose window never
holds the stretch. Nothing here imports Safwa.
"""

from __future__ import annotations

import json

from sqlalchemy import select
from wallet_harness import OWNER_ID, Running, take_a_turn

from llm_gateway import CompletionTurn, ToolCall
from tg_agent_shell.ai.messages import OPENED_FROM
from tg_agent_shell.history import Subsession, current_subsession

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
    running.message.message_id = 1 + max(
        (sent.message_id for sent in running.message.sent), default=running.message.message_id
    )
    await take_a_turn(running, text)


def said(running: Running, request: int) -> str:
    """Everything one request to the model carried, as one text."""
    return json.dumps(running.provider.requests[request].messages, ensure_ascii=False)


def offered(running: Running, request: int) -> list[str]:
    return [tool["function"]["name"] for tool in running.provider.requests[request].tools]


async def test_a_subsession_answers_until_it_finishes_and_leaves_one_result(wallet_bot):
    running = await started(
        wallet_bot,
        sub_turn("coach"),
        CompletionTurn(content="What is the budget for?"),
        CompletionTurn(content="How much a month?"),
        finish_turn(RESULT),
        CompletionTurn(content="Noted."),
    )

    await say(running, "Let us plan a budget")
    assert running.chat[-2:] == ["💬 Budget talk", "What is the budget for?"]
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
