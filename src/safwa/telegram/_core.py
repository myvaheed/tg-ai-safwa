from __future__ import annotations

import asyncio
import html
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from aiogram import BaseMiddleware, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, Message, TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..ai.service import AIAdvisor
from ..continuity import PersonaContinuity
from ..domain import (
    TAG_REFERENCE,
    VALUE_REFERENCE,
    toggle_card_category,
    toggle_card_energy_type,
    toggle_card_tag,
    toggle_card_value,
)
from ..enums import Category, EnergyType, MessageKind
from ..history import TelegramHistorySource, mark_message, register_message
from ..memory import MemoryFileStore
from ..models import CardCategory, CardEnergyType, CardTag, CardValue

logger = logging.getLogger(__name__)
router = Router(name="safwa")


@dataclass
class Services:
    sessions: async_sessionmaker[AsyncSession]
    advisor: AIAdvisor
    history: TelegramHistorySource
    memory: MemoryFileStore
    continuity: PersonaContinuity
    owner_id: int
    guard: GenerationGuard
    owner_name: str = "Owner"
    # Loaded from Settings; item citations stay plain text when the username is omitted.
    bot_username: str = ""


# The source id of a generation nobody asked for. Telegram message ids are positive, so a
# negative one cannot collide with a real message.
BACKGROUND_SOURCE_ID = -1


@dataclass(eq=False)
class QueuedMessage:
    text: str
    placeholder_message_id: int | None = None
    ready: asyncio.Event = field(default_factory=asyncio.Event)


class GenerationGuard:
    """Allows one AI generation at a time, identified by what started it.

    ``active_source_id`` is the Telegram message id being answered, or
    ``BACKGROUND_SOURCE_ID`` when nothing was asked.  Holding it is what makes the
    middleware reject callbacks and delete incoming messages, so only one answer is ever
    being written into the chat.  ``dialogue_revision`` is bumped by :meth:`cancel` and is
    how a generation already in flight learns to discard its result.
    """

    def __init__(self) -> None:
        self.active_source_id: int | None = None
        self.dialogue_revision = 0
        self.queue_messages = False
        self._queued_messages: list[QueuedMessage] = []

    @property
    def active(self) -> bool:
        return self.active_source_id is not None

    @property
    def background(self) -> bool:
        return self.active_source_id == BACKGROUND_SOURCE_ID

    async def acquire(self, source_id: int, *, queue_messages: bool = False) -> None:
        if self.active_source_id not in {None, source_id}:
            raise RuntimeError("Another foreground generation is active")
        self.active_source_id = source_id
        self.queue_messages = self.queue_messages or queue_messages

    def reserve(self, source_id: int, *, queue_messages: bool = False) -> bool:
        if self.active_source_id is not None:
            return self.active_source_id == source_id
        self.active_source_id = source_id
        self.queue_messages = queue_messages
        return True

    def reserve_background(self) -> bool:
        """Take the guard for a generation nobody asked for, or decline if it is held.

        Never takes it away from the owner; the caller retries later.
        """
        if self.active_source_id is not None:
            return False
        self.active_source_id = BACKGROUND_SOURCE_ID
        self.queue_messages = False
        return True

    def release(self, source_id: int | None = None) -> None:
        if source_id is not None and self.active_source_id != source_id:
            return
        self.active_source_id = None
        self.queue_messages = False

    def cancel(self) -> None:
        self.dialogue_revision += 1
        self.release()

    def begin_queue(self, text: str) -> QueuedMessage:
        queued = QueuedMessage(text=text)
        self._queued_messages.append(queued)
        return queued

    def finish_queue(self, queued: QueuedMessage, placeholder_message_id: int | None) -> None:
        queued.placeholder_message_id = placeholder_message_id
        queued.ready.set()

    def abort_queue(self, queued: QueuedMessage) -> None:
        if queued in self._queued_messages:
            self._queued_messages.remove(queued)
        queued.ready.set()

    async def drain_queue(self) -> list[QueuedMessage]:
        while self._queued_messages:
            snapshot = list(self._queued_messages)
            await asyncio.gather(*(queued.ready.wait() for queued in snapshot))
            if snapshot == self._queued_messages:
                self._queued_messages.clear()
                return snapshot
        return []


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
            if command and command != "/newsession":
                try:
                    await event.delete()
                    command_deleted = True
                except TelegramAPIError as error:
                    logger.warning("Could not delete operational command %s: %s", command, error)
        if services.guard.background:
            # The owner outranks a generation nobody asked for: drop it and take the message
            # normally, rather than deleting it the way a foreground collision would.
            services.guard.cancel()
        if isinstance(event, Message) and services.guard.active:
            if command == "/cancel":
                return await handler(event, data)
            if command == "/newsession":
                services.guard.cancel()
                return await handler(event, data)
            if event.message_id != services.guard.active_source_id:
                if services.guard.queue_messages and not command and event.text:
                    queued = services.guard.begin_queue(event.text)
                    try:
                        await event.delete()
                    except TelegramAPIError:
                        services.guard.abort_queue(queued)
                        services.guard.cancel()
                        return await handler(event, data)
                    placeholder_id: int | None = None
                    try:
                        placeholder_text, event_id = mark_message(
                            "Generating response... /cancel for cancelling.\n"
                            f"Queued: {html.escape(event.text)}",
                            MessageKind.UI_INPUT,
                        )
                        placeholder = await event.answer(
                            placeholder_text,
                            parse_mode=ParseMode.HTML,
                        )
                        placeholder_id = placeholder.message_id
                        async with services.sessions() as session:
                            await register_message(
                                session,
                                placeholder.chat.id,
                                placeholder.message_id,
                                "out",
                                MessageKind.UI_INPUT,
                                event_id=event_id,
                            )
                            await session.commit()
                    except Exception:
                        logger.exception("Could not render a queued-message placeholder")
                    finally:
                        services.guard.finish_queue(queued, placeholder_id)
                    return None
                if not command_deleted:
                    try:
                        await event.delete()
                    except TelegramAPIError:
                        services.guard.cancel()
                        return await handler(event, data)
                return None
        if isinstance(event, CallbackQuery) and services.guard.active:
            await event.answer("Safwa is responding. Use /cancel to stop it.", show_alert=True)
            return None
        reserved = False
        if (
            isinstance(event, Message)
            and not services.guard.active
            and bool(event.text)
            and not event.text.lstrip().startswith("/")
        ):
            reserved = services.guard.reserve(event.message_id, queue_messages=True)
        try:
            return await handler(event, data)
        finally:
            if reserved:
                services.guard.release(event.message_id)


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


