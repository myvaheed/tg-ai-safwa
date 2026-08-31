"""A bot that only talks, on `telegram_llm` and `llm_gateway`, with no Safwa in it.

It remembers no conversation of its own. The chat is the conversation, and every turn is
read back out of it — which is the whole claim of the package, and the reason it asks an
application for only two things: somewhere to keep notes about its own messages, and a way
to read the chat back. Neither has to be a database or a user session. Here one small class
is both, because an aiogram bot already sees every message that passes through it.

Run it: `BOT_TOKEN=... uv run python examples/plain_chat_bot/bot.py`
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Collection, Sequence
from dataclasses import replace

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message

from llm_gateway import (
    CompletionRequest,
    LlmProvider,
    OpenAICompatibleConfig,
    OpenAICompatibleProvider,
)
from telegram_llm import ChatHost, ChatMessage, ChatVocabulary, ChatWindow, KindMarks, Note

# This bot has three kinds of message and they are all conversation. The codes are its own;
# the package needs only that they are distinct and never change once a chat has used them.
PERSON, BOT, SUMMARY = "person", "bot", "summary"
MARKS = KindMarks({PERSON: 1, BOT: 2, SUMMARY: 3})
VOCABULARY = ChatVocabulary(person=PERSON, assistant=frozenset({BOT}), summary=SUMMARY)

SYSTEM = {"role": "system", "content": "You are a friendly bot. Keep answers short."}


class Chat:
    """Both ports at once: notes about the bot's own messages, and the chat it can read."""

    def __init__(self) -> None:
        self.kept: dict[tuple[int, int], Note] = {}
        self.seen: list[ChatMessage] = []

    async def outgoing(
        self, chat_id: int, *, kinds: Collection[str] | None = None
    ) -> Sequence[Note]:
        return sorted(
            (
                note
                for note in self.kept.values()
                if note.chat_id == chat_id
                and note.direction == "out"
                and (kinds is None or note.kind in kinds)
            ),
            key=lambda note: note.message_id,
            reverse=True,
        )

    async def note(self, chat_id: int, message_id: int) -> Note | None:
        return self.kept.get((chat_id, message_id))

    async def write(self, note: Note) -> None:
        standing = self.kept.get((note.chat_id, note.message_id))
        if standing is not None and note.event_id is None:
            note = replace(note, event_id=standing.event_id)
        self.kept[(note.chat_id, note.message_id)] = note

    async def forget(self, chat_id: int, message_id: int) -> None:
        self.kept.pop((chat_id, message_id), None)

    async def messages(self, limit: int) -> AsyncIterator[ChatMessage]:
        """The chat newest first, which is the order the window scans it in."""
        for message in reversed(self.seen[-limit:]):
            yield message

    def saw(self, message: Message) -> None:
        self.seen.append(
            ChatMessage(
                id=message.message_id,
                text=message.text or "",
                sender_id=message.from_user.id if message.from_user else None,
                date=message.date,
            )
        )


class Talker:
    """One turn: read the chat, ask the model, put the answer back in the chat."""

    def __init__(self, provider: LlmProvider, bot_user_id: int) -> None:
        self.chat = Chat()
        # A Toast timer is started by whoever can cancel it; this example never shuts down.
        self.host = ChatHost(
            self.chat, MARKS, spawn=lambda work, name: asyncio.create_task(work, name=name)
        )
        self.provider = provider
        self.bot_user_id = bot_user_id

    def window(self, person_id: int) -> ChatWindow:
        return ChatWindow(
            self.chat,
            self.chat,
            MARKS,
            VOCABULARY,
            bot_user_id=self.bot_user_id,
            owner_id=person_id,
            count_tokens=lambda text: max(len(text) // 4, 1),
            token_budget=4_000,
            summary_context_limit=0,
        )

    async def answer(self, message: Message) -> None:
        self.chat.saw(message)
        person = message.from_user.id if message.from_user else message.chat.id
        dialogue = await self.window(person).dialogue(message.chat.id)
        turn = await self.provider.complete(
            CompletionRequest(
                messages=(SYSTEM, *({"role": m.role, "content": m.content} for m in dialogue))
            )
        )
        self.chat.saw(await self.host.send_parts(message, turn.content, kind=BOT))


async def main() -> None:
    bot = Bot(os.environ["BOT_TOKEN"])
    provider = OpenAICompatibleProvider(
        OpenAICompatibleConfig(
            base_url=os.environ.get("LLM_URL", "http://localhost:1234/v1"),
            api_key=os.environ.get("LLM_KEY", "not-needed"),
            model=os.environ.get("LLM_MODEL", "local-model"),
        )
    )
    talker = Talker(provider, (await bot.get_me()).id)
    dispatcher = Dispatcher()
    dispatcher.message.register(talker.answer, F.text)
    try:
        await dispatcher.start_polling(bot)
    finally:
        await provider.aclose()


if __name__ == "__main__":
    asyncio.run(main())
