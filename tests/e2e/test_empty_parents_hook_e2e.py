"""A Goal without Actions becomes one morning request: said in the chat as the Advisor's own
words, and read back on the owner's next turn.

The real registry, the real Cue poll, the real Advisor turn and the real chat window; the
provider is scripted, the chat is a fake that keeps what was sent, and Telethon is a fake
reading that chat back.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from telegram_fakes import FakeTelegramClient, FakeTelegramMessage, spawn_timer
from ui_harness import FakeMessage, history_source

from safwa.bootstrap.modules import REGISTRY, SCREENS
from safwa.features.cards.hooks import (
    EMPTY_PARENT_GRACE_DAYS,
    EMPTY_PARENTS_HOOK,
    HARD_TIME_CHECK,
    HARD_TIME_HOOK,
)
from safwa.features.cards.use_cases import create_card
from safwa.features.profile.api import morning_time
from safwa.features.profile.model import MORNING_TIME_DEFAULT
from telegram_llm import ChatHost
from tg_agent_shell.cues.background import tick
from tg_agent_shell.cues.initiatives import queue_advice
from tg_agent_shell.cues.model import Cue
from tg_agent_shell.cues.runtime import CueRuntime
from tg_agent_shell.foundation.kinds import MARKS
from tg_agent_shell.history import TelegramNotes
from tg_agent_shell.hooks.contracts import Tick
from tg_agent_shell.turn import TurnManager

pytestmark = pytest.mark.e2e

OWNER_ID = 42
BOT_ID = 999
# What a well-behaved Advisor answers the request with: both ways forward, in one message.
ANSWER = (
    "У Цели «Learn Spanish» пока нет Действий. Хотите запланировать их сейчас или создать "
    "Действие «Запланировать действия для Learn Spanish»?"
)


async def _pending(sessions) -> list[tuple[str | None, list | None]]:
    async with sessions() as session:
        return [(cue.hook, cue.payload) for cue in await session.scalars(select(Cue))]


async def test_cd_empty_035_the_morning_question_is_said_and_read_back_on_the_next_turn(
    e2e_harness, monkeypatch
):
    """CD-EMPTY-035 — tests/brd/cards.feature"""
    sessions = e2e_harness.sessions
    async with sessions() as session:
        goal = await create_card(session, kind="goal", title="Learn Spanish")
        goal.created_at = datetime.now(UTC) - timedelta(days=EMPTY_PARENT_GRACE_DAYS, hours=1)
        await session.commit()
        goal_id = goal.id

    # The morning fires both daily checks; firing again before either is said adds nothing.
    morning = [
        (EMPTY_PARENTS_HOOK.name, [MORNING_TIME_DEFAULT]), (HARD_TIME_HOOK.name, [HARD_TIME_CHECK]),
    ]
    for _ in range(2):
        await queue_advice(REGISTRY.hooks, sessions, Tick(MORNING_TIME_DEFAULT, morning_time))
    assert await _pending(sessions) == morning

    advisor, provider = e2e_harness.advisor([ANSWER])
    chat = FakeMessage(900, bot_message=False, chat_id=OWNER_ID, answer_as_new=True)
    read_back: list[FakeTelegramMessage] = []
    history = history_source(
        FakeTelegramClient(read_back), sessions, bot_user_id=BOT_ID, owner_id=OWNER_ID
    )
    services = SimpleNamespace(
        sessions=sessions,
        hooks=REGISTRY.hooks,
        turn=TurnManager(),
        root=advisor,
        history=history,
        chat=ChatHost(TelegramNotes(sessions), MARKS, spawn=spawn_timer),
        screens=SCREENS,
        bot_username="safwa_ai_bot",
        owner_id=OWNER_ID,
    )
    runtime = CueRuntime(services, bot=None, owner_id=OWNER_ID)  # type: ignore[arg-type]
    monkeypatch.setattr(runtime, "_anchor", lambda: chat)

    assert await tick(
        sessions,
        gate=runtime.can_speak,
        speak=runtime.speak,
        release=runtime.release,
        prepare=runtime.prepare,
    ) is True
    asked = [str(item["content"]) for item in provider.calls[0] if item["role"] == "user"]
    handed = next(text for text in asked if f"#{goal_id} «Learn Spanish» (Goal)" in text)
    assert "plan its Actions now, or create one Action" in handed
    # No Sprint runs, so the plan check had nothing to say: settled on the same tick, without
    # a word of its own in the one message.
    assert await _pending(sessions) == []
    assert len(chat.sent_messages) == 1
    assert "Hard Time" not in handed
    assert await tick(
        sessions,
        gate=runtime.can_speak,
        speak=runtime.speak,
        release=runtime.release,
        prepare=runtime.prepare,
    ) is False
    assert await _pending(sessions) == []
    assert len(chat.sent_messages) == 1

    # The chat holds the Advisor's words, and the owner's next turn reads them back.
    for sent in chat.sent_messages:
        read_back.insert(
            0, FakeTelegramMessage(sent.message_id, sent.text, BOT_ID, datetime.now(UTC))
        )
    dialogue = await history.dialogue(OWNER_ID)
    assert [message.role for message in dialogue] == ["assistant"]
    assert ANSWER in dialogue[0].content
    follow_up, provider = e2e_harness.advisor(["Создаю Действие."])
    await follow_up.handle("Давай второй вариант", dialogue=dialogue)
    heard = [message for message in provider.calls[0] if message["role"] == "assistant"]
    assert len(heard) == 1 and ANSWER in str(heard[0]["content"])

    # The next morning asks about the same Goal again: nothing remembers it was asked.
    await queue_advice(REGISTRY.hooks, sessions, Tick(MORNING_TIME_DEFAULT, morning_time))
    assert await _pending(sessions) == morning
    words = await runtime.prepare(EMPTY_PARENTS_HOOK.name, [MORNING_TIME_DEFAULT])
    assert words is not None and words in handed