@dataclass(frozen=True)
class RelationChoice:
    """One overlapping Card relationship, described once for both selector surfaces."""

    singular: str
    draft_field: str
    link_column: Any
    link_owner: Any
    payload_key: str
    toggle: Callable[..., Awaitable[Any]]
    parse: Callable[[Any], Any]


RELATION_CHOICES: dict[str, RelationChoice] = {
    "categories": RelationChoice(
        "category",
        "categories",
        CardCategory.category,
        CardCategory.card_id,
        "value",
        toggle_card_category,
        Category,
    ),
    "energy": RelationChoice(
        "energy",
        "energy_types",
        CardEnergyType.energy_type,
        CardEnergyType.card_id,
        "value",
        toggle_card_energy_type,
        EnergyType,
    ),
    "values": RelationChoice(
        "value",
        "value_ids",
        CardValue.value_id,
        CardValue.card_id,
        "value_id",
        toggle_card_value,
        int,
    ),
    "tags": RelationChoice(
        "tag",
        "tag_ids",
        CardTag.tag_id,
        CardTag.card_id,
        "tag_id",
        toggle_card_tag,
        int,
    ),
}
# Single-valued selectors map a choice field to the Card column it sets.
SINGLE_CHOICE_FIELDS = {
    "kind": "kind",
    "stage": "stage",
    "priority": "priority",
    "effort": "effort_points",
}
CHOICE_TITLES = {
    "kind": "Choose Kind",
    "stage": "Choose Stage",
    "priority": "Choose Priority",
    "effort": "Choose Effort",
    "categories": "Categories",
    "energy": "Energy",
    "values": "Direct Values",
    "tags": "Tags",
}
# The committed-Card and draft selectors cover the same fields; a draft additionally
# chooses its kind, which is immutable once the Card exists.
CARD_CHOICE_FIELDS = ("stage", "priority", "effort", *RELATION_CHOICES)
CARD_DRAFT_CHOICE_FIELDS = ("kind", *CARD_CHOICE_FIELDS)
CARD_DRAFT_RELATIONS = {
    f"card_create_toggle_{relation.singular}": (relation.draft_field, relation.payload_key)
    for relation in RELATION_CHOICES.values()
}
CARD_RELATION_TOGGLES = {
    f"card_toggle_{relation.singular}": field
    for field, relation in RELATION_CHOICES.items()
}
NAMED_CHOICE_FIELDS = frozenset({"values", "tags"})
# Tag and Value share one field-oriented item screen; the spec supplies the differences.
ITEM_REFERENCES = {"tag": TAG_REFERENCE, "value": VALUE_REFERENCE}
