"""The application container, the router, and who is allowed to reach them.

Everything the whole of Safwa shares is assembled once by the composition root and handed
around on `Services`: the sessions, the advisor, the turn, and every catalogue the features
declared. A feature's Telegram adapter imports this module and `telegram_llm`, and nothing
else of the shell.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from aiogram import BaseMiddleware, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Audio, CallbackQuery, Message, TelegramObject, VideoNote, Voice
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telegram_llm import ChatHost, Transcriber

from ..adapters.telegram_history import TelegramHistorySource
from ..ai.advisor import AIAdvisor
from ..features.continuity.memory import MemoryFileStore
from ..features.continuity.persona import PersonaContinuity
from ..foundation.models import Workspace
from ..foundation.screens import ScreenCatalogue, ScreenCommand, StartLink, TextInputFlow
from ..turn import TurnManager

logger = logging.getLogger(__name__)
router = Router(name="safwa")


def audio_payload(message: Message) -> Audio | Voice | VideoNote | None:
    """The audio a message carries, whichever of the three Telegram shapes it arrived in."""
    return message.voice or message.audio or message.video_note


async def sprint_is_active(session: AsyncSession) -> bool:
    """Whether a Sprint is running, which is what makes Today a real screen."""
    workspace = await session.get(Workspace, 1)
    return bool(workspace and workspace.active_sprint_id)


@dataclass
class Services:
    sessions: async_sessionmaker[AsyncSession]
    advisor: AIAdvisor
    history: TelegramHistorySource
    memory: MemoryFileStore
    continuity: PersonaContinuity
    owner_id: int
    turn: TurnManager
    chat: ChatHost
    screens: ScreenCatalogue
    commands: tuple[ScreenCommand, ...]
    callback_actions: Mapping[str, CallbackHandler]
    text_inputs: Mapping[str, TextInputFlow]
    start_links: tuple[StartLink, ...] = ()
    # The `ai_*` views the features publish; a saved Request's SQL is validated against them.
    views: frozenset[str] = frozenset()
    # Loaded from Settings; item citations stay plain text when the username is omitted.
    bot_username: str = ""
    # None when SAFWA_ASR_PROVIDER is off, which is what makes the bot text-only.
    transcriber: Transcriber | None = None


class OwnerAndWritingMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        services: Services = data["services"]
        user = getattr(event, "from_user", None)
        chat = getattr(event, "chat", None) or getattr(
            getattr(event, "message", None), "chat", None
        )
        if user is None or user.id != services.owner_id or (chat and chat.type != "private"):
            return None
        command = ""
        command_deleted = False
        if isinstance(event, Message):
            command_text = (event.text or "").lstrip()
            command_token = command_text.split(maxsplit=1)[0] if command_text else ""
            if command_token.startswith("/"):
                command = command_token.split("@", 1)[0].casefold()
            if command:
                try:
                    await event.delete()
                    command_deleted = True
                except TelegramAPIError as error:
                    logger.warning("Could not delete operational command %s: %s", command, error)
        if services.turn.background:
            # The owner outranks work nobody asked for: drop it and take the message
            # normally, rather than deleting it the way a foreground collision would.
            services.turn.cancel()
        if isinstance(event, Message) and services.turn.active:
            if command == "/cancel":
                return await handler(event, data)
            if event.message_id != services.turn.source_message_id:
                # Nothing joins a running answer: the message leaves the chat, and leaving
                # the chat is what makes it not something the owner said. A recording is
                # refused here rather than downloaded, so nothing is paid to transcribe it.
                if not command_deleted:
                    try:
                        await event.delete()
                    except TelegramAPIError:
                        # It could not be taken out, so it is theirs and stays theirs.
                        services.turn.cancel()
                        return await handler(event, data)
                return None
        if isinstance(event, CallbackQuery) and services.turn.active:
            await event.answer("Safwa is responding. Use /cancel to stop it.", show_alert=True)
            return None
        taken = False
        if isinstance(event, Message) and not services.turn.active:
            is_dialogue = (
                bool(event.text) and not event.text.lstrip().startswith("/")
            ) or audio_payload(event) is not None
            if is_dialogue:
                taken = services.turn.try_begin(event.message_id)
        try:
            return await handler(event, data)
        finally:
            if taken:
                services.turn.end(event.message_id)


@dataclass(frozen=True)
class CallbackContext:
    """One claimed inline action: the screen it replaces plus its owner-scoped payload."""

    callback: CallbackQuery
    services: Services
    action: str
    payload: dict[str, Any]

    @property
    def message(self) -> Message:
        return self.callback.message

    @property
    def sessions(self) -> async_sessionmaker[AsyncSession]:
        return self.services.sessions

    @property
    def owner_id(self) -> int:
        return self.services.owner_id


CallbackHandler = Callable[[CallbackContext], Awaitable[None]]


