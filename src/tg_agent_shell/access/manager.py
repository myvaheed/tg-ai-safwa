"""One owner of chat access, clear boundaries and delivery during a lock."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any
from uuid import uuid4

from aiogram.types import Message
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..cues.runtime import resume_cues
from ..foundation.clock import utcnow
from ..foundation.kinds import MessageKind
from ..history import CONVERSATION_KINDS, TelegramMessage, register_message
from ..telegram.chat import UNASKED_KINDS, send_prose
from ..telegram.model import CallbackToken, UiSession
from ..telegram.services import Services
from .credentials import matches_secret_word
from .model import AccessState, ChatClearBoundary, DeferredDelivery, Locked, Open, Unlocking

logger = logging.getLogger(__name__)
ENTER_SECRET_WORD = "Сначала введите Secret word."

Verifier = Callable[[AsyncSession], Awaitable[str | None]]
Home = Callable[[Message, Services], Awaitable[None]]


class AccessManager:
    def __init__(self, services: Services, verifier: Verifier, home: Home | None = None) -> None:
        self.services = services
        self.verifier = verifier
        self.home = home
        self._state: AccessState = Open()
        self._transition = asyncio.Lock()

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
        self.services.history.access_boundaries = True
        if await self.enabled():
            await self.lock(anchor)

    async def lock(self, anchor: Message) -> None:
        async with self._transition:
            if self.blocked:
                return
            self._state = Locked()
            await self._capture_unanswered(anchor.chat.id)
            await self._clear(anchor)

    async def clear(self, anchor: Message) -> None:
        """Leave the chat empty while keeping unanswered messages for the next Home."""
        async with self._transition:
            await self._capture_unanswered(anchor.chat.id)
            await self._clear(anchor, anchor.message_id)

    async def restore(self, anchor: Message) -> None:
        async with self._transition:
            await self._restore(anchor)

    async def _restore(self, anchor: Message) -> None:
        async with self.services.sessions() as session:
            rows = list(await session.scalars(
                select(DeferredDelivery)
                .where(DeferredDelivery.chat_id == anchor.chat.id)
                .order_by(DeferredDelivery.id)
            ))
        for row in rows:
            await self._deliver(anchor, row)
        async with self.services.sessions() as session:
            await session.execute(delete(DeferredDelivery).where(
                DeferredDelivery.id.in_([row.id for row in rows]),
            ))
            await session.commit()

    async def _capture_unanswered(self, chat_id: int) -> None:
        async with self.services.sessions() as session:
            last_user = (
                await session.scalar(
                    select(func.max(TelegramMessage.message_id)).where(
                        TelegramMessage.chat_id == chat_id,
                        TelegramMessage.kind == MessageKind.DIALOGUE_USER.value,
                    )
                )
                or 0
            )
            rows = await session.scalars(
                select(TelegramMessage)
                .where(
                    TelegramMessage.chat_id == chat_id,
                    TelegramMessage.direction == "out",
                    TelegramMessage.kind.in_(UNASKED_KINDS),
                    TelegramMessage.message_id > last_user,
                    TelegramMessage.text.is_not(None),
                )
                .order_by(TelegramMessage.message_id)
            )
            for note in rows:
                pending = await session.scalar(
                    select(DeferredDelivery.id).where(
                        DeferredDelivery.source_note_id == note.id,
                    )
                )
                if pending is None:
                    tagged = (
                        await session.get(DeferredDelivery, note.related_id)
                        if note.related_id is not None
                        else None
                    )
                    if (
                        tagged is not None
                        and tagged.chat_id == chat_id
                        and tagged.payload.get("text") == note.text
                    ):
                        note.text = None
                        note.reads_as = None
                        continue
                    session.add(
                        DeferredDelivery(
                            chat_id=chat_id,
                            source_note_id=note.id,
                            payload={
                                "text": note.text,
                                "kind": note.kind,
                                "reads_as": note.reads_as,
                                "at": note.created_at.isoformat(),
                                "passing_seconds": note.passing_seconds,
                            },
                        )
                    )
            await session.commit()

    async def _clear(self, anchor: Message, through: int = 0) -> None:
        services = self.services
        async with services.sessions() as session:
            last = max(
                through,
                await session.scalar(
                    select(func.max(TelegramMessage.message_id)).where(
                        TelegramMessage.chat_id == anchor.chat.id,
                    )
                )
                or 0,
            )
            boundary = await session.get(ChatClearBoundary, anchor.chat.id)
            if boundary is None:
                session.add(ChatClearBoundary(chat_id=anchor.chat.id, through_message_id=last))
            else:
                boundary.through_message_id = last
            await session.execute(delete(UiSession).where(UiSession.owner_id == services.owner_id))
            await session.execute(
                delete(CallbackToken).where(CallbackToken.owner_id == services.owner_id)
            )
            await session.commit()
        # Keep logical conversation records, including the passing words being restored.
        await services.chat.clear(
            anchor.bot,
            anchor.chat.id,
            last,
            keep=CONVERSATION_KINDS | UNASKED_KINDS,
            strict=True,
        )

    async def intercept(self, event: Any) -> bool:
        if not self.blocked:
            return False
        async with self._transition:
            if not self.blocked:
                if hasattr(event, "message_id"):
                    await event.delete()
                else:
                    await event.answer()
                return True
            return await self._locked_input(event)

    async def _locked_input(self, event: Any) -> bool:
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
            await self.services.chat.send(
                event,
                ENTER_SECRET_WORD,
                kind=MessageKind.STATUS.value,
                replace=False,
            )
        return True

    async def _unlock(self, anchor: Message) -> None:
        self._state = Unlocking()
        try:
            await self.services.turn.wait_idle()
            await self._capture_unanswered(anchor.chat.id)
            await self._clear(anchor, anchor.message_id)
            await self._restore(anchor)
            await resume_cues(self.services, anchor)
            self.services.owner_acted_at = utcnow()
            if self.home is not None:
                await self.home(anchor, self.services)
            self._state = Open()
        except Exception:
            logger.exception("Could not finish unlocking the chat")
        finally:
            if self.unlocking:
                self._state = Locked()

    async def _deliver(self, anchor: Message, row: DeferredDelivery) -> None:
        services, payload = self.services, row.payload
        async with services.sessions() as session:
            before = (
                await session.scalar(
                    select(func.max(TelegramMessage.message_id)).where(
                        TelegramMessage.chat_id == anchor.chat.id,
                    )
                )
                or 0
            )
        sent = await send_prose(
            anchor,
            services,
            payload["text"],
            kind=MessageKind(payload["kind"]),
            event_id=uuid4().hex,
            replace=False,
            reads_as=payload.get("reads_as"),
            related_id=row.id,
        )
        async with services.sessions() as session:
            source = await session.get(TelegramMessage, row.source_note_id)
            first = await session.scalar(
                select(TelegramMessage)
                .where(
                    TelegramMessage.chat_id == anchor.chat.id,
                    TelegramMessage.message_id > before,
                    TelegramMessage.message_id <= sent.message_id,
                    TelegramMessage.text.is_not(None),
                )
                .order_by(TelegramMessage.message_id)
            )
            if first is not None:
                if source is not None:
                    message_id = first.message_id
                    source.displayed_at = first.displayed_at or first.created_at
                    await session.delete(first)
                    await session.flush()
                    source.message_id = message_id
                else:
                    first.created_at = datetime.fromisoformat(payload["at"])
                    stored = await session.get(DeferredDelivery, row.id)
                    stored.source_note_id = first.id
            await session.commit()
        if payload.get("passing_seconds") is not None:
            await services.chat.let_pass(
                anchor,
                kind=payload["kind"],
                seconds=payload["passing_seconds"],
            )
