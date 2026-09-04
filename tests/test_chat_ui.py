"""How a message reaches the chat: where it is cut, and what is swept afterwards."""

from __future__ import annotations

import re
from uuid import uuid4

from sqlalchemy import select
from ui_harness import (
    FakeMessage,
    services_for,
)

from safwa.features.continuity.model import SUMMARY_HEADER
from telegram_llm import (
    TELEGRAM_TEXT_LIMIT,
    split_telegram_text,
)
from tg_agent_shell.adapters.kinds import MessageKind
from tg_agent_shell.adapters.telegram_history import TelegramMessage
from tg_agent_shell.ai.outcome import AIOutcome, AIOutcomeKind
from tg_agent_shell.proposals.telegram import render_ai_outcome
from tg_agent_shell.telegram.chat import (
    discard_stale_status,
    send_registered,
)


def test_a_split_falls_on_a_line_break_and_closes_what_it_opened() -> None:
    """SC-SPLIT-004 — tests/brd/screens.feature"""
    body = "\n".join(f"line {index} of the answer" for index in range(400))
    text = f"<b>Heading</b>\n<i>{body}</i>"

    parts = split_telegram_text(text)

    assert len(parts) > 1
    assert all(len(part) <= TELEGRAM_TEXT_LIMIT for part in parts)
    # Nothing is left open across a cut, and the next part opens it again.
    assert all(part.count("<i>") == part.count("</i>") for part in parts)
    assert parts[1].startswith("<i>")
    assert all(part.endswith("</i>") for part in parts[:-1])
    assert all(part.rsplit("<", 1)[0].endswith("answer") for part in parts[:-1])
    # And nothing is lost between them.
    visible = " ".join(re.sub(r"<[^>]+>", "", part) for part in parts)
    assert visible.split() == re.sub(r"<[^>]+>", "", text).split()


async def test_an_over_long_answer_arrives_as_several_dialogue_messages(sessions) -> None:
    """SC-SPLIT-004 — tests/brd/screens.feature"""
    services = services_for(sessions)
    message = FakeMessage(970, bot_message=False, answer_as_new=True)
    answer = " ".join(f"word{index}" for index in range(1_500))

    await render_ai_outcome(message, services, AIOutcome(AIOutcomeKind.ANSWER, answer))

    assert len(message.sent_messages) > 1
    async with sessions() as session:
        rows = list(await session.scalars(select(TelegramMessage)))
    # Every part is registered as dialogue, which is what makes the window read them all
    # and `dialogue()` merge them back into the one answer they were.
    assert [row.kind for row in rows] == [MessageKind.DIALOGUE_ASSISTANT.value] * len(rows)
    assert len(rows) == len(message.sent_messages)


async def test_an_over_long_summary_is_split_and_every_part_is_registered(
    sessions,
) -> None:
    """SC-SPLIT-004 — tests/brd/screens.feature"""
    import tg_agent_shell.telegram.chat as messaging

    services = services_for(sessions)
    message = FakeMessage(971, bot_message=False, answer_as_new=True)
    text = f"{SUMMARY_HEADER}\n" + "\n".join(f"point {index}" for index in range(600))

    await messaging.send_summary(message, services, text)

    assert len(message.sent_messages) > 1
    async with sessions() as session:
        rows = list(await session.scalars(select(TelegramMessage)))
    # Every part carries the kind, which is what the backwards read meets them as.
    assert [row.kind for row in rows] == [MessageKind.SUMMARY.value] * len(rows)


async def test_a_split_cue_is_delivered_only_once_its_last_part_is_in_the_chat(
    sessions,
) -> None:
    """SC-SPLIT-004 — tests/brd/screens.feature"""
    import tg_agent_shell.telegram.chat as messaging

    services = services_for(sessions)
    message = FakeMessage(972, bot_message=False, answer_as_new=True)
    event_id = uuid4().hex

    sent = await messaging.send_prose(
        message,
        services,
        " ".join(f"word{index}" for index in range(1_500)),
        kind=MessageKind.CUE,
        event_id=event_id,
    )

    assert len(message.sent_messages) > 1
    async with sessions() as session:
        carrier = await session.scalar(
            select(TelegramMessage).where(TelegramMessage.event_id == event_id)
        )
    # `CueRuntime.speak` reads this row back to mean "the owner has these words". On the
    # first part it would call a send that failed halfway delivered.
    assert carrier.message_id == sent.message_id == message.sent_messages[-1].message_id


async def test_a_status_message_the_process_died_under_is_swept_at_startup(sessions) -> None:
    services = services_for(sessions)
    screen = FakeMessage(90, bot_message=True, answer_as_new=True)
    await send_registered(screen, services, "Thinking", kind=MessageKind.STATUS, replace=False)
    orphan = screen.sent_messages[-1].message_id

    await discard_stale_status(screen.bot, services, screen.chat.id)

    assert orphan in screen.bot.deleted
    async with sessions() as session:
        assert (
            await session.scalar(
                select(TelegramMessage).where(TelegramMessage.kind == MessageKind.STATUS.value)
            )
            is None
        )
