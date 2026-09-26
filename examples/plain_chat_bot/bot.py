"""A bot that only talks, on `telegram_llm` and `llm_gateway`, with no Safwa in it.

The chat is the conversation: the bot keeps each message as it passes through it, and every
turn is read back out of what it kept — which is the whole claim of the package, and the
reason it asks an application for only one thing: somewhere to keep those notes. It does
not have to be a database. Here it is one small class.

Run it: `BOT_TOKEN=... uv run python examples/plain_chat_bot/bot.py`
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Collection, Sequence
from dataclasses import replace

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message

from llm_gateway import (
    CompletionRequest,
    LlmProvider,
    OpenAICompatibleConfig,
    OpenAICompatibleProvider,
)
from telegram_llm import ChatHost, ChatVocabulary, ChatWindow, Note

# This bot has two kinds of message and they are both conversation. It declares no
# `WindowEdge`, so nothing but the token budget ever ends its window.
PERSON, BOT = "person", "bot"
VOCABULARY = ChatVocabulary(person=PERSON, assistant=frozenset({BOT}))

SYSTEM = {"role": "system", "content": "You are a friendly bot. Keep answers short."}


class Chat:
    """The notes: every message of the chat, as the bot keeps it."""

    def __init__(self) -> None:
        self.kept: dict[tuple[int, int], Note] = {}

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

    async def messages(self, chat_id: int, *, limit: int) -> Sequence[Note]:
        """The chat newest first, which is the order the window scans it in."""
        return sorted(
            (
                note
                for note in self.kept.values()
                if note.chat_id == chat_id and note.text is not None
            ),
            key=lambda note: note.message_id,
            reverse=True,
        )[:limit]

    async def note(self, chat_id: int, message_id: int) -> Note | None:
        return self.kept.get((chat_id, message_id))

    async def write(self, note: Note) -> None:
        standing = self.kept.get((note.chat_id, note.message_id))
        if standing is not None:
            note = replace(
                note, event_id=note.event_id or standing.event_id, at=standing.at
            )
        self.kept[(note.chat_id, note.message_id)] = note

    async def forget(self, chat_id: int, message_id: int) -> None:
        self.kept.pop((chat_id, message_id), None)


class Talker:
    """One turn: read the chat, ask the model, put the answer back in the chat."""

    def __init__(self, provider: LlmProvider) -> None:
        self.chat = Chat()
        # A Toast timer is started by whoever can cancel it; this example never shuts down.
        self.host = ChatHost(
            self.chat, spawn=lambda work, name: asyncio.create_task(work, name=name)
        )
        self.window = ChatWindow(
            self.chat,
            VOCABULARY,
            count_tokens=lambda text: max(len(text) // 4, 1),
            token_budget=4_000,
        )
        self.provider = provider

    async def answer(self, message: Message) -> None:
        await self.host.keep(message, kind=PERSON)
        dialogue = await self.window.dialogue(message.chat.id)
        turn = await self.provider.complete(
            CompletionRequest(
                messages=(SYSTEM, *({"role": m.role, "content": m.content} for m in dialogue))
            )
        )
        await self.host.send_parts(message, turn.content, kind=BOT)


async def main() -> None:
    bot = Bot(os.environ["BOT_TOKEN"])
    provider = OpenAICompatibleProvider(
        OpenAICompatibleConfig(
            base_url=os.environ.get("LLM_URL", "http://localhost:1234/v1"),
            api_key=os.environ.get("LLM_KEY", "not-needed"),
            model=os.environ.get("LLM_MODEL", "local-model"),
        )
    )
    talker = Talker(provider)
    dispatcher = Dispatcher()
    dispatcher.message.register(talker.answer, F.text)
    try:
        await dispatcher.start_polling(bot)
    finally:
        await provider.aclose()


if __name__ == "__main__":
    asyncio.run(main())
