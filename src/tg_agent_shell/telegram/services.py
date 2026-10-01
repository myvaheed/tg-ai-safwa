"""The application container and who is allowed to reach it.

Everything the whole application shares is assembled once by the composition root and handed
around on `Services`: the sessions, the root session, the turn, and every catalogue the features
declared. A feature's Telegram adapter imports this module and `telegram_llm`, and nothing
else of the shell. The router that carries the handlers is `routing.py`'s.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Audio, CallbackQuery, Message, TelegramObject, VideoNote, Voice
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telegram_llm import ChatHost, Transcriber

from ..foundation.clock import utcnow
from ..foundation.screens import ScreenCatalogue
from ..history import TelegramHistorySource
from ..hooks.registry import HookRegistry
from ..media.library import MediaLibrary
from ..session import RootSession
from ..similarity import Similarity
from ..turn import TurnManager
from .contributions import ScreenCommand, StartLink, TextInputFlow

logger = logging.getLogger(__name__)

# How long the first photo of an album waits for the rest. Telegram hands an album over as
# one message per photo, a few milliseconds apart.
ALBUM_GATHER_SECONDS = 1.0

# What a press is told while an answer holds the turn.
STILL_ANSWERING = "Still answering. Use /cancel to stop it."


def audio_payload(message: Message) -> Audio | Voice | VideoNote | None:
    """The audio a message carries, whichever of the three Telegram shapes it arrived in."""
    return message.voice or message.audio or message.video_note


class AlbumGatherer:
    """The photos of one album, gathered so the album is answered once.

    Telegram sends each photo of an album as a message of its own. The first one waits for
    the rest and carries them all; each later one joins it and goes no further, so none of
    them reaches the turn as a second message and is taken out of the chat.
    """

    def __init__(self) -> None:
        self.albums: dict[str, list[Message]] = {}

    async def gather(self, message: Message) -> list[Message] | None:
        """The whole album for its first photo, and None for every later one."""
        group = str(message.media_group_id)
        album = self.albums.get(group)
        if album is not None:
            album.append(message)
            return None
        album = self.albums[group] = [message]
        try:
            await asyncio.sleep(ALBUM_GATHER_SECONDS)
        finally:
            del self.albums[group]
        return sorted(album, key=lambda photo: photo.message_id)


@dataclass
class Services:
    sessions: async_sessionmaker[AsyncSession]
    root: RootSession
    history: TelegramHistorySource
    owner_id: int
    turn: TurnManager
    chat: ChatHost
    screens: ScreenCatalogue
    commands: tuple[ScreenCommand, ...]
    callback_actions: Mapping[str, CallbackHandler]
    text_inputs: Mapping[str, TextInputFlow]
    hooks: HookRegistry = field(default_factory=HookRegistry.of)
    start_links: tuple[StartLink, ...] = ()
    # Whatever the application's own handlers need to reach. The shell carries it and
    # never reads it, the way a session carries `host_state`.
    features: Any = None
    views: frozenset[str] = frozenset()
    bot_username: str = ""
    transcriber: Transcriber | None = None
    # None, and a creating review screen lists no similar items.
    similarity: Similarity | None = None
    # None, and a photo is refused with a line saying image input is off.
    media: MediaLibrary | None = None
    albums: AlbumGatherer = field(default_factory=AlbumGatherer)
    # The owner's last message or press, in this process: starting counts as one.
    owner_acted_at: datetime = field(default_factory=utcnow)


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
        services.owner_acted_at = utcnow()
        album: list[Message] | None = None
        if isinstance(event, Message) and event.media_group_id is not None:
            album = await services.albums.gather(event)
            if album is None:
                return None
            data["album"] = album
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
                # the chat is what makes it not something the owner said. A recording or a
                # photo is refused here rather than downloaded, so nothing is paid to read it.
                if not command_deleted:
                    try:
                        for refused in album or [event]:
                            await refused.delete()
                    except TelegramAPIError:
                        # It could not be taken out, so it is theirs and stays theirs.
                        services.turn.cancel()
                        return await handler(event, data)
                return None
        if isinstance(event, CallbackQuery) and services.turn.active:
            await event.answer(STILL_ANSWERING, show_alert=True)
            return None
        taken = False
        if isinstance(event, Message) and not services.turn.active:
            is_dialogue = (
                (bool(event.text) and not event.text.lstrip().startswith("/"))
                or audio_payload(event) is not None
                or bool(event.photo)
            )
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


