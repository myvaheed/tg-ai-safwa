from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from aiogram import BaseMiddleware, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Audio, CallbackQuery, Message, TelegramObject, VideoNote, Voice
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telegram_llm import Transcriber

from ..ai.advisor import AIAdvisor
from ..enums import Category, EnergyType
from ..features.cards.references import TAG_REFERENCE, VALUE_REFERENCE
from ..features.cards.use_cases import (
    toggle_card_category,
    toggle_card_energy_type,
    toggle_card_tag,
    toggle_card_value,
)
from ..features.checks.api import CHECK_VALUE_REFERENCE
from ..features.continuity.memory import MemoryFileStore
from ..features.continuity.persona import PersonaContinuity
from ..foundation.screens import ScreenCatalogue, ScreenCommand, TextInputFlow
from ..history import TelegramHistorySource
from ..models import CardCategory, CardEnergyType, CardTag, CardValue, Workspace
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
    # Every item type a feature publishes a screen for, and how one is cited.
    screens: ScreenCatalogue
    # Every screen the owner opens by name, the shell's own first.
    commands: tuple[ScreenCommand, ...]
    # What each inline button does, by the action its token carries.
    callback_actions: Mapping[str, CallbackHandler]
    # What each text editor writes the owner's typed value to.
    text_inputs: Mapping[str, TextInputFlow]
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
# Tag and Value share one field-oriented item screen; the specs supply the differences.
# Every spec of one item names the same model, and together they are what carries it: a
# Value is on Checks as well as Cards, and the delete question has to count both.
ITEM_CARRIERS = {
    "tag": (TAG_REFERENCE,),
    "value": (VALUE_REFERENCE, CHECK_VALUE_REFERENCE),
}
