"""One owner of chat access and of clearing the chat."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram.types import Message
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..cues.runtime import resume_cues
from ..foundation.clock import utcnow
from ..foundation.kinds import MessageKind
from ..history import (
    CONVERSATION_KINDS,
    TelegramMessage,
    cleared_through,
    mark_cleared,
    register_message,
)
from ..telegram.chat import UNASKED_KINDS
from ..telegram.model import CallbackToken, UiSession
from ..telegram.services import Services, is_dialogue
from .credentials import matches_secret_word, resembles_secret_word
from .model import AccessState, Locked, Open, Unlocking

logger = logging.getLogger(__name__)
ENTER_SECRET_WORD = "Сначала введите Secret word."
# How long a locked chat Telegram refused to clear waits before the next try.
CLEAR_RETRY_SECONDS = 60

Verifier = Callable[[AsyncSession], Awaitable[str | None]]
Home = Callable[[Message, Services], Awaitable[None]]
Again = Callable[[], Awaitable[Any]]


async def _last_user_message_id(session: AsyncSession, chat_id: int) -> int:
    """Where the owner last wrote: what Safwa said unasked after it is still unanswered."""
    return (
        await session.scalar(
            select(func.max(TelegramMessage.message_id)).where(
                TelegramMessage.chat_id == chat_id,
                TelegramMessage.kind == MessageKind.DIALOGUE_USER.value,
            )
        )
        or 0
    )


class AccessManager:
    def __init__(self, services: Services, verifier: Verifier, home: Home | None = None) -> None:
        self.services = services
        self.verifier = verifier
        self.home = home
        self._state: AccessState = Open()
        self._transition = asyncio.Lock()
        # Messages sent while locked, and how to take each up once open. Which
        # one is kept is known only at unlock, against the word just entered.
        self._attempts: list[tuple[Message, Again]] = []

    @property
    def blocked(self) -> bool:
        return not isinstance(self._state, Open)

    @property
    def unlocking(self) -> bool:
        return isinstance(self._state, Unlocking)

    async def enabled(self) -> bool:
        async with self.services.sessions() as session:
            return await self.verifier(session) is not None

    async def initialize(self, anchor: Message) -> None:
        if await self.enabled():
            await self.lock(anchor)

    async def lock(self, anchor: Message, *, keep_unanswered: bool = True) -> None:
        async with self._transition:
            if self.blocked:
                return
            self._state = Locked()
            if not await self._try_clear(anchor, keep_unanswered=keep_unanswered):
                self.services.chat.spawn(
                    self._clear_while_locked(anchor, keep_unanswered=keep_unanswered), "access-clear"
                )

    async def _try_clear(self, anchor: Message, *, keep_unanswered: bool) -> bool:
        try:
            await self._clear(
                anchor, 0 if keep_unanswered else anchor.message_id,
                keep_unanswered=keep_unanswered,
            )
        except Exception:
            logger.exception("Could not clear the locked chat")
            return False
        return True

    async def _clear_while_locked(self, anchor: Message, *, keep_unanswered: bool) -> None:
        """Access is closed already; what is still shown is taken out once Telegram allows."""
        while True:
            await asyncio.sleep(CLEAR_RETRY_SECONDS)
            async with self._transition:
                if not isinstance(self._state, Locked) or await self._try_clear(
                    anchor, keep_unanswered=keep_unanswered
                ):
                    return

    async def clear(self, anchor: Message, *, keep_unanswered: bool = False) -> None:
        """Empty the chat; an automatic clear leaves what the owner has not answered in it."""
        async with self._transition:
            await self._clear(anchor, anchor.message_id, keep_unanswered=keep_unanswered)

    async def _clear(
        self, anchor: Message, through: int = 0, *, keep_unanswered: bool = False
    ) -> None:
        services, chat_id = self.services, anchor.chat.id
        async with services.sessions() as session:
            last = max(
                through,
                await session.scalar(
                    select(func.max(TelegramMessage.message_id)).where(
                        TelegramMessage.chat_id == chat_id,
                    )
                )
                or 0,
            )
            spare: list[int] = []
            if keep_unanswered:
                # Before the boundary is only what an earlier clear took out of the chat.
                after = max(
                    await _last_user_message_id(session, chat_id),
                    await cleared_through(session, chat_id),
                )
                spare = list(await session.scalars(
                    select(TelegramMessage.message_id).where(
                        TelegramMessage.chat_id == chat_id,
                        TelegramMessage.direction == "out",
                        TelegramMessage.kind.in_(UNASKED_KINDS),
                        TelegramMessage.message_id > after,
                        TelegramMessage.message_id <= last,
                    )
                ))
            spare.extend(attempt.message_id for attempt, _again in self._attempts)
            # The conversation starts with the first message the clear leaves in the chat.
            await mark_cleared(session, chat_id, min(spare, default=last + 1) - 1)
            await session.execute(delete(UiSession).where(UiSession.owner_id == services.owner_id))
            await session.execute(
                delete(CallbackToken).where(CallbackToken.owner_id == services.owner_id)
            )
            await session.commit()
        await services.chat.clear(
            anchor.bot,
            chat_id,
            last,
            keep=CONVERSATION_KINDS | UNASKED_KINDS,
            spare=spare,
            strict=True,
        )

    async def intercept(self, event: Any, again: Again | None = None) -> bool:
        if not self.blocked:
            return False
        authenticated = self.unlocking
        async with self._transition:
            if not self.blocked:
                if authenticated:
                    return False
                if hasattr(event, "message_id"):
                    await event.delete()
                else:
                    await event.answer()
                return True
            return await self._locked_input(event, again)

    async def _locked_input(self, event: Any, again: Again | None) -> bool:
        if not hasattr(event, "message_id"):
            await event.answer(ENTER_SECRET_WORD, show_alert=True)
            return True
        async with self.services.sessions() as session:
            existing = await session.scalar(
                select(TelegramMessage.id).where(
                    TelegramMessage.chat_id == event.chat.id,
                    TelegramMessage.message_id == event.message_id,
                )
            )
            if existing is None:
                await register_message(
                    session, event.chat.id, event.message_id, "in", MessageKind.UI_INPUT
                )
            verifier = await self.verifier(session)
            await session.commit()
        correct = (
            isinstance(self._state, Locked)
            and event.edit_date is None
            and event.text is not None
            and verifier is not None
            and await asyncio.to_thread(matches_secret_word, event.text, verifier)
        )
        if correct:
            await self._unlock(event)
        else:
            if (
                again is not None
                and event.edit_date is None
                and (bool(event.text) or is_dialogue(event))
            ):
                self._attempts.append((event, again))
            await self.services.chat.send(
                event,
                ENTER_SECRET_WORD,
                kind=MessageKind.STATUS.value,
                replace=False,
            )
        return True

    async def _unlock(self, anchor: Message) -> None:
        self._state = Unlocking()
        # Commands are never guesses at the word, even when short or similar to it.
        self._attempts = [
            (attempt, again)
            for attempt, again in self._attempts
            if (
                attempt.text is None
                or attempt.text.lstrip().startswith("/")
                or not resembles_secret_word(attempt.text, anchor.text)
            )
        ][-1:]
        try:
            await self.services.turn.wait_idle()
            await self._clear(anchor, anchor.message_id, keep_unanswered=True)
            # The owner's message runs first; waiting hooks speak once the chat is free.
            if not self._attempts:
                await resume_cues(self.services, anchor)
            self.services.owner_acted_at = utcnow()
            if self.home is not None:
                await self.home(anchor, self.services)
            self._state = Open()
            if self._attempts:
                [(_attempt, again)] = self._attempts
                self._attempts = []
                self.services.chat.spawn(self._take_up(again), "access-attempt")
        except Exception:
            logger.exception("Could not finish unlocking the chat")
        finally:
            if self.unlocking:
                self._state = Locked()

    async def _take_up(self, again: Again) -> None:
        """Pass the message sent while locked through the gate again, as if sent now."""
        try:
            await again()
        except Exception:
            logger.exception("Could not take up the message sent while the chat was locked")
