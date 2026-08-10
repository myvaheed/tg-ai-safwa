from __future__ import annotations

import html
import logging
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from aiogram import BaseMiddleware, F, Router
from aiogram.enums import ChatAction, ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    TelegramObject,
)
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .ai.service import AIAdvisor, AIOutcome, ProposalService
from .analytics import render_retrospective_png, retrospective_data, retrospective_recommendations
from .continuity import (
    MemoryMaintenanceResult,
    PersonaContinuity,
    parse_memory_update_time,
    record_memory_run,
)
from .domain import (
    CARD_REFERENCE_SPECS,
    TAG_REFERENCE,
    VALUE_REFERENCE,
    DomainError,
    ReferenceSpec,
    StaleStateError,
    archive_subtree,
    archive_tag,
    archive_value,
    card_progress,
    create_card,
    create_tag,
    create_value,
    delete_subtree,
    edit_card_text,
    finish_action,
    finish_sprint,
    move_card,
    resolve_references,
    set_feedback,
    set_value_focus,
    snooze_reminders,
    sprint_metrics,
    start_sprint,
    toggle_card_category,
    toggle_card_energy_type,
    toggle_card_tag,
    toggle_card_value,
    update_card_fields,
    update_profile,
    update_tag_fields,
    update_value_fields,
    validate_action_fields,
    validate_blocked_fields,
)
from .enums import (
    EFFORT_POINTS,
    CardKind,
    CardStage,
    Category,
    EnergyType,
    MessageKind,
    Priority,
    ProposalStatus,
)
from .history import (
    SUBSESSION_RESULT_HEADER,
    HistoryBoundaryMissing,
    HistoryEntry,
    TelegramHistorySource,
    register_message,
)
from .memory import MemoryFileError, MemoryFileStore, estimate_tokens
from .models import (
    CallbackToken,
    Card,
    CardCategory,
    CardEnergyType,
    CardTag,
    CardValue,
    ChangeProposal,
    FeedbackQueue,
    ProposalChange,
    SavedRequest,
    Sprint,
    SummaryState,
    Tag,
    TelegramMessage,
    UiSession,
    UserProfile,
    Value,
    Workspace,
)
from .saved_requests import request_cards

_KIND_EMOJIS = {
    CardKind.GOAL.value: "🎯",
    CardKind.IDEA.value: "💡",
    CardKind.ACTION.value: "⭐️",
}
_CATEGORY_EMOJIS = {
    Category.SELF.value: "🌱",
    Category.CONTRIBUTION.value: "❤️",
    Category.WORK.value: "💰",
    Category.REST.value: "🔋",
}
_ENERGY_EMOJIS = {
    EnergyType.PHYSICAL.value: "💪",
    EnergyType.COGNITIVE.value: "🧠",
    EnergyType.SOCIAL.value: "🤝",
    EnergyType.VALUES.value: "💎",
}


def _typed_label(value: Any, emojis: dict[str, str]) -> str:
    raw = str(getattr(value, "value", value)).strip()
    normalized = raw.casefold()
    emoji = emojis.get(normalized)
    return f"{emoji} {normalized.title()}" if emoji else raw


def _typed_expression(values: Any, emojis: dict[str, str]) -> str:
    """Label an overlapping set of Categories or Energy types, or an em dash if empty."""
    items = values.split(",") if isinstance(values, str) else (values or [])
    labels = [_typed_label(item, emojis) for item in items if str(item).strip()]
    return ", ".join(labels) or "—"


def _with_notice(body: str, notice: str | None) -> str:
    """Carry a warning into the destination screen.

    A callback response replaces the current message, so a warning sent as its own
    message is overwritten by the next render.  It has to be part of that render.
    """
    return f"{html.escape(notice)}\n\n{body}" if notice else body


def _kind_label(value: Any) -> str:
    return _typed_label(value, _KIND_EMOJIS)


def _category_expression(values: Any) -> str:
    return _typed_expression(values, _CATEGORY_EMOJIS)


def _energy_expression(values: Any) -> str:
    return _typed_expression(values, _ENERGY_EMOJIS)


logger = logging.getLogger(__name__)
router = Router(name="safwa")

_PAGE_SIZE = 5
# Value and Tag selectors grow with the workspace, so they page instead of truncating.
_SELECTOR_PAGE_SIZE = 10
_NAMED_CHOICE_FIELDS = frozenset({"values", "tags"})
_REQUEST_RESULT_LIMIT = 25
# Tag and Value share one field-oriented item screen; the spec supplies the differences.
_ITEM_REFERENCES = {"tag": TAG_REFERENCE, "value": VALUE_REFERENCE}


async def _linked_card_count(session: AsyncSession, spec: ReferenceSpec, item_id: int) -> int:
    return int(
        await session.scalar(
            select(func.count()).select_from(spec.link_model).where(spec.link_column == item_id)
        )
        or 0
    )


@dataclass
class Services:
    sessions: async_sessionmaker[AsyncSession]
    advisor: AIAdvisor
    history: TelegramHistorySource
    memory: MemoryFileStore
    continuity: PersonaContinuity
    owner_id: int
    guard: GenerationGuard


class GenerationGuard:
    def __init__(self) -> None:
        self.active_source_id: int | None = None
        self.dialogue_revision = 0

    @property
    def active(self) -> bool:
        return self.active_source_id is not None

    async def acquire(self, source_id: int) -> None:
        if self.active_source_id not in {None, source_id}:
            raise RuntimeError("Another foreground generation is active")
        self.active_source_id = source_id

    def reserve(self, source_id: int) -> bool:
        if self.active_source_id is not None:
            return self.active_source_id == source_id
        self.active_source_id = source_id
        return True

    def release(self, source_id: int | None = None) -> None:
        if source_id is not None and self.active_source_id != source_id:
            return
        self.active_source_id = None

    def cancel(self) -> None:
        self.dialogue_revision += 1
        self.release()


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
        if isinstance(event, Message) and services.guard.active:
            if command == "/cancel":
                return await handler(event, data)
            if command == "/newsession":
                services.guard.cancel()
                return await handler(event, data)
            if event.message_id != services.guard.active_source_id:
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
            reserved = services.guard.reserve(event.message_id)
        try:
            return await handler(event, data)
        finally:
            if reserved:
                services.guard.release(event.message_id)


async def token_button(
    session: AsyncSession,
    owner_id: int,
    text: str,
    action: str,
    payload: dict[str, Any] | None = None,
) -> InlineKeyboardButton:
    token = secrets.token_urlsafe(9)
    session.add(
        CallbackToken(
            token=token,
            owner_id=owner_id,
            action=action,
            payload=payload or {},
            expires_at=datetime.now(UTC) + timedelta(hours=24),
        )
    )
    return InlineKeyboardButton(text=text, callback_data=f"cb:{token}")


async def send_registered(
    message: Message,
    services: Services,
    text: str,
    *,
    kind: MessageKind,
    markup: InlineKeyboardMarkup | None = None,
    related_id: int | None = None,
    replace: bool | None = None,
) -> Message:
    """Render a UI state, replacing an inline-action screen when possible.

    Command and ordinary-text handlers receive a user message, so their response
    remains a new bot message.  Callback handlers receive the bot's previous
    message and therefore update that message in place.  Text-entry actions opt
    out explicitly because their prompt must be a separate conversational turn.
    """
    should_replace = (
        bool(message.from_user and message.from_user.is_bot) if replace is None else replace
    )
    if should_replace:
        try:
            await message.edit_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)
            sent = message
        except TelegramAPIError as error:
            # Telegram rejects a no-op edit.  It is still the same rendered state.
            if "message is not modified" in str(error).casefold():
                sent = message
            else:
                logger.warning(
                    "Could not replace Telegram UI message %s; sending a new screen: %s",
                    message.message_id,
                    error,
                )
                sent = await message.answer(text, reply_markup=markup, parse_mode=ParseMode.HTML)
    else:
        sent = await message.answer(text, reply_markup=markup, parse_mode=ParseMode.HTML)
    async with services.sessions() as session:
        await register_message(session, sent.chat.id, sent.message_id, "out", kind, related_id)
        await session.commit()
    return sent


async def edit_registered_message(
    message: Message,
    services: Services,
    message_id: int,
    text: str,
    *,
    kind: MessageKind,
    markup: InlineKeyboardMarkup | None = None,
    related_id: int | None = None,
) -> None:
    """Replace a known bot UI message after consuming a separate user text message."""
    try:
        await message.bot.edit_message_text(
            text,
            chat_id=message.chat.id,
            message_id=message_id,
            reply_markup=markup,
            parse_mode=ParseMode.HTML,
        )
    except TelegramAPIError as error:
        if "message is not modified" not in str(error).casefold():
            raise
    async with services.sessions() as session:
        await register_message(
            session,
            message.chat.id,
            message_id,
            "out",
            kind,
            related_id,
        )
        await session.commit()


async def delete_text_input(message: Message, services: Services) -> bool:
    """Text entered into a field is operational UI input, not dialogue history."""
    async with services.sessions() as session:
        await register_message(
            session,
            message.chat.id,
            message.message_id,
            "in",
            MessageKind.UI_INPUT,
        )
        await session.commit()
    try:
        await message.delete()
    except TelegramAPIError as error:
        logger.warning("Could not delete UI field input %s: %s", message.message_id, error)
        return False
    return True


async def clear_message_markup(message: Message, message_id: int) -> None:
    try:
        await message.bot.edit_message_reply_markup(
            chat_id=message.chat.id,
            message_id=message_id,
            reply_markup=None,
        )
    except TelegramAPIError:
        pass


def proposal_change_summary(change: ProposalChange) -> str:
    target = f" #{change.entity_id}" if change.entity_id is not None else ""
    values = ", ".join(f"{key}={value!r}" for key, value in change.values.items())
    suffix = f": {values}" if values else ""
    return f"{change.action.title()} {change.entity.title()}{target}{suffix}"


async def dismiss_prior_ui(message: Message, services: Services) -> None:
    """Ensure an interaction screen is never left active above new dialogue."""
    ui_kinds = {
        MessageKind.DASHBOARD.value,
        MessageKind.CARD_EDITOR.value,
        MessageKind.APPROVAL.value,
    }
    async with services.sessions() as session:
        screens = list(
            await session.scalars(
                select(TelegramMessage)
                .where(
                    TelegramMessage.chat_id == message.chat.id,
                    TelegramMessage.direction == "out",
                    TelegramMessage.kind.in_(ui_kinds),
                    TelegramMessage.message_id < message.message_id,
                )
                .order_by(TelegramMessage.message_id.desc())
            )
        )

    for screen in screens:
        replacement: str | None = None
        if screen.kind == MessageKind.APPROVAL.value and screen.related_id:
            async with services.sessions() as session:
                proposal = await session.get(ChangeProposal, screen.related_id)
                if proposal is not None and proposal.status == "pending":
                    changes = list(
                        await session.scalars(
                            select(ProposalChange)
                            .where(ProposalChange.proposal_id == proposal.id)
                            .order_by(ProposalChange.position)
                        )
                    )
                    proposal.status = ProposalStatus.REJECTED.value
                    proposed = "\n".join(
                        f"• {html.escape(proposal_change_summary(change))}" for change in changes
                    )
                    await session.commit()
                    advisor = getattr(services, "advisor", None)
                    progress = (
                        await advisor.cancel_approval_for_target("proposal", proposal.id)
                        if advisor is not None
                        else None
                    )
                    if progress:
                        # Earlier items in the queue may already be saved, and this frozen
                        # screen becomes assistant history.  Report the whole request.
                        replacement = (
                            "<b>Request interrupted</b>\n"
                            "You continued the conversation, so the remaining proposals were "
                            "discarded.\n\n" + html.escape(progress)
                        )
                    else:
                        replacement = (
                            "<b>Proposal discarded</b>\n"
                            "You continued the conversation without saving it.\n\n" + proposed
                        )
        if replacement is not None:
            try:
                await message.bot.edit_message_text(
                    replacement,
                    chat_id=message.chat.id,
                    message_id=screen.message_id,
                    parse_mode=ParseMode.HTML,
                )
            except TelegramAPIError as error:
                logger.warning("Could not freeze proposal UI %s: %s", screen.message_id, error)
                try:
                    await message.bot.edit_message_reply_markup(
                        chat_id=message.chat.id,
                        message_id=screen.message_id,
                        reply_markup=None,
                    )
                except TelegramAPIError:
                    pass
                continue
            async with services.sessions() as session:
                await register_message(
                    session,
                    message.chat.id,
                    screen.message_id,
                    "out",
                    MessageKind.DIALOGUE_ASSISTANT,
                    screen.related_id,
                )
                await session.commit()
            continue

        try:
            await message.bot.delete_message(message.chat.id, screen.message_id)
        except TelegramAPIError:
            try:
                await message.bot.edit_message_reply_markup(
                    chat_id=message.chat.id,
                    message_id=screen.message_id,
                    reply_markup=None,
                )
            except TelegramAPIError:
                pass
        else:
            async with services.sessions() as session:
                stored = await session.scalar(
                    select(TelegramMessage).where(
                        TelegramMessage.chat_id == message.chat.id,
                        TelegramMessage.message_id == screen.message_id,
                    )
                )
                if stored is not None:
                    await session.delete(stored)
                    await session.commit()

    async with services.sessions() as session:
        await session.execute(delete(UiSession).where(UiSession.owner_id == services.owner_id))
        await session.commit()


def menu_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="☀️ Today", callback_data="nav:today"),
                InlineKeyboardButton(text="🏃 Sprint", callback_data="nav:sprint"),
            ],
            [
                InlineKeyboardButton(text="📚 Backlog", callback_data="nav:backlog"),
                InlineKeyboardButton(text="➕ Add", callback_data="nav:add"),
            ],
            [
                InlineKeyboardButton(text="💎 Values", callback_data="nav:values"),
                InlineKeyboardButton(text="🏷 Tags", callback_data="nav:tags"),
            ],
            [
                InlineKeyboardButton(text="💬 Advisor", callback_data="nav:advisor"),
                InlineKeyboardButton(text="🔎 Requests", callback_data="nav:requests"),
                InlineKeyboardButton(text="📊 Retro", callback_data="nav:retro"),
                InlineKeyboardButton(text="⚙️ Settings", callback_data="nav:settings"),
            ],
        ]
    )


def menu_row() -> list[InlineKeyboardButton]:
    """A consistent escape hatch for a screen reached through quick actions."""
    return [InlineKeyboardButton(text="↩️ Menu", callback_data="nav:home")]


def retro_back_row() -> list[InlineKeyboardButton]:
    """Return from a media-only retrospective without creating another screen."""
    return [InlineKeyboardButton(text="↩️ Menu", callback_data="nav:retro_back")]


def split_telegram_text(text: str, limit: int = 3_900) -> list[str]:
    """Split visible context messages without breaking the result protocol header."""
    text = text.strip()
    if not text:
        return []
    chunks: list[str] = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    chunks.append(text)
    return chunks


async def delete_message_range(message: Message, first_id: int, last_id: int) -> None:
    if last_id < first_id:
        return
    for offset in range(first_id, last_id + 1, 100):
        await message.bot.delete_messages(
            chat_id=message.chat.id,
            message_ids=list(range(offset, min(offset + 100, last_id + 1))),
        )


async def send_subsession_result(
    message: Message, services: Services, initial_request: str, result: str
) -> None:
    payload = f"{initial_request.strip()}\n\nSubsession result\n{result.strip()}"
    for index, chunk in enumerate(split_telegram_text(payload)):
        header = (
            SUBSESSION_RESULT_HEADER if index == 0 else f"{SUBSESSION_RESULT_HEADER} (continued)"
        )
        sent = await message.bot.send_message(
            message.chat.id,
            f"{header}\n{html.escape(chunk)}",
            parse_mode=ParseMode.HTML,
        )
        async with services.sessions() as session:
            await register_message(
                session,
                sent.chat.id,
                sent.message_id,
                "out",
                MessageKind.SUBSESSION_RESULT,
            )
            await session.commit()


async def end_subsession(
    message: Message,
    services: Services,
    *,
    start_message_id: int,
    instruction: str,
) -> None:
    active_start = await services.history.active_session_start(message.chat.id)
    if active_start is None or active_start.message_id != start_message_id:
        raise StaleStateError("This subsession has changed or is no longer active")
    await services.guard.acquire(message.message_id)
    try:
        await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
        transcript = await services.history.dialogue(message.chat.id)
        result = await services.advisor.compress_subsession(transcript, instruction)
        await delete_message_range(message, start_message_id, message.message_id)
        await send_subsession_result(message, services, active_start.text, result)
    finally:
        services.guard.release(message.message_id)


_PRIORITY_ORDER = {Priority.CRITICAL.value: 0, Priority.MEDIUM.value: 1, Priority.LOW.value: 2}


def _live_card_order(card: Card) -> tuple[bool, int, datetime]:
    """Hard Time first, then priority, then oldest — one ordering for every Card list."""
    return (not card.hard_time, _PRIORITY_ORDER[card.priority], card.created_at)


@dataclass(frozen=True)
class _Page:
    items: list[Any]
    index: int
    count: int

    @property
    def label(self) -> str:
        return f"page {self.index + 1}/{self.count}"


def _paginate(items: list[Any], page: int, size: int = _PAGE_SIZE) -> _Page:
    last = max(0, (len(items) - 1) // size)
    index = min(max(page, 0), last)
    return _Page(items[index * size : (index + 1) * size], index, last + 1)


def _paginate_cards(cards: list[Card], page: int) -> _Page:
    return _paginate(sorted(cards, key=_live_card_order), page)


async def _paging_row(
    session: AsyncSession,
    owner_id: int,
    page: _Page,
    action: str,
    payload: dict[str, Any],
) -> list[list[InlineKeyboardButton]]:
    row: list[InlineKeyboardButton] = []
    if page.index > 0:
        row.append(
            await token_button(
                session, owner_id, "◀ Previous", action, {**payload, "page": page.index - 1}
            )
        )
    if page.index + 1 < page.count:
        row.append(
            await token_button(
                session, owner_id, "Next ▶", action, {**payload, "page": page.index + 1}
            )
        )
    return [row] if row else []


async def render_dashboard(
    message: Message,
    services: Services,
    stage: CardStage,
    *,
    title: str,
    page: int = 0,
) -> None:
    async with services.sessions() as session:
        cards = list(
            await session.scalars(
                select(Card).where(
                    Card.effective_stage == stage.value,
                    Card.kind == CardKind.ACTION.value,
                    Card.archived_at.is_(None),
                )
            )
        )
        current = _paginate_cards(cards, page)
        back = {
            "kind": "dashboard",
            "stage": stage.value,
            "title": title,
            "page": current.index,
        }
        rows: list[list[InlineKeyboardButton]] = []
        descriptions: list[str] = []
        for card in current.items:
            metadata = [
                _kind_label(card.kind),
                card.priority.title(),
                f"{card.effort_points or '—'} EP",
            ]
            if card.hard_time:
                metadata.append("Hard time")
            if card.repeatable:
                metadata.append("Repeat")
            if card.blocked:
                metadata.append("Blocked")
            label = f"{card.title} · {' · '.join(metadata)}"
            descriptions.append(f"• {label}")
            rows.append(
                [
                    await token_button(
                        session,
                        services.owner_id,
                        label[:60],
                        "card_view",
                        {"id": card.id, "back": back},
                    )
                ]
            )
        rows.extend(
            await _paging_row(
                session,
                services.owner_id,
                current,
                "dashboard_page",
                {"stage": stage.value, "title": title},
            )
        )
        rows.append(menu_row())
        await session.commit()
    text = f"<b>{html.escape(title)}</b> · {current.label}\n"
    text += (
        "\n".join(html.escape(description) for description in descriptions) or "Nothing here yet."
    )
    await send_registered(
        message,
        services,
        text,
        kind=MessageKind.DASHBOARD,
        markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


def _new_card_creation_state() -> dict[str, Any]:
    return {
        "kind": CardKind.ACTION.value,
        "title": "",
        "note": "",
        "stage": CardStage.BACKLOG.value,
        "priority": Priority.MEDIUM.value,
        "hard_time": False,
        "blocked": False,
        "blocked_description": "",
        "effort_points": None,
        "repeatable": False,
        "categories": [],
        "energy_types": [],
        "value_ids": [],
        "tag_ids": [],
    }


def _sanitize_card_creation_state(state: dict[str, Any]) -> dict[str, Any]:
    clean = {**_new_card_creation_state(), **state}
    try:
        clean["kind"] = CardKind(clean["kind"]).value
    except ValueError:
        clean["kind"] = CardKind.ACTION.value
    try:
        clean["stage"] = CardStage(clean["stage"]).value
    except ValueError:
        clean["stage"] = CardStage.BACKLOG.value
    if clean["stage"] in {CardStage.DONE.value, CardStage.CANCELLED.value}:
        clean["stage"] = CardStage.BACKLOG.value
    if clean["kind"] != CardKind.ACTION.value:
        clean.update(
            effort_points=None,
            repeatable=False,
            categories=[],
            energy_types=[],
        )
    if not clean["blocked"]:
        clean["blocked_description"] = ""
    for field in ("categories", "energy_types", "value_ids", "tag_ids"):
        clean[field] = list(dict.fromkeys(clean.get(field) or []))
    return clean


def _card_creation_errors(state: dict[str, Any]) -> list[str]:
    """Report what still blocks Save, using the same rules the domain enforces.

    The draft is checked here only so Save can be hidden until it would succeed;
    ``create_card`` remains the authority and revalidates everything.
    """
    errors: list[str] = []
    if not str(state.get("title", "")).strip():
        errors.append("Add a title")
    for check in (
        lambda: validate_action_fields(
            state["kind"],
            state.get("effort_points"),
            bool(state.get("repeatable")),
            set(state.get("categories") or []),
            set(state.get("energy_types") or []),
        ),
        lambda: validate_blocked_fields(
            bool(state.get("blocked")), state.get("blocked_description")
        ),
    ):
        try:
            check()
        except DomainError as error:
            errors.append(str(error))
    return errors


async def card_creation_markup(
    session: AsyncSession, services: Services, state: dict[str, Any]
) -> InlineKeyboardMarkup:
    fields: list[tuple[str, str, dict[str, Any]]] = [
        ("🧩 Kind", "card_create_choose_kind", {}),
        ("✏️ Title", "card_create_edit_text", {"field": "title"}),
        ("📍 Stage", "card_create_choose_stage", {}),
        ("📝 Note", "card_create_edit_text", {"field": "note"}),
        ("⚠️ Priority", "card_create_choose_priority", {}),
        ("⏱ Hard Time", "card_create_toggle", {"field": "hard_time"}),
        ("🚧 Blocked", "card_create_toggle", {"field": "blocked"}),
    ]
    if state.get("blocked"):
        fields.append(
            (
                "📝 Blocked reason",
                "card_create_edit_text",
                {"field": "blocked_description"},
            )
        )
    if state["kind"] == CardKind.ACTION.value:
        fields.extend(
            [
                ("🔢 Effort", "card_create_choose_effort", {}),
                ("🔁 Repeat", "card_create_toggle", {"field": "repeatable"}),
                ("🏷 Categories", "card_create_choose_categories", {}),
                ("⚡ Energy", "card_create_choose_energy", {}),
            ]
        )
    fields.extend(
        [
            ("💎 Values", "card_create_choose_values", {}),
            ("🏷 Tags", "card_create_choose_tags", {}),
        ]
    )
    buttons = [await token_button(session, services.owner_id, *field) for field in fields]
    rows = [buttons[index : index + 2] for index in range(0, len(buttons), 2)]
    if not _card_creation_errors(state):
        rows.append([await token_button(session, services.owner_id, "✅ Save", "card_create_save")])
    rows.append(
        [await token_button(session, services.owner_id, "🗑 Discard", "card_create_discard")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def render_card_creation(
    message: Message,
    services: Services,
    *,
    replace_message_id: int | None = None,
) -> None:
    async with services.sessions() as session:
        editor = await session.scalar(
            select(UiSession).where(
                UiSession.owner_id == services.owner_id,
                UiSession.kind == "card_create",
            )
        )
        if editor is None:
            await send_registered(
                message,
                services,
                "Card creation is no longer active.",
                kind=MessageKind.ERROR,
                markup=menu_markup(),
            )
            return
        state = _sanitize_card_creation_state(dict(editor.state or {}))
        editor.state = state
        value_ids = list(state["value_ids"])
        tag_ids = list(state["tag_ids"])
        values = (
            list(await session.scalars(select(Value).where(Value.id.in_(value_ids))))
            if value_ids
            else []
        )
        tags = (
            list(await session.scalars(select(Tag).where(Tag.id.in_(tag_ids)))) if tag_ids else []
        )
        display = {
            **state,
            "value_names": [value.name for value in values],
            "tag_names": [tag.name for tag in tags],
        }
        text = _card_overview_text(display, heading="Create Card")
        errors = _card_creation_errors(state)
        if errors:
            text += "\n\n" + "\n".join(f"⚠️ {html.escape(error)}" for error in errors)
        markup = await card_creation_markup(session, services, state)
        editor_id = editor.id
        await session.commit()
    if replace_message_id is not None:
        await edit_registered_message(
            message,
            services,
            replace_message_id,
            text,
            kind=MessageKind.CARD_EDITOR,
            markup=markup,
            related_id=editor_id,
        )
    else:
        await send_registered(
            message,
            services,
            text,
            kind=MessageKind.CARD_EDITOR,
            markup=markup,
            related_id=editor_id,
        )


async def start_manual_card_creation(message: Message, services: Services) -> None:
    async with services.sessions() as session:
        await session.execute(delete(UiSession).where(UiSession.owner_id == services.owner_id))
        session.add(
            UiSession(
                owner_id=services.owner_id,
                kind="card_create",
                state=_new_card_creation_state(),
                expires_at=datetime.now(UTC) + timedelta(hours=24),
            )
        )
        await session.commit()
    await render_card_creation(message, services)


@router.message(Command("start"))
async def command_start(message: Message, services: Services) -> None:
    await send_registered(
        message,
        services,
        "<b>Safwa</b>\nYour personal agile advisor. Choose a dashboard or just write to me.",
        kind=MessageKind.DASHBOARD,
        markup=menu_markup(),
    )


@router.message(Command("newsession"))
async def command_newsession(message: Message, services: Services) -> None:
    parts = (message.text or "").split(maxsplit=1)
    initial_request = parts[1].strip() if len(parts) == 2 else ""
    if not initial_request:
        await send_registered(
            message,
            services,
            "Usage: <code>/newsession your initial request or situation</code>",
            kind=MessageKind.ERROR,
        )
        return
    async with services.sessions() as session:
        await register_message(
            session,
            message.chat.id,
            message.message_id,
            "in",
            MessageKind.SESSION_START,
        )
        await session.commit()
    await send_registered(
        message,
        services,
        "🆕 New Safwa session started. I will use this initial request as the dialogue boundary.",
        kind=MessageKind.RECEIPT,
        markup=InlineKeyboardMarkup(inline_keyboard=[menu_row()]),
    )


@router.message(Command("endsession"))
async def command_endsession(message: Message, services: Services) -> None:
    active_start = await services.history.active_session_start(message.chat.id)
    if active_start is None:
        await send_registered(
            message,
            services,
            "There is no active <code>/newsession</code> branch to end.",
            kind=MessageKind.ERROR,
            markup=InlineKeyboardMarkup(inline_keyboard=[menu_row()]),
        )
        return
    parts = (message.text or "").split(maxsplit=1)
    instruction = parts[1].strip() if len(parts) == 2 else ""
    async with services.sessions() as session:
        confirm = await token_button(
            session,
            services.owner_id,
            "✅ Compress and delete branch",
            "subsession_confirm",
            {
                "start_message_id": active_start.message_id,
                "instruction": instruction,
            },
        )
        cancel = await token_button(
            session,
            services.owner_id,
            "↩️ Keep branch",
            "subsession_cancel",
        )
        await session.commit()
    instruction_text = (
        html.escape(instruction) if instruction else "Use a concise planning/advisory result."
    )
    await send_registered(
        message,
        services,
        "<b>End this subsession?</b>\n"
        "Safwa will compress everything since the active <code>/newsession</code>, delete that "
        "branch from Telegram, and leave one compact context result.\n\n"
        f"<b>Initial request:</b>\n<blockquote>{html.escape(active_start.text)}</blockquote>\n\n"
        f"<b>Result instruction:</b>\n<blockquote>{instruction_text}</blockquote>",
        kind=MessageKind.APPROVAL,
        markup=InlineKeyboardMarkup(inline_keyboard=[[confirm], [cancel]]),
    )


@router.message(Command("today"))
async def command_today(message: Message, services: Services) -> None:
    await render_dashboard(message, services, CardStage.TODAY, title="Today")


@router.message(Command("backlog"))
async def command_backlog(message: Message, services: Services) -> None:
    await render_dashboard(message, services, CardStage.BACKLOG, title="Backlog")


@router.message(Command("sprint"))
async def command_sprint(message: Message, services: Services) -> None:
    async with services.sessions() as session:
        workspace = await session.get(Workspace, 1)
        if workspace and workspace.active_sprint_id:
            sprint = await session.get(Sprint, workspace.active_sprint_id)
            metrics = await sprint_metrics(session, workspace.active_sprint_id)
            start = await token_button(
                session, services.owner_id, "⏹ Finish early", "sprint_finish"
            )
            text = (
                f"<b>Sprint {sprint.number}</b> · {sprint.planned_start_date}–{sprint.planned_end_date}\n"
                f"Committed {metrics['committed']} · Added {metrics['added']} · "
                f"Done {metrics['completed']} · Cancelled {metrics['cancelled']}"
            )
        else:
            selected_effort = (
                await session.scalar(
                    select(func.coalesce(func.sum(Card.effort_points), 0)).where(
                        Card.kind == CardKind.ACTION.value,
                        Card.archived_at.is_(None),
                        Card.effective_stage.in_(
                            [
                                CardStage.SPRINT.value,
                                CardStage.TODAY.value,
                            ]
                        ),
                    )
                )
                or 0
            )
            profile = await session.get(UserProfile, 1)
            start = await token_button(
                session, services.owner_id, "▶️ Start 14-day Sprint", "sprint_start"
            )
            warning = ""
            if profile.capacity_effort_points and selected_effort > profile.capacity_effort_points:
                warning = f"\n⚠️ Above configured capacity ({profile.capacity_effort_points} EP)."
            text = (
                "<b>Planning</b>\nCards in Sprint and Today are preselected for the next Sprint."
                f"\nSelected effort: {selected_effort} EP{warning}"
            )
        await session.commit()
    await send_registered(
        message,
        services,
        text,
        kind=MessageKind.DASHBOARD,
        markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [start],
                menu_row(),
            ]
        ),
    )


async def command_add(message: Message, services: Services) -> None:
    await start_manual_card_creation(message, services)


@router.message(Command("advisor"))
async def command_advisor(message: Message, services: Services) -> None:
    await send_registered(
        message,
        services,
        "<b>Advisor</b>\nWrite naturally and Safwa can answer, analyze your planning, or prepare a "
        "reviewable change. Use <code>/newsession</code> for a focused branch.",
        kind=MessageKind.DASHBOARD,
        markup=InlineKeyboardMarkup(inline_keyboard=[menu_row()]),
    )


@router.message(Command("values"))
async def command_values(message: Message, services: Services) -> None:
    async with services.sessions() as session:
        values = list(
            await session.scalars(
                select(Value).where(Value.archived_at.is_(None)).order_by(Value.name)
            )
        )
        rows = [
            [
                await token_button(
                    session,
                    services.owner_id,
                    f"{'✅' if value.active else '○'} {value.name}",
                    "item_view",
                    {"entity": "value", "id": value.id},
                )
            ]
            for value in values
        ]
        rows.append(
            [
                await token_button(
                    session, services.owner_id, "➕ Add Value", "value_create_prompt", {}
                )
            ]
        )
        await session.commit()
    await send_registered(
        message,
        services,
        "<b>Values in focus</b>\nActive Values are injected into the advisor context.",
        kind=MessageKind.DASHBOARD,
        markup=InlineKeyboardMarkup(inline_keyboard=rows + [menu_row()]),
    )


@router.message(Command("tags"))
async def command_tags(message: Message, services: Services) -> None:
    async with services.sessions() as session:
        tags = list(
            await session.scalars(select(Tag).where(Tag.archived_at.is_(None)).order_by(Tag.name))
        )
        rows = [
            [
                await token_button(
                    session,
                    services.owner_id,
                    tag.name,
                    "item_view",
                    {"entity": "tag", "id": tag.id},
                )
            ]
            for tag in tags
        ]
        rows.append(
            [await token_button(session, services.owner_id, "➕ Add Tag", "tag_create_prompt", {})]
        )
        await session.commit()
    await send_registered(
        message,
        services,
        "<b>Tags</b>\nUse Tags to group Cards independently of Values.",
        kind=MessageKind.DASHBOARD,
        markup=InlineKeyboardMarkup(inline_keyboard=rows + [menu_row()]),
    )


async def render_item_editor(
    message: Message,
    services: Services,
    entity: str,
    *,
    mode: str,
    item_id: int | None = None,
    values: dict[str, str] | None = None,
    replace_message_id: int | None = None,
) -> None:
    if entity not in _ITEM_REFERENCES or mode not in {"create", "view"}:
        raise DomainError("Unsupported item editor")
    spec = _ITEM_REFERENCES[entity]
    linked_count = 0
    async with services.sessions() as session:
        item: Tag | Value | None = None
        if mode == "view":
            item = await session.get(spec.model, item_id)
            if item is None or item.archived_at is not None:
                raise DomainError(f"{entity.title()} does not exist")
            editor_values = {"name": item.name, "description": item.description}
            linked_count = await _linked_card_count(session, spec, item.id)
        else:
            editor_values = {"name": "", "description": "", **(values or {})}

        await session.execute(delete(UiSession).where(UiSession.owner_id == services.owner_id))
        state: dict[str, Any] = {
            "entity": entity,
            "mode": mode,
            "item_id": item_id,
            "values": editor_values,
            "message_id": replace_message_id or message.message_id,
        }
        session.add(
            UiSession(
                owner_id=services.owner_id,
                kind="item_editor",
                state=state,
                expires_at=datetime.now(UTC) + timedelta(minutes=30),
            )
        )
        name = await token_button(
            session,
            services.owner_id,
            "✏️ Name",
            "item_edit_text",
            {"entity": entity, "mode": mode, "id": item_id, "field": "name"},
        )
        description = await token_button(
            session,
            services.owner_id,
            "📝 Description",
            "item_edit_text",
            {"entity": entity, "mode": mode, "id": item_id, "field": "description"},
        )
        rows: list[list[InlineKeyboardButton]] = [[name, description]]
        if entity == "value" and mode == "view" and isinstance(item, Value):
            rows.append(
                [
                    await token_button(
                        session,
                        services.owner_id,
                        f"💎 Focus: {'On' if item.active else 'Off'}",
                        "item_toggle_focus",
                        {"id": item.id},
                    )
                ]
            )
        if mode == "create" and editor_values["name"].strip():
            rows.append(
                [
                    await token_button(
                        session,
                        services.owner_id,
                        f"✅ Create {entity.title()}",
                        "item_create",
                        {"entity": entity},
                    )
                ]
            )
        if mode == "view" and item is not None:
            rows.append(
                [
                    await token_button(
                        session,
                        services.owner_id,
                        f"Archive {entity.title()}",
                        "item_archive_prompt",
                        {"entity": entity, "id": item.id},
                    )
                ]
            )
        rows.append(
            [
                await token_button(
                    session,
                    services.owner_id,
                    "↩️ Back",
                    "item_back",
                    {"entity": entity},
                )
            ]
        )
        await session.commit()

    title = f"Create {entity.title()}" if mode == "create" else entity.title()
    body = (
        f"<b>{title}</b>\n"
        f"Name: {html.escape(editor_values['name'] or '—')}\n"
        f"Description: {html.escape(editor_values['description'] or '—')}"
        + (f"\nLinked Cards: {linked_count}" if mode == "view" else "")
    )
    markup = InlineKeyboardMarkup(inline_keyboard=rows)
    if replace_message_id is not None:
        await edit_registered_message(
            message,
            services,
            replace_message_id,
            body,
            kind=MessageKind.DASHBOARD,
            markup=markup,
            related_id=item_id,
        )
    else:
        await send_registered(
            message,
            services,
            body,
            kind=MessageKind.DASHBOARD,
            markup=markup,
            related_id=item_id,
        )


async def render_item_text_prompt(
    message: Message,
    services: Services,
    *,
    entity: str,
    mode: str,
    item_id: int | None,
    field: str,
) -> None:
    if field not in {"name", "description"}:
        raise DomainError("Unsupported text field")
    async with services.sessions() as session:
        editor = await session.scalar(
            select(UiSession).where(UiSession.owner_id == services.owner_id)
        )
        if editor is None or editor.kind != "item_editor":
            raise DomainError("Item editor expired")
        state = dict(editor.state)
        state["field"] = field
        state["message_id"] = message.message_id
        editor.kind = "item_text"
        editor.state = state
        back = await token_button(
            session,
            services.owner_id,
            "↩️ Back",
            "item_text_back",
            {"entity": entity, "mode": mode, "id": item_id},
        )
        await session.commit()
    current = str(state.get("values", {}).get(field, ""))
    await send_registered(
        message,
        services,
        f"<b>Current {html.escape(field)}</b>: {html.escape(current or '—')}\n\n"
        f"Set new {html.escape(field.title())}",
        kind=MessageKind.DASHBOARD,
        markup=InlineKeyboardMarkup(inline_keyboard=[[back]]),
    )


@router.message(Command("requests"))
async def command_requests(message: Message, services: Services) -> None:
    """Show AI-authored saved queries; creation intentionally remains advisor-only."""
    async with services.sessions() as session:
        requests = list(
            await session.scalars(
                select(SavedRequest)
                .where(SavedRequest.archived_at.is_(None))
                .order_by(SavedRequest.name)
            )
        )
        rows = [
            [
                await token_button(
                    session,
                    services.owner_id,
                    request.name,
                    "request_view",
                    {"id": request.id},
                )
            ]
            for request in requests
        ]
        await session.commit()
    await send_registered(
        message,
        services,
        "<b>Requests</b>\nSaved card queries created by your advisor.",
        kind=MessageKind.DASHBOARD,
        markup=InlineKeyboardMarkup(inline_keyboard=rows + [menu_row()]),
    )


async def render_saved_request(message: Message, services: Services, request_id: int) -> None:
    async with services.sessions() as session:
        request = await session.get(SavedRequest, request_id)
        if request is None or request.archived_at is not None:
            raise DomainError("Request no longer exists")
        matches = await request_cards(session, request.query_sql)
        cards = matches[:_REQUEST_RESULT_LIMIT]
        rows = [
            [
                await token_button(
                    session,
                    services.owner_id,
                    f"{_kind_label(card.kind)} · {card.title}"[:60],
                    "card_view",
                    {"id": card.id, "back": {"kind": "request", "id": request.id}},
                )
            ]
            for card in cards
        ]
        rows.append(
            [
                await token_button(
                    session,
                    services.owner_id,
                    "↻ Refresh",
                    "request_view",
                    {"id": request.id},
                )
            ]
        )
        rows.append(menu_row())
        await session.commit()
    details = request.description or "No description."
    details += f"\n\n{len(matches)} matching card{'s' if len(matches) != 1 else ''}"
    if len(matches) > _REQUEST_RESULT_LIMIT:
        details += f" (showing first {_REQUEST_RESULT_LIMIT})"
    await send_registered(
        message,
        services,
        f"<b>{html.escape(request.name)}</b>\n{html.escape(details)}",
        kind=MessageKind.DASHBOARD,
        markup=InlineKeyboardMarkup(inline_keyboard=rows),
        related_id=request.id,
    )


@router.message(Command("memory"))
async def command_memory(message: Message, services: Services) -> None:
    snapshot = await services.memory.sync()
    if not snapshot.valid:
        text = "<b>memory.md needs attention</b>\n" + html.escape(snapshot.error or "Invalid file")
    else:
        text = f"<b>Persistent memory</b> · {snapshot.estimated_tokens}/4000 tokens\n" + (
            "\n".join(f"{i}. {html.escape(fact)}" for i, fact in enumerate(snapshot.facts, 1))
            or "Empty"
        )
    await send_registered(message, services, text, kind=MessageKind.DASHBOARD)


@router.message(Command("syncmem"))
async def command_syncmem(message: Message, services: Services) -> None:
    if (message.text or "").partition(" ")[2].strip():
        await send_registered(message, services, "Usage: /syncmem", kind=MessageKind.ERROR)
        return
    if services.guard.active:
        await send_registered(
            message,
            services,
            "Wait for the current advisor response, then retry /syncmem.",
            kind=MessageKind.ERROR,
        )
        return
    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    result = await services.continuity.maintain_memory(message.chat.id)
    if result == MemoryMaintenanceResult.UPDATED:
        await record_memory_run(services.sessions)
        text, kind = "Memory synchronized from Telegram dialogue.", MessageKind.RECEIPT
    elif result == MemoryMaintenanceResult.CURRENT:
        await record_memory_run(services.sessions)
        text, kind = "Memory is already synchronized.", MessageKind.RECEIPT
    elif result == MemoryMaintenanceResult.BUSY:
        text, kind = "Memory synchronization is already running.", MessageKind.ERROR
    elif result == MemoryMaintenanceResult.BOUNDARY_MISSING:
        text, kind = (
            "No /newsession or Summary boundary was found in Telegram. "
            "Start with /newsession followed by your initial request.",
            MessageKind.ERROR,
        )
    else:
        text, kind = "memory.md needs attention; synchronization was not run.", MessageKind.ERROR
    await send_registered(message, services, text, kind=kind)


@router.message(Command("mem"))
@router.message(Command("remember"))
async def command_remember(message: Message, services: Services) -> None:
    fact = (message.text or "").partition(" ")[2].strip()
    if not fact:
        await send_registered(
            message, services, "Usage: /mem one durable fact", kind=MessageKind.ERROR
        )
        return
    await services.memory.append_manual(fact)
    await send_registered(message, services, "Remembered in memory.md.", kind=MessageKind.RECEIPT)


@router.message(Command("forget"))
async def command_forget(message: Message, services: Services) -> None:
    raw = (message.text or "").partition(" ")[2].strip()
    try:
        line = int(raw)
        await services.memory.forget_line(line)
    except (ValueError, MemoryFileError) as error:
        await send_registered(message, services, html.escape(str(error)), kind=MessageKind.ERROR)
        return
    await send_registered(
        message, services, "Forgotten and memory.md updated.", kind=MessageKind.RECEIPT
    )


@router.message(Command("retro"))
async def command_retro(message: Message, services: Services) -> None:
    async with services.sessions() as session:
        sprint = await session.scalar(
            select(Sprint).where(Sprint.status == "finished").order_by(Sprint.number.desc())
        )
        if sprint is None:
            await send_registered(
                message,
                services,
                "No finished Sprint yet.",
                kind=MessageKind.DASHBOARD,
                markup=InlineKeyboardMarkup(inline_keyboard=[menu_row()]),
            )
            return
        data = await retrospective_data(session, sprint.id)
    png = render_retrospective_png(data)
    sent = await message.answer_photo(
        BufferedInputFile(png, filename=f"sprint-{sprint.number}-retro.png"),
        caption=f"Sprint {sprint.number} retrospective\n"
        + "\n".join(retrospective_recommendations(data)),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[retro_back_row()]),
    )
    async with services.sessions() as session:
        await register_message(
            session, sent.chat.id, sent.message_id, "out", MessageKind.RETROSPECTIVE_PNG, sprint.id
        )
        await session.commit()


async def render_feedback(
    message: Message, services: Services, *, notice: str | None = None
) -> None:
    async with services.sessions() as session:
        pending = list(
            await session.scalars(
                select(FeedbackQueue)
                .where(FeedbackQueue.answered_at.is_(None))
                .order_by(FeedbackQueue.created_at)
            )
        )
        if not pending:
            await send_registered(
                message,
                services,
                _with_notice("No completion feedback pending.", notice),
                kind=MessageKind.DASHBOARD,
                markup=InlineKeyboardMarkup(inline_keyboard=[menu_row()]),
            )
            return
        item = pending[0]
        card = await session.get(Card, item.card_id)
        yes = await token_button(
            session, services.owner_id, "Yes 🙂", "feedback", {"id": item.id, "liked": True}
        )
        no = await token_button(
            session, services.owner_id, "No 🙁", "feedback", {"id": item.id, "liked": False}
        )
        await session.commit()
    await send_registered(
        message,
        services,
        _with_notice(
            f"<b>Feedback 1/{len(pending)}</b>\n"
            f"Did you like doing <b>{html.escape(card.title)}</b>?",
            notice,
        ),
        kind=MessageKind.DASHBOARD,
        markup=InlineKeyboardMarkup(inline_keyboard=[[yes, no], menu_row()]),
        related_id=card.id,
    )


@router.message(Command("feedback"))
async def command_feedback(message: Message, services: Services) -> None:
    await render_feedback(message, services)


@router.message(Command("settings"))
async def command_settings(message: Message, services: Services) -> None:
    async with services.sessions() as session:
        profile = await session.get(UserProfile, 1)
        workspace = await session.get(Workspace, 1)
    memory_update_time = (
        profile.memory_update_time.strftime("%H:%M") if profile.memory_update_time else "off"
    )
    await send_registered(
        message,
        services,
        "<b>Settings</b>\n"
        f"About me: {html.escape(profile.about_me or '—')}\n"
        f"Advisor instructions: {html.escape(profile.advisor_instructions or '—')}\n"
        f"Wake/bed: {profile.wake_time or '—'} / {profile.bed_time or '—'}\n"
        f"Quiet hours: {profile.quiet_start or '—'}–{profile.quiet_end or '—'}\n"
        f"Sprint capacity: {profile.capacity_effort_points or '—'} EP\n"
        f"Memory sync: {memory_update_time} ({workspace.timezone})\n"
        "Edit with /setabout, /setadvisor, /setwake, /setbed, /setquiet, /setcapacity, "
        "or /setmemtime HH:MM|off.",
        kind=MessageKind.DASHBOARD,
        markup=InlineKeyboardMarkup(inline_keyboard=[menu_row()]),
    )


@router.message(Command("setabout"))
async def command_setabout(message: Message, services: Services) -> None:
    value = (message.text or "").partition(" ")[2].strip()
    async with services.sessions() as session:
        await update_profile(session, about_me=value)
        await session.commit()
    await send_registered(message, services, "About Me updated.", kind=MessageKind.RECEIPT)


@router.message(Command("setadvisor"))
async def command_setadvisor(message: Message, services: Services) -> None:
    value = (message.text or "").partition(" ")[2].strip()
    async with services.sessions() as session:
        await update_profile(session, advisor_instructions=value)
        await session.commit()
    await send_registered(
        message, services, "Advisor Instructions updated.", kind=MessageKind.RECEIPT
    )


async def update_profile_field(
    message: Message, services: Services, field: str, value: Any
) -> None:
    async with services.sessions() as session:
        await update_profile(session, **{field: value})
        await session.commit()
    await send_registered(message, services, "Settings updated.", kind=MessageKind.RECEIPT)


@router.message(Command("setwake"))
async def command_setwake(message: Message, services: Services) -> None:
    value = datetime.strptime((message.text or "").partition(" ")[2].strip(), "%H:%M").time()
    await update_profile_field(message, services, "wake_time", value)


@router.message(Command("setbed"))
async def command_setbed(message: Message, services: Services) -> None:
    value = datetime.strptime((message.text or "").partition(" ")[2].strip(), "%H:%M").time()
    await update_profile_field(message, services, "bed_time", value)


@router.message(Command("setquiet"))
async def command_setquiet(message: Message, services: Services) -> None:
    raw = (message.text or "").partition(" ")[2].strip()
    start, end = [datetime.strptime(item.strip(), "%H:%M").time() for item in raw.split("-", 1)]
    async with services.sessions() as session:
        await update_profile(session, quiet_start=start, quiet_end=end)
        await session.commit()
    await send_registered(message, services, "Quiet hours updated.", kind=MessageKind.RECEIPT)


@router.message(Command("snooze"))
async def command_snooze(message: Message, services: Services) -> None:
    raw = (message.text or "").partition(" ")[2].strip()
    minutes = int(raw) if raw else 60
    if not 1 <= minutes <= 24 * 60:
        raise DomainError("Snooze must be between 1 and 1440 minutes")
    async with services.sessions() as session:
        await snooze_reminders(session, datetime.now(UTC) + timedelta(minutes=minutes))
        await session.commit()
    await send_registered(
        message, services, f"Reminders snoozed for {minutes} minutes.", kind=MessageKind.RECEIPT
    )


@router.message(Command("setcapacity"))
async def command_setcapacity(message: Message, services: Services) -> None:
    raw = (message.text or "").partition(" ")[2].strip()
    value = int(raw) if raw else None
    if value is not None and value <= 0:
        raise DomainError("Capacity must be positive or omitted")
    await update_profile_field(message, services, "capacity_effort_points", value)


@router.message(Command("setmemtime"))
async def command_setmemtime(message: Message, services: Services) -> None:
    raw = (message.text or "").partition(" ")[2].strip()
    try:
        value = parse_memory_update_time(raw)
    except ValueError as error:
        await send_registered(message, services, html.escape(str(error)), kind=MessageKind.ERROR)
        return
    await update_profile_field(message, services, "memory_update_time", value)


@router.message(Command("status"))
async def command_status(message: Message, services: Services) -> None:
    memory = await services.memory.sync()
    async with services.sessions() as session:
        workspace = await session.get(Workspace, 1)
        feedback = (
            await session.scalar(
                select(func.count(FeedbackQueue.id)).where(FeedbackQueue.answered_at.is_(None))
            )
            or 0
        )
    await send_registered(
        message,
        services,
        f"<b>Status</b>\nMode: {workspace.mode}\nRevision: {workspace.revision}\n"
        f"Feedback: {feedback}\nMemory: {'OK' if memory.valid else 'ERROR'}",
        kind=MessageKind.DASHBOARD,
    )


@router.message(Command("cancel"))
async def command_cancel(message: Message, services: Services) -> None:
    services.guard.cancel()
    await send_registered(
        message, services, "Current generation cancelled.", kind=MessageKind.RECEIPT
    )


@router.callback_query(F.data.startswith("nav:"))
async def navigation(callback: CallbackQuery, services: Services) -> None:
    await callback.answer()
    if not callback.message:
        return
    action = callback.data.split(":", 1)[1]
    if action == "retro_back":
        # Telegram cannot turn a photo message into a text message.  The screen
        # underneath is still the menu that opened the retrospective, so remove
        # only the media screen rather than sending an unnecessary new message.
        await callback.message.delete()
        return
    handlers = {
        "home": command_start,
        "today": command_today,
        "sprint": command_sprint,
        "backlog": command_backlog,
        "add": command_add,
        "values": command_values,
        "tags": command_tags,
        "requests": command_requests,
        "advisor": command_advisor,
        "retro": command_retro,
        "settings": command_settings,
    }
    if action != "add":
        async with services.sessions() as session:
            await session.execute(delete(UiSession).where(UiSession.owner_id == services.owner_id))
            await session.commit()
    await handlers[action](callback.message, services)


async def choice_screen(
    message: Message,
    services: Services,
    title: str,
    choices: list[tuple[str, str, dict[str, Any]]],
    *,
    back: tuple[str, str, dict[str, Any]] | None = None,
    paging: tuple[_Page, str, dict[str, Any]] | None = None,
) -> None:
    heading = title if paging is None or paging[0].count == 1 else f"{title} · {paging[0].label}"
    async with services.sessions() as session:
        rows = [
            [await token_button(session, services.owner_id, text, action, payload)]
            for text, action, payload in choices
        ]
        if paging is not None:
            page, page_action, page_payload = paging
            rows.extend(
                await _paging_row(session, services.owner_id, page, page_action, page_payload)
            )
        if back:
            rows.append([await token_button(session, services.owner_id, *back)])
        await session.commit()
    await send_registered(
        message,
        services,
        f"<b>{html.escape(heading)}</b>",
        kind=MessageKind.CARD_EDITOR,
        markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


async def _clear_ui_sessions(session: AsyncSession, owner_id: int) -> None:
    await session.execute(delete(UiSession).where(UiSession.owner_id == owner_id))


async def _require_card_draft(session: AsyncSession, owner_id: int) -> UiSession:
    draft = await session.scalar(
        select(UiSession).where(
            UiSession.owner_id == owner_id,
            UiSession.kind == "card_create",
        )
    )
    if draft is None:
        raise DomainError("Card creation is no longer active")
    return draft


async def _card_editor_back_state(session: AsyncSession, owner_id: int) -> dict[str, Any]:
    """Keep the navigation trail of the Card screen a focused prompt replaces."""
    editor = await session.scalar(select(UiSession).where(UiSession.owner_id == owner_id))
    if editor is None or editor.kind != "card_editor":
        return {}
    return dict(editor.state.get("back", {}))


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
class _RelationChoice:
    """One overlapping Card relationship, described once for both selector surfaces."""

    singular: str
    draft_field: str
    link_column: Any
    link_owner: Any
    payload_key: str
    toggle: Callable[..., Awaitable[Any]]
    parse: Callable[[Any], Any]


_RELATION_CHOICES: dict[str, _RelationChoice] = {
    "categories": _RelationChoice(
        "category",
        "categories",
        CardCategory.category,
        CardCategory.card_id,
        "value",
        toggle_card_category,
        Category,
    ),
    "energy": _RelationChoice(
        "energy",
        "energy_types",
        CardEnergyType.energy_type,
        CardEnergyType.card_id,
        "value",
        toggle_card_energy_type,
        EnergyType,
    ),
    "values": _RelationChoice(
        "value",
        "value_ids",
        CardValue.value_id,
        CardValue.card_id,
        "value_id",
        toggle_card_value,
        int,
    ),
    "tags": _RelationChoice(
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
_SINGLE_CHOICE_FIELDS = {
    "kind": "kind",
    "stage": "stage",
    "priority": "priority",
    "effort": "effort_points",
}
_CHOICE_TITLES = {
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
_CARD_CHOICE_FIELDS = ("stage", "priority", "effort", *_RELATION_CHOICES)
_CARD_DRAFT_CHOICE_FIELDS = ("kind", *_CARD_CHOICE_FIELDS)
_CARD_DRAFT_RELATIONS = {
    f"card_create_toggle_{relation.singular}": (relation.draft_field, relation.payload_key)
    for relation in _RELATION_CHOICES.values()
}
_CARD_RELATION_TOGGLES = {
    f"card_toggle_{relation.singular}": field
    for field, relation in _RELATION_CHOICES.items()
}


# --- Tag and Value item screens ------------------------------------------------


async def _on_item_create_prompt(context: CallbackContext) -> None:
    entity = "value" if context.action == "value_create_prompt" else "tag"
    await render_item_editor(context.message, context.services, entity, mode="create")


async def _on_item_view(context: CallbackContext) -> None:
    await render_item_editor(
        context.message,
        context.services,
        context.payload["entity"],
        mode="view",
        item_id=context.payload["id"],
    )


async def _on_item_edit_text(context: CallbackContext) -> None:
    await render_item_text_prompt(
        context.message,
        context.services,
        entity=context.payload["entity"],
        mode=context.payload["mode"],
        item_id=context.payload.get("id"),
        field=context.payload["field"],
    )


async def _on_item_text_back(context: CallbackContext) -> None:
    async with context.sessions() as session:
        editor = await session.scalar(
            select(UiSession).where(UiSession.owner_id == context.owner_id)
        )
        values = dict(editor.state.get("values", {})) if editor is not None else {}
    await render_item_editor(
        context.message,
        context.services,
        context.payload["entity"],
        mode=context.payload["mode"],
        item_id=context.payload.get("id"),
        values=values,
    )


async def _on_item_create(context: CallbackContext) -> None:
    async with context.sessions() as session:
        editor = await session.scalar(
            select(UiSession).where(UiSession.owner_id == context.owner_id)
        )
        if editor is None or editor.kind != "item_editor":
            raise DomainError("Item editor expired")
        values = dict(editor.state.get("values", {}))
        create = create_value if context.payload["entity"] == "value" else create_tag
        item = await create(session, values.get("name", ""), values.get("description", ""))
        await session.commit()
    await render_item_editor(
        context.message,
        context.services,
        context.payload["entity"],
        mode="view",
        item_id=item.id,
    )


async def _on_item_toggle_focus(context: CallbackContext) -> None:
    async with context.sessions() as session:
        await set_value_focus(session, context.payload["id"])
        await session.commit()
    await render_item_editor(
        context.message,
        context.services,
        "value",
        mode="view",
        item_id=context.payload["id"],
    )


async def _on_item_archive_prompt(context: CallbackContext) -> None:
    entity = context.payload["entity"]
    spec = _ITEM_REFERENCES[entity]
    async with context.sessions() as session:
        item = await session.get(spec.model, context.payload["id"])
        if item is None or item.archived_at is not None:
            raise DomainError(f"{entity.title()} does not exist")
        linked_count = await _linked_card_count(session, spec, item.id)
        confirm = await token_button(
            session,
            context.owner_id,
            f"Archive {entity.title()}",
            "item_archive_confirm",
            {"entity": entity, "id": item.id},
        )
        back = await token_button(
            session,
            context.owner_id,
            "↩️ Back",
            "item_view",
            {"entity": entity, "id": item.id},
        )
        await session.commit()
    await send_registered(
        context.message,
        context.services,
        f"<b>Archive {html.escape(entity.title())}?</b>\n"
        f"{html.escape(item.name)} will be removed from active lists and unlinked from "
        f"{linked_count} Card(s).",
        kind=MessageKind.APPROVAL,
        markup=InlineKeyboardMarkup(inline_keyboard=[[confirm], [back]]),
        related_id=item.id,
    )


async def _on_item_archive_confirm(context: CallbackContext) -> None:
    entity = context.payload["entity"]
    async with context.sessions() as session:
        archive = archive_tag if entity == "tag" else archive_value
        item, linked_count = await archive(session, context.payload["id"])
        await _clear_ui_sessions(session, context.owner_id)
        back = await token_button(
            session,
            context.owner_id,
            f"Back to {entity.title()}s",
            "item_back",
            {"entity": entity},
        )
        await session.commit()
    await send_registered(
        context.message,
        context.services,
        f"Archived <b>{html.escape(item.name)}</b>. Removed {linked_count} Card link(s).",
        kind=MessageKind.RECEIPT,
        markup=InlineKeyboardMarkup(inline_keyboard=[[back]]),
        related_id=item.id,
    )


async def _on_item_back(context: CallbackContext) -> None:
    async with context.sessions() as session:
        await _clear_ui_sessions(session, context.owner_id)
        await session.commit()
    listing = command_values if context.payload["entity"] == "value" else command_tags
    await listing(context.message, context.services)


async def _on_request_view(context: CallbackContext) -> None:
    await render_saved_request(context.message, context.services, context.payload["id"])


# --- Subsessions ---------------------------------------------------------------


async def _on_subsession_confirm(context: CallbackContext) -> None:
    await send_registered(
        context.message,
        context.services,
        "<b>Compressing subsession…</b>",
        kind=MessageKind.APPROVAL,
    )
    try:
        await end_subsession(
            context.message,
            context.services,
            start_message_id=int(context.payload["start_message_id"]),
            instruction=str(context.payload.get("instruction", "")),
        )
    except Exception:
        logger.exception("Could not end Safwa subsession")
        await send_registered(
            context.message,
            context.services,
            "Safwa could not compress the subsession. The branch was not deleted.",
            kind=MessageKind.ERROR,
            markup=InlineKeyboardMarkup(inline_keyboard=[menu_row()]),
        )


async def _on_subsession_cancel(context: CallbackContext) -> None:
    await send_registered(
        context.message,
        context.services,
        "Subsession end cancelled. The branch is unchanged.",
        kind=MessageKind.RECEIPT,
        markup=InlineKeyboardMarkup(inline_keyboard=[menu_row()]),
    )


# --- Transient manual Card draft -----------------------------------------------


async def _on_card_draft_view(context: CallbackContext) -> None:
    async with context.sessions() as session:
        draft = await session.scalar(
            select(UiSession).where(UiSession.owner_id == context.owner_id)
        )
        if draft is None:
            raise DomainError("Card creation is no longer active")
        state = dict(draft.state or {})
        state.pop("input_field", None)
        state.pop("message_id", None)
        draft.kind = "card_create"
        draft.state = _sanitize_card_creation_state(state)
        await session.commit()
    await render_card_creation(context.message, context.services)


async def _on_card_draft_edit_text(context: CallbackContext) -> None:
    field = context.payload["field"]
    async with context.sessions() as session:
        draft = await _require_card_draft(session, context.owner_id)
        state = dict(draft.state or {})
        current = str(state.get(field) or "")
        state.update(input_field=field, message_id=context.message.message_id)
        draft.kind = "card_create_text"
        draft.state = state
        back = await token_button(session, context.owner_id, "↩️ Back", "card_create_view")
        await session.commit()
    await send_registered(
        context.message,
        context.services,
        f"<b>Current {html.escape(field.replace('_', ' '))}</b>: "
        f"{html.escape(current or '—')}\n\n"
        f"Set new {html.escape(field.replace('_', ' ').title())}",
        kind=MessageKind.CARD_EDITOR,
        markup=InlineKeyboardMarkup(inline_keyboard=[[back]]),
    )


async def _on_card_draft_toggle(context: CallbackContext) -> None:
    async with context.sessions() as session:
        draft = await _require_card_draft(session, context.owner_id)
        state = dict(draft.state or {})
        field = context.payload["field"]
        state[field] = not bool(state.get(field))
        draft.state = _sanitize_card_creation_state(state)
        await session.commit()
    await render_card_creation(context.message, context.services)


async def _on_card_draft_chooser(context: CallbackContext) -> None:
    await handle_card_creation_chooser(
        context.message,
        context.services,
        context.action,
        page=int(context.payload.get("page", 0)),
    )


async def _on_card_draft_set(context: CallbackContext) -> None:
    async with context.sessions() as session:
        draft = await _require_card_draft(session, context.owner_id)
        state = dict(draft.state or {})
        state[context.payload["field"]] = context.payload["value"]
        draft.state = _sanitize_card_creation_state(state)
        await session.commit()
    await render_card_creation(context.message, context.services)


async def _on_card_draft_toggle_relation(context: CallbackContext) -> None:
    field, payload_key = _CARD_DRAFT_RELATIONS[context.action]
    async with context.sessions() as session:
        draft = await _require_card_draft(session, context.owner_id)
        state = dict(draft.state or {})
        selected = set(state.get(field) or [])
        selected.symmetric_difference_update({context.payload[payload_key]})
        state[field] = sorted(selected)
        draft.state = _sanitize_card_creation_state(state)
        await session.commit()
    await render_card_creation(context.message, context.services)


async def _on_card_draft_save(context: CallbackContext) -> None:
    async with context.sessions() as session:
        draft = await _require_card_draft(session, context.owner_id)
        state = _sanitize_card_creation_state(dict(draft.state or {}))
        errors = _card_creation_errors(state)
        if errors:
            raise DomainError("Card is incomplete: " + "; ".join(errors))
        card = await create_card(
            session,
            kind=state["kind"],
            title=state["title"],
            note=state["note"],
            stage=state["stage"],
            priority=state["priority"],
            hard_time=state["hard_time"],
            blocked=state["blocked"],
            blocked_description=state["blocked_description"],
            effort_points=state["effort_points"],
            repeatable=state["repeatable"],
            categories=set(state["categories"]),
            energy_types=set(state["energy_types"]),
            value_ids=set(state["value_ids"]),
            tag_ids=set(state["tag_ids"]),
        )
        await session.delete(draft)
        await session.commit()
    await send_registered(
        context.message,
        context.services,
        f"✅ Created <b>{html.escape(card.title)}</b>.",
        kind=MessageKind.DIALOGUE_ASSISTANT,
        related_id=card.id,
    )


async def _on_card_draft_discard(context: CallbackContext) -> None:
    async with context.sessions() as session:
        await _clear_ui_sessions(session, context.owner_id)
        await session.commit()
    await command_start(context.message, context.services)


# --- Navigation ----------------------------------------------------------------


async def _on_dashboard_page(context: CallbackContext) -> None:
    await render_dashboard(
        context.message,
        context.services,
        CardStage(context.payload["stage"]),
        title=context.payload["title"],
        page=int(context.payload["page"]),
    )


async def _on_card_view(context: CallbackContext) -> None:
    await render_card(
        context.message,
        context.services,
        context.payload["id"],
        back=context.payload.get("back"),
    )


async def _on_card_children(context: CallbackContext) -> None:
    await render_children(
        context.message,
        context.services,
        context.payload["id"],
        page=int(context.payload.get("page", 0)),
        back=context.payload.get("back"),
    )


async def _on_card_back(context: CallbackContext) -> None:
    back = context.payload.get("back") or {"kind": "home"}
    if back["kind"] == "dashboard":
        await render_dashboard(
            context.message,
            context.services,
            CardStage(back["stage"]),
            title=back["title"],
            page=int(back.get("page", 0)),
        )
    elif back["kind"] == "request":
        await render_saved_request(context.message, context.services, int(back["id"]))
    elif back["kind"] == "card":
        await render_card(
            context.message,
            context.services,
            int(back["id"]),
            back=back.get("back"),
        )
    elif back["kind"] == "children":
        await render_children(
            context.message,
            context.services,
            int(back["id"]),
            page=int(back.get("page", 0)),
            back=back.get("back"),
        )
    else:
        await command_start(context.message, context.services)


# --- Committed Card mutations --------------------------------------------------


async def _on_card_move(context: CallbackContext) -> None:
    async with context.sessions() as session:
        result = await move_card(
            session, context.payload["id"], CardStage(context.payload["stage"])
        )
        await session.commit()
    await render_card(
        context.message,
        context.services,
        context.payload["id"],
        notice="⚠️ " + "; ".join(result.warnings) if result.warnings else None,
    )


async def _on_card_edit_text(context: CallbackContext) -> None:
    card_id = context.payload["id"]
    field = context.payload["field"]
    async with context.sessions() as session:
        back_state = await _card_editor_back_state(session, context.owner_id)
        await _clear_ui_sessions(session, context.owner_id)
        card = await session.get(Card, card_id)
        if card is None:
            raise DomainError("Card does not exist")
        session.add(
            UiSession(
                owner_id=context.owner_id,
                kind="card_text",
                state={
                    "card_id": card_id,
                    "field": field,
                    "message_id": context.message.message_id,
                    "back": back_state,
                },
                expires_at=datetime.now(UTC) + timedelta(minutes=30),
            )
        )
        back = await token_button(
            session,
            context.owner_id,
            "↩️ Back",
            "card_view",
            {"id": card_id, "back": back_state},
        )
        current = str(getattr(card, field) or "")
        await session.commit()
    await send_registered(
        context.message,
        context.services,
        f"<b>Current {html.escape(field)}</b>: "
        f"{html.escape(current or '—')}\n\n"
        f"Set new {html.escape(field.title())}",
        kind=MessageKind.DASHBOARD,
        markup=InlineKeyboardMarkup(inline_keyboard=[[back]]),
        related_id=card_id,
    )


async def _on_card_choices(context: CallbackContext) -> None:
    await render_card_choices(
        context.message,
        context.services,
        context.action,
        context.payload["id"],
        page=int(context.payload.get("page", 0)),
    )


async def _on_card_set_field(context: CallbackContext) -> None:
    async with context.sessions() as session:
        await update_card_fields(
            session,
            context.payload["id"],
            {context.payload["field"]: context.payload["value"]},
        )
        await session.commit()
    await render_card(context.message, context.services, context.payload["id"])


async def _on_card_toggle_field(context: CallbackContext) -> None:
    field = context.payload["field"]
    async with context.sessions() as session:
        card = await session.get(Card, context.payload["id"])
        if card is None:
            raise DomainError("Card does not exist")
        if field == "blocked" and not card.blocked:
            # Blocking always needs a reason, so ask for it before writing anything.
            back_state = await _card_editor_back_state(session, context.owner_id)
            await _clear_ui_sessions(session, context.owner_id)
            session.add(
                UiSession(
                    owner_id=context.owner_id,
                    kind="card_blocked_text",
                    state={
                        "card_id": card.id,
                        "message_id": context.message.message_id,
                        "back": back_state,
                    },
                    expires_at=datetime.now(UTC) + timedelta(minutes=30),
                )
            )
            back_button = await token_button(
                session,
                context.owner_id,
                "↩️ Back",
                "card_view",
                {"id": card.id, "back": back_state},
            )
            await session.commit()
            await send_registered(
                context.message,
                context.services,
                "<b>Mark Card as blocked</b>\n\nDescribe what is blocking it.",
                kind=MessageKind.CARD_EDITOR,
                markup=InlineKeyboardMarkup(inline_keyboard=[[back_button]]),
            )
            return
        await update_card_fields(
            session,
            card.id,
            {field: not bool(getattr(card, field))},
        )
        await session.commit()
    await render_card(context.message, context.services, context.payload["id"])


async def _on_card_toggle_relation(context: CallbackContext) -> None:
    """Toggle one Category, Energy type, Value or Tag and reopen the same selector."""
    field = _CARD_RELATION_TOGGLES[context.action]
    relation = _RELATION_CHOICES[field]
    async with context.sessions() as session:
        await relation.toggle(
            session,
            context.payload["id"],
            relation.parse(context.payload[relation.payload_key]),
        )
        await session.commit()
    await render_card_choices(
        context.message, context.services, f"card_choose_{field}", context.payload["id"]
    )


async def _on_card_archive(context: CallbackContext) -> None:
    async with context.sessions() as session:
        count = len(await archive_subtree(session, context.payload["id"]))
        undo = await token_button(
            session,
            context.owner_id,
            "Undo archive",
            "card_restore",
            {"id": context.payload["id"]},
        )
        await session.commit()
    await send_registered(
        context.message,
        context.services,
        f"Archived {count} card(s).",
        kind=MessageKind.RECEIPT,
        markup=InlineKeyboardMarkup(inline_keyboard=[[undo], menu_row()]),
    )


async def _on_card_restore(context: CallbackContext) -> None:
    async with context.sessions() as session:
        count = len(await archive_subtree(session, context.payload["id"], archive=False))
        await session.commit()
    await send_registered(
        context.message,
        context.services,
        f"Restored {count} card(s).",
        kind=MessageKind.RECEIPT,
        markup=InlineKeyboardMarkup(inline_keyboard=[menu_row()]),
    )


async def _on_card_delete_prompt(context: CallbackContext) -> None:
    async with context.sessions() as session:
        confirm = await token_button(
            session,
            context.owner_id,
            "Permanently delete tree",
            "card_delete_confirm",
            {"id": context.payload["id"]},
        )
        await session.commit()
    await send_registered(
        context.message,
        context.services,
        "<b>Final confirmation</b>\nThis removes the tree and its historical contribution.",
        kind=MessageKind.APPROVAL,
        markup=InlineKeyboardMarkup(inline_keyboard=[[confirm], menu_row()]),
    )


async def _on_card_delete_confirm(context: CallbackContext) -> None:
    async with context.sessions() as session:
        count = await delete_subtree(session, context.payload["id"])
        await session.commit()
    await send_registered(
        context.message,
        context.services,
        f"Permanently deleted {count} card(s).",
        kind=MessageKind.RECEIPT,
        markup=InlineKeyboardMarkup(inline_keyboard=[menu_row()]),
    )


async def _on_card_finish(context: CallbackContext) -> None:
    async with context.sessions() as session:
        result = await finish_action(
            session, context.payload["id"], CardStage(context.payload["stage"])
        )
        await session.commit()
    notice = "⚠️ " + "; ".join(result.warnings) if result.warnings else None
    if context.payload["stage"] == CardStage.DONE.value:
        await render_feedback(context.message, context.services, notice=notice)
        return
    await send_registered(
        context.message,
        context.services,
        _with_notice("Card updated.", notice),
        kind=MessageKind.RECEIPT,
        markup=InlineKeyboardMarkup(inline_keyboard=[menu_row()]),
    )


async def _on_feedback(context: CallbackContext) -> None:
    async with context.sessions() as session:
        await set_feedback(session, context.payload["id"], bool(context.payload["liked"]))
        await session.commit()
    await render_feedback(context.message, context.services)


# --- Sprint --------------------------------------------------------------------


async def _on_sprint_start(context: CallbackContext) -> None:
    async with context.sessions() as session:
        profile = await session.get(UserProfile, 1)
        sprint = await start_sprint(
            session, capacity=profile.capacity_effort_points if profile else None
        )
        await session.commit()
    await send_registered(
        context.message,
        context.services,
        f"Sprint {sprint.number} started.",
        kind=MessageKind.RECEIPT,
        markup=InlineKeyboardMarkup(inline_keyboard=[menu_row()]),
    )


async def _on_sprint_finish(context: CallbackContext) -> None:
    async with context.sessions() as session:
        sprint = await finish_sprint(session, reason="finished_early")
        await session.commit()
    await send_registered(
        context.message,
        context.services,
        f"Sprint {sprint.number} finished early.",
        kind=MessageKind.RECEIPT,
        markup=InlineKeyboardMarkup(inline_keyboard=[menu_row()]),
    )


# --- AI proposals --------------------------------------------------------------


async def _on_proposal_approve(context: CallbackContext) -> None:
    proposal_id = context.payload["id"]
    async with context.sessions() as session:
        destructive = await session.scalar(
            select(ProposalChange.id).where(
                ProposalChange.proposal_id == proposal_id,
                ProposalChange.action == "delete",
            )
        )
        if destructive:
            confirm = await token_button(
                session,
                context.owner_id,
                "Permanently delete",
                "proposal_delete_confirm",
                {"id": proposal_id},
            )
            await session.commit()
            await send_registered(
                context.message,
                context.services,
                "<b>Final destructive confirmation</b>\nThis permanently removes the "
                "selected tree and its historical contribution.",
                kind=MessageKind.APPROVAL,
                markup=InlineKeyboardMarkup(inline_keyboard=[[confirm]]),
            )
            return
        affected = await ProposalService(session).apply(proposal_id)
        await session.commit()
    if await continue_agent_approval(
        context.message,
        context.services,
        "proposal",
        proposal_id,
        decision="approved",
        result={"affected_ids": affected},
    ):
        return
    await send_registered(
        context.message,
        context.services,
        f"✅ Saved proposal. Updated {len(affected)} item(s).",
        kind=MessageKind.DIALOGUE_ASSISTANT,
    )


async def _on_proposal_delete_confirm(context: CallbackContext) -> None:
    proposal_id = context.payload["id"]
    async with context.sessions() as session:
        affected = await ProposalService(session).apply(proposal_id, allow_destructive=True)
        await session.commit()
    if await continue_agent_approval(
        context.message,
        context.services,
        "proposal",
        proposal_id,
        decision="approved",
        result={"affected_ids": affected, "destructive": True},
    ):
        return
    await send_registered(
        context.message,
        context.services,
        f"Permanently deleted {len(affected)} selected item(s).",
        kind=MessageKind.RECEIPT,
    )


async def _on_proposal_reject(context: CallbackContext) -> None:
    proposal_id = context.payload["id"]
    async with context.sessions() as session:
        await ProposalService(session).reject(proposal_id)
        await session.commit()
    if await continue_agent_approval(
        context.message,
        context.services,
        "proposal",
        proposal_id,
        decision="discarded",
        result={"message": "The user discarded this proposed change."},
    ):
        return
    await send_registered(
        context.message,
        context.services,
        "🗑 Proposal discarded.",
        kind=MessageKind.DIALOGUE_ASSISTANT,
    )


CALLBACK_ACTIONS: dict[str, CallbackHandler] = {
    "value_create_prompt": _on_item_create_prompt,
    "tag_create_prompt": _on_item_create_prompt,
    "item_view": _on_item_view,
    "item_edit_text": _on_item_edit_text,
    "item_text_back": _on_item_text_back,
    "item_create": _on_item_create,
    "item_toggle_focus": _on_item_toggle_focus,
    "item_archive_prompt": _on_item_archive_prompt,
    "item_archive_confirm": _on_item_archive_confirm,
    "item_back": _on_item_back,
    "request_view": _on_request_view,
    "subsession_confirm": _on_subsession_confirm,
    "subsession_cancel": _on_subsession_cancel,
    "card_create_view": _on_card_draft_view,
    "card_create_edit_text": _on_card_draft_edit_text,
    "card_create_toggle": _on_card_draft_toggle,
    "card_create_set": _on_card_draft_set,
    "card_create_save": _on_card_draft_save,
    "card_create_discard": _on_card_draft_discard,
    "dashboard_page": _on_dashboard_page,
    "card_view": _on_card_view,
    "card_children": _on_card_children,
    "card_back": _on_card_back,
    "card_move": _on_card_move,
    "card_edit_text": _on_card_edit_text,
    "card_set_field": _on_card_set_field,
    "card_toggle_field": _on_card_toggle_field,
    "card_archive": _on_card_archive,
    "card_restore": _on_card_restore,
    "card_delete_prompt": _on_card_delete_prompt,
    "card_delete_confirm": _on_card_delete_confirm,
    "card_finish": _on_card_finish,
    "feedback": _on_feedback,
    "sprint_start": _on_sprint_start,
    "sprint_finish": _on_sprint_finish,
    "proposal_approve": _on_proposal_approve,
    "proposal_delete_confirm": _on_proposal_delete_confirm,
    "proposal_reject": _on_proposal_reject,
    **{f"card_choose_{field}": _on_card_choices for field in _CARD_CHOICE_FIELDS},
    **{
        f"card_create_choose_{field}": _on_card_draft_chooser
        for field in _CARD_DRAFT_CHOICE_FIELDS
    },
    **dict.fromkeys(_CARD_DRAFT_RELATIONS, _on_card_draft_toggle_relation),
    **dict.fromkeys(_CARD_RELATION_TOGGLES, _on_card_toggle_relation),
}


async def _report_callback_failure(
    context: CallbackContext, *, notice: str, fallback: str
) -> None:
    """Keep a still-pending proposal reviewable, otherwise state what failed.

    A failed approval leaves the proposal pending, so the owner needs its screen back
    rather than a bare error above a dead message.
    """
    if context.action.startswith("proposal_") and context.payload.get("id"):
        try:
            await render_proposal(
                context.message,
                context.services,
                context.payload["id"],
                notice=notice,
            )
            return
        except DomainError:
            pass
        except Exception:
            logger.exception("Could not restore proposal UI after callback failure")
    await send_registered(context.message, context.services, fallback, kind=MessageKind.ERROR)


async def _resume_failed_approval(context: CallbackContext, error: Exception) -> bool:
    """Let a suspended agent turn observe an approval that could not be applied."""
    if context.action not in {"proposal_approve", "proposal_delete_confirm"}:
        return False
    return await continue_agent_approval(
        context.message,
        context.services,
        "proposal",
        context.payload["id"],
        decision="failed",
        result={"error": str(error)},
    )


@router.callback_query(F.data.startswith("cb:"))
async def callback_token_handler(callback: CallbackQuery, services: Services) -> None:
    if not callback.message:
        return
    token_value = callback.data.split(":", 1)[1]
    async with services.sessions() as session:
        now = datetime.now(UTC)
        claimed = (
            await session.execute(
                update(CallbackToken)
                .where(
                    CallbackToken.token == token_value,
                    CallbackToken.owner_id == services.owner_id,
                    CallbackToken.expires_at >= now,
                    CallbackToken.consumed_at.is_(None),
                )
                .values(consumed_at=now)
                .returning(CallbackToken.action, CallbackToken.payload)
            )
        ).one_or_none()
        if claimed is None:
            await callback.answer("This action expired. Reopen the screen.", show_alert=True)
            return
        action, payload = claimed
        await session.commit()
    await callback.answer()

    context = CallbackContext(callback, services, action, dict(payload or {}))
    handler = CALLBACK_ACTIONS.get(action)
    if handler is None:
        logger.warning("Unknown Telegram callback action: %s", action)
        await send_registered(
            context.message,
            services,
            "This action is no longer available. Reopen the screen.",
            kind=MessageKind.ERROR,
        )
        return

    try:
        await handler(context)
    except StaleStateError as error:
        if action.startswith("proposal_") and payload.get("id"):
            async with services.sessions() as session:
                proposal = await session.get(ChangeProposal, payload["id"])
                if proposal:
                    proposal.status = ProposalStatus.STALE.value
                    await session.commit()
        if await _resume_failed_approval(context, error):
            return
        await send_registered(
            context.message, services, html.escape(str(error)), kind=MessageKind.ERROR
        )
    except DomainError as error:
        if await _resume_failed_approval(context, error):
            return
        await _report_callback_failure(
            context,
            notice=f"⚠️ {error}",
            fallback=html.escape(str(error)),
        )
    except Exception:
        logger.exception("Telegram callback failed: action=%s payload=%s", action, payload)
        await _report_callback_failure(
            context,
            notice=(
                "⚠️ This action failed. The proposal is still pending; "
                "you can retry or discard it."
            ),
            fallback="Safwa could not finish this action. Reopen the screen and try again.",
        )


async def _choice_options(session: AsyncSession, field: str) -> list[tuple[str, Any]]:
    """The selectable options for one Card field, shared by draft and committed screens."""
    if field == "kind":
        return [(_kind_label(kind), kind.value) for kind in CardKind]
    if field == "stage":
        return [
            (stage.value.title(), stage.value)
            for stage in (CardStage.BACKLOG, CardStage.SPRINT, CardStage.TODAY)
        ]
    if field == "priority":
        return [(priority.value.title(), priority.value) for priority in Priority]
    if field == "effort":
        return [(f"{points} EP", points) for points in sorted(EFFORT_POINTS)]
    if field == "categories":
        return [(_typed_label(item, _CATEGORY_EMOJIS), item.value) for item in Category]
    if field == "energy":
        return [(_typed_label(item, _ENERGY_EMOJIS), item.value) for item in EnergyType]
    if field not in {"values", "tags"}:
        raise DomainError("Unknown Card selector")
    model = Value if field == "values" else Tag
    items = await session.scalars(
        select(model).where(model.archived_at.is_(None)).order_by(model.name)
    )
    return [(item.name, item.id) for item in items]


def _choice_rows(
    options: list[tuple[str, Any]],
    selected: set[Any],
    build: Callable[[Any], tuple[str, dict[str, Any]]],
) -> list[tuple[str, str, dict[str, Any]]]:
    rows: list[tuple[str, str, dict[str, Any]]] = []
    for label, value in options:
        action, payload = build(value)
        rows.append((f"{'✓ ' if value in selected else ''}{label}", action, payload))
    return rows


def _selector_page(field: str, options: list[tuple[str, Any]], page: int) -> _Page | None:
    """Page the Value and Tag lists; the fixed enumerations always fit one screen."""
    if field not in _NAMED_CHOICE_FIELDS:
        return None
    return _paginate(options, page, _SELECTOR_PAGE_SIZE)


async def handle_card_creation_chooser(
    message: Message, services: Services, action: str, *, page: int = 0
) -> None:
    """Render one selector for the transient Card draft; nothing is committed here."""
    field = action.removeprefix("card_create_choose_")
    relation = _RELATION_CHOICES.get(field)
    async with services.sessions() as session:
        draft = await _require_card_draft(session, services.owner_id)
        state = _sanitize_card_creation_state(dict(draft.state or {}))
        options = await _choice_options(session, field)
        current = _selector_page(field, options, page)
        if relation is not None:
            selected = set(state[relation.draft_field])

            def build(value: Any, relation: _RelationChoice = relation) -> tuple[str, dict]:
                return (
                    f"card_create_toggle_{relation.singular}",
                    {relation.payload_key: value},
                )
        else:
            state_field = _SINGLE_CHOICE_FIELDS[field]
            selected = {state[state_field]}

            def build(value: Any, state_field: str = state_field) -> tuple[str, dict]:
                return "card_create_set", {"field": state_field, "value": value}

        choices = _choice_rows(current.items if current else options, selected, build)
        await session.commit()
    await choice_screen(
        message,
        services,
        _CHOICE_TITLES[field],
        choices,
        back=("↩️ Back", "card_create_view", {}),
        paging=(current, action, {}) if current else None,
    )


async def render_card_choices(
    message: Message, services: Services, action: str, card_id: int, *, page: int = 0
) -> None:
    """Render field and relationship selectors for an already committed Card.

    Every selection routes its mutation through the domain layer, and the screens
    themselves stay out of the persona dialogue.
    """
    field = action.removeprefix("card_choose_")
    if field not in _CARD_CHOICE_FIELDS:
        raise DomainError("Unknown Card relationship selector")
    relation = _RELATION_CHOICES.get(field)
    async with services.sessions() as session:
        card = await session.get(Card, card_id)
        if card is None or card.archived_at is not None:
            raise DomainError("Card does not exist or is archived")
        options = await _choice_options(session, field)
        current = _selector_page(field, options, page)
        if relation is not None:
            selected = set(
                await session.scalars(
                    select(relation.link_column).where(relation.link_owner == card.id)
                )
            )

            def build(value: Any, relation: _RelationChoice = relation) -> tuple[str, dict]:
                return (
                    f"card_toggle_{relation.singular}",
                    {"id": card.id, relation.payload_key: value},
                )
        elif field == "stage":
            # A stage change is a domain move, not a plain field write.
            selected = {card.effective_stage}

            def build(value: Any) -> tuple[str, dict]:
                return "card_move", {"id": card.id, "stage": value}

        else:
            column = _SINGLE_CHOICE_FIELDS[field]
            selected = {getattr(card, column)}

            def build(value: Any, column: str = column) -> tuple[str, dict]:
                return "card_set_field", {"id": card.id, "field": column, "value": value}

        choices = _choice_rows(current.items if current else options, selected, build)
        await session.commit()
    await choice_screen(
        message,
        services,
        _CHOICE_TITLES[field],
        choices,
        back=("↩️ Back", "card_view", {"id": card_id}),
        paging=(current, action, {"id": card_id}) if current else None,
    )


def _card_overview_text(state: dict[str, Any], *, heading: str = "Card") -> str:
    kind = str(state.get("kind") or "")
    lines = [
        f"Kind: {html.escape(_kind_label(kind))}",
        f"Title: <b>{html.escape(str(state.get('title') or '—'))}</b>",
    ]
    if state.get("parent_name"):
        lines.append(f"Parent: {html.escape(str(state['parent_name']))}")
    lines.extend(
        [
            f"Stage: {html.escape(str(state.get('stage') or 'backlog').title())}",
            f"Note: {html.escape(str(state.get('note') or '—'))}",
            f"Priority: {html.escape(str(state.get('priority') or 'medium').title())} · "
            f"Hard Time: {'Yes' if state.get('hard_time') else 'No'}",
            f"Blocked: {'Yes' if state.get('blocked') else 'No'}",
        ]
    )
    if state.get("blocked"):
        lines.append(
            "Blocked description: "
            + html.escape(str(state.get("blocked_description") or "Required"))
        )
    if kind == CardKind.ACTION.value:
        lines.extend(
            [
                f"Effort: {state.get('effort_points') or '—'}",
                f"Repeatable: {'Yes' if state.get('repeatable') else 'No'}",
                f"Categories: {html.escape(_category_expression(state.get('categories', [])))}",
                f"Energy: {html.escape(_energy_expression(state.get('energy_types', [])))}",
            ]
        )
    else:
        lines.extend(
            [
                f"Effort: {state.get('completed_effort', 0)}/{state.get('total_effort', 0)} EP",
                "Children: "
                f"{state.get('completed_children', 0)}/{state.get('total_children', 0)} completed",
            ]
        )
    lines.extend(
        [
            f"Values: {html.escape(', '.join(state.get('value_names', [])) or '—')}",
            f"Tags: {html.escape(', '.join(state.get('tag_names', [])) or '—')}",
        ]
    )
    return f"<b>{html.escape(heading)}</b>\n" + "\n".join(lines)


async def render_children(
    message: Message,
    services: Services,
    parent_id: int,
    *,
    page: int = 0,
    back: dict[str, Any] | None = None,
) -> None:
    back = back or {"kind": "home"}
    async with services.sessions() as session:
        parent = await session.get(Card, parent_id)
        if parent is None or parent.archived_at is not None:
            raise DomainError("Parent Card does not exist or is archived")
        children = list(
            await session.scalars(
                select(Card).where(
                    Card.parent_id == parent.id,
                    Card.archived_at.is_(None),
                )
            )
        )
        current = _paginate_cards(children, page)
        child_back = {
            "kind": "children",
            "id": parent.id,
            "page": current.index,
            "back": back,
        }
        rows: list[list[InlineKeyboardButton]] = []
        for child in current.items:
            label = f"{_kind_label(child.kind)} · {child.title} · {child.effective_stage.title()}"
            rows.append(
                [
                    await token_button(
                        session,
                        services.owner_id,
                        label[:60],
                        "card_view",
                        {"id": child.id, "back": child_back},
                    )
                ]
            )
        rows.extend(
            await _paging_row(
                session,
                services.owner_id,
                current,
                "card_children",
                {"id": parent.id, "back": back},
            )
        )
        rows.append(
            [
                await token_button(
                    session,
                    services.owner_id,
                    "↩️ Back",
                    "card_view",
                    {"id": parent.id, "back": back},
                )
            ]
        )
        await session.commit()
    await send_registered(
        message,
        services,
        f"<b>Children of {html.escape(parent.title)}</b> · "
        f"{len(children)} total · {current.label}",
        kind=MessageKind.DASHBOARD,
        markup=InlineKeyboardMarkup(inline_keyboard=rows),
        related_id=parent.id,
    )


async def render_card(
    message: Message,
    services: Services,
    card_id: int,
    *,
    replace_message_id: int | None = None,
    back: dict[str, Any] | None = None,
    notice: str | None = None,
) -> None:
    async with services.sessions() as session:
        existing_editor = await session.scalar(
            select(UiSession).where(UiSession.owner_id == services.owner_id)
        )
        if (
            back is None
            and existing_editor is not None
            and existing_editor.kind == "card_editor"
            and existing_editor.state.get("card_id") == card_id
        ):
            back = dict(existing_editor.state.get("back", {}))
        back = back or {"kind": "home"}
        card = await session.get(Card, card_id)
        if card is None:
            raise DomainError("Card does not exist")
        parent = await session.get(Card, card.parent_id) if card.parent_id else None
        direct_value_ids = list(
            await session.scalars(select(CardValue.value_id).where(CardValue.card_id == card.id))
        )
        direct_values = (
            list(await session.scalars(select(Value).where(Value.id.in_(direct_value_ids))))
            if direct_value_ids
            else []
        )
        direct_tag_ids = list(
            await session.scalars(select(CardTag.tag_id).where(CardTag.card_id == card.id))
        )
        direct_tags = (
            list(await session.scalars(select(Tag).where(Tag.id.in_(direct_tag_ids))))
            if direct_tag_ids
            else []
        )
        categories = list(
            await session.scalars(
                select(CardCategory.category).where(CardCategory.card_id == card.id)
            )
        )
        energy_types = list(
            await session.scalars(
                select(CardEnergyType.energy_type).where(CardEnergyType.card_id == card.id)
            )
        )
        field_specs = [
            ("✏️ Title", "card_edit_text", {"id": card.id, "field": "title"}),
            ("📝 Note", "card_edit_text", {"id": card.id, "field": "note"}),
            ("📍 Stage", "card_choose_stage", {"id": card.id}),
            ("⚠️ Priority", "card_choose_priority", {"id": card.id}),
            ("⏱ Hard Time", "card_toggle_field", {"id": card.id, "field": "hard_time"}),
            ("🚧 Blocked", "card_toggle_field", {"id": card.id, "field": "blocked"}),
        ]
        if card.blocked:
            field_specs.append(
                (
                    "📝 Blocked reason",
                    "card_edit_text",
                    {"id": card.id, "field": "blocked_description"},
                )
            )
        if card.kind == CardKind.ACTION.value:
            field_specs.extend(
                [
                    ("🔢 Effort", "card_choose_effort", {"id": card.id}),
                    (
                        "🔁 Repeat",
                        "card_toggle_field",
                        {"id": card.id, "field": "repeatable"},
                    ),
                    ("🏷 Categories", "card_choose_categories", {"id": card.id}),
                    ("⚡ Energy", "card_choose_energy", {"id": card.id}),
                ]
            )
        field_specs.extend(
            [
                ("💎 Values", "card_choose_values", {"id": card.id}),
                ("🏷 Tags", "card_choose_tags", {"id": card.id}),
            ]
        )
        buttons = [await token_button(session, services.owner_id, *spec) for spec in field_specs]
        rows = [buttons[index : index + 2] for index in range(0, len(buttons), 2)]
        relationship_rows: list[list[InlineKeyboardButton]] = []
        if parent is not None:
            relationship_rows.append(
                [
                    await token_button(
                        session,
                        services.owner_id,
                        f"🌳 Parent: {parent.title}"[:60],
                        "card_view",
                        {
                            "id": parent.id,
                            "back": {"kind": "card", "id": card.id, "back": back},
                        },
                    )
                ]
            )
        if card.kind in {CardKind.GOAL.value, CardKind.IDEA.value}:
            relationship_rows.append(
                [
                    await token_button(
                        session,
                        services.owner_id,
                        "👥 Children",
                        "card_children",
                        {"id": card.id, "page": 0, "back": back},
                    )
                ]
            )
        rows = relationship_rows + rows
        if card.kind == CardKind.ACTION.value and card.effective_stage not in {"done", "cancelled"}:
            rows.append(
                [
                    await token_button(
                        session,
                        services.owner_id,
                        "✅ Done",
                        "card_finish",
                        {"id": card.id, "stage": "done"},
                    ),
                    await token_button(
                        session,
                        services.owner_id,
                        "✖ Cancel",
                        "card_finish",
                        {"id": card.id, "stage": "cancelled"},
                    ),
                ]
            )
        rows.append(
            [
                await token_button(
                    session, services.owner_id, "Archive", "card_archive", {"id": card.id}
                ),
                await token_button(
                    session,
                    services.owner_id,
                    "Delete",
                    "card_delete_prompt",
                    {"id": card.id},
                ),
            ]
        )
        rows.append(
            [
                await token_button(
                    session,
                    services.owner_id,
                    "↩️ Back",
                    "card_back",
                    {"back": back},
                )
            ]
        )
        await session.execute(delete(UiSession).where(UiSession.owner_id == services.owner_id))
        session.add(
            UiSession(
                owner_id=services.owner_id,
                kind="card_editor",
                state={
                    "card_id": card.id,
                    "back": back,
                    "message_id": replace_message_id or message.message_id,
                },
                expires_at=datetime.now(UTC) + timedelta(minutes=30),
            )
        )
        progress = (
            await card_progress(session, card.id)
            if card.kind in {CardKind.GOAL.value, CardKind.IDEA.value}
            else {}
        )
        await session.commit()
    text = _with_notice(
        _card_overview_text(
            {
                "kind": card.kind,
                "title": card.title,
                "parent_name": parent.title if parent else None,
                "stage": card.effective_stage,
                "note": card.note,
                "priority": card.priority,
                "hard_time": card.hard_time,
                "blocked": card.blocked,
                "blocked_description": card.blocked_description,
                "effort_points": card.effort_points,
                "repeatable": card.repeatable,
                "categories": categories,
                "energy_types": energy_types,
                "value_names": [value.name for value in direct_values],
                "tag_names": [tag.name for tag in direct_tags],
                **progress,
            }
        ),
        notice,
    )
    markup = InlineKeyboardMarkup(inline_keyboard=rows)
    if replace_message_id is not None:
        await edit_registered_message(
            message,
            services,
            replace_message_id,
            text,
            kind=MessageKind.DASHBOARD,
            markup=markup,
            related_id=card.id,
        )
    else:
        await send_registered(
            message,
            services,
            text,
            kind=MessageKind.DASHBOARD,
            markup=markup,
            related_id=card.id,
        )


async def _proposal_item_state(
    session: AsyncSession, change: ProposalChange
) -> tuple[dict[str, Any], dict[str, Any]]:
    current: dict[str, Any] = {}
    archived = False
    if change.entity == "card" and change.entity_id:
        card = await session.get(Card, change.entity_id)
        if card is not None:
            archived = card.archived_at is not None
            current = {
                "id": card.id,
                "kind": card.kind,
                "title": card.title,
                "note": card.note,
                "stage": card.effective_stage,
                "priority": card.priority,
                "hard_time": card.hard_time,
                "blocked": card.blocked,
                "blocked_description": card.blocked_description,
                "effort_points": card.effort_points,
                "repeatable": card.repeatable,
                "parent_id": card.parent_id,
                "categories": sorted(
                    await session.scalars(
                        select(CardCategory.category).where(CardCategory.card_id == card.id)
                    )
                ),
                "energy_types": sorted(
                    await session.scalars(
                        select(CardEnergyType.energy_type).where(CardEnergyType.card_id == card.id)
                    )
                ),
                "value_ids": sorted(
                    await session.scalars(
                        select(CardValue.value_id).where(CardValue.card_id == card.id)
                    )
                ),
                "tag_ids": sorted(
                    await session.scalars(select(CardTag.tag_id).where(CardTag.card_id == card.id))
                ),
            }
    elif change.entity == "tag" and change.entity_id:
        tag = await session.get(Tag, change.entity_id)
        if tag is not None:
            archived = tag.archived_at is not None
            current = {"name": tag.name, "description": tag.description}
    elif change.entity == "value" and change.entity_id:
        value = await session.get(Value, change.entity_id)
        if value is not None:
            archived = value.archived_at is not None
            current = {
                "name": value.name,
                "description": value.description,
                "active": value.active,
            }
    proposed = {**current, **dict(change.values)}
    if change.entity in {"tag", "value"} and change.action in {"archive", "delete"}:
        current["status"] = "Archived" if archived else "Active"
        proposed["status"] = "Archived" if change.action == "archive" else "Deleted"
    if change.entity == "card":
        if change.action == "complete":
            proposed["stage"] = CardStage.DONE.value
        elif change.action == "cancel":
            proposed["stage"] = CardStage.CANCELLED.value
        elif change.action == "reopen":
            proposed["stage"] = change.values.get("stage", CardStage.BACKLOG.value)
        elif change.action in {"archive", "delete"}:
            current["status"] = "Archived" if archived else "Active"
            proposed["status"] = "Archived" if change.action == "archive" else "Deleted"

        unresolved_references: list[tuple[str, str]] = []
        for spec in CARD_REFERENCE_SPECS:
            if not spec.mentioned_in(change.values):
                continue
            resolved = await resolve_references(session, spec, change.values)
            target_ids = resolved.ids | set(resolved.unknown_ids)
            unresolved_references.extend(
                (spec.query_key, name) for name in resolved.unresolved
            )
            existing = set(current.get(spec.plural_key, []))
            if change.action == "link":
                proposed[spec.plural_key] = sorted(existing | target_ids)
            elif change.action == "unlink":
                proposed[spec.plural_key] = sorted(existing - target_ids)
            else:
                proposed[spec.plural_key] = sorted(target_ids)
        if unresolved_references:
            proposed["_unresolved_references"] = unresolved_references
    return current, proposed


def _display_diff_value(value: Any) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return str(value)


async def _proposal_card_display_state(
    session: AsyncSession, state: dict[str, Any]
) -> dict[str, Any]:
    display = dict(state)
    parent = await session.get(Card, state.get("parent_id")) if state.get("parent_id") else None
    display["parent_name"] = parent.title if parent else None
    for ids_key, names_key, model, name_field in (
        ("value_ids", "value_names", Value, "name"),
        ("tag_ids", "tag_names", Tag, "name"),
    ):
        ids = list(state.get(ids_key) or [])
        entities = (
            list(await session.scalars(select(model).where(model.id.in_(ids)))) if ids else []
        )
        by_id = {entity.id: getattr(entity, name_field) for entity in entities}
        display[names_key] = [by_id[item_id] for item_id in ids if item_id in by_id]
    if display.get("id") and display.get("kind") in {
        CardKind.GOAL.value,
        CardKind.IDEA.value,
    }:
        display.update(await card_progress(session, int(display["id"])))
    return display


async def _proposal_diff_value(session: AsyncSession, field: str, value: Any) -> str:
    if field == "parent_id":
        if value is None:
            return "Root"
        parent = await session.get(Card, value)
        return parent.title if parent else f"Card #{value}"
    relation_specs = {
        "value_ids": (Value, "name"),
        "tag_ids": (Tag, "name"),
    }
    if field in relation_specs:
        model, name_field = relation_specs[field]
        ids = list(value or [])
        entities = (
            list(await session.scalars(select(model).where(model.id.in_(ids)))) if ids else []
        )
        by_id = {entity.id: getattr(entity, name_field) for entity in entities}
        return ", ".join(by_id[item_id] for item_id in ids if item_id in by_id) or "—"
    if field == "categories":
        return _category_expression(value)
    if field == "energy_types":
        return _energy_expression(value)
    if field in {"stage", "priority"} and value:
        return str(value).title()
    return _display_diff_value(value)


async def _proposal_card_diffs(
    session: AsyncSession, current: dict[str, Any], proposed: dict[str, Any]
) -> list[str]:
    labels = {
        "title": "Title",
        "note": "Note",
        "parent_id": "Parent",
        "stage": "Stage",
        "priority": "Priority",
        "hard_time": "Hard Time",
        "blocked": "Blocked",
        "blocked_description": "Blocked Description",
        "effort_points": "Effort",
        "repeatable": "Repeatable",
        "categories": "Categories",
        "energy_types": "Energy",
        "value_ids": "Values",
        "tag_ids": "Tags",
        "status": "Status",
    }
    diffs: list[str] = []
    for field, label in labels.items():
        if current.get(field) == proposed.get(field):
            continue
        old = await _proposal_diff_value(session, field, current.get(field))
        new = await _proposal_diff_value(session, field, proposed.get(field))
        diffs.append(f"• {label}: {html.escape(old)} → {html.escape(new)}")
    for query_key, name in proposed.get("_unresolved_references", []):
        label = query_key.replace("_", " ").title()
        diffs.append(f"• {label}: — → {html.escape(name)} (not found)")
    return diffs


async def render_proposal(
    message: Message,
    services: Services,
    proposal_id: int,
    *,
    replace_message_id: int | None = None,
    notice: str | None = None,
) -> None:
    async with services.sessions() as session:
        proposal = await session.get(ChangeProposal, proposal_id)
        if proposal is None or proposal.status != "pending":
            raise DomainError("Proposal is no longer pending")
        changes = list(
            await session.scalars(
                select(ProposalChange)
                .where(ProposalChange.proposal_id == proposal.id)
                .order_by(ProposalChange.position)
            )
        )
        save = await token_button(
            session, services.owner_id, "✅ Save", "proposal_approve", {"id": proposal.id}
        )
        discard = await token_button(
            session, services.owner_id, "🗑 Discard", "proposal_reject", {"id": proposal.id}
        )
        rows: list[list[InlineKeyboardButton]] = []
        text_parts = ["<b>Review proposal</b>"]
        if notice:
            text_parts.append(html.escape(notice))
        text_parts.append(html.escape(proposal.message))
        if len(changes) == 1 and changes[0].entity in {"card", "tag", "value"}:
            change = changes[0]
            current, proposed = await _proposal_item_state(session, change)
            item_name = change.entity.title()
            mode_name = "Create" if change.action == "create" else "Edit"
            text_parts[0] = f"<b>{mode_name} {item_name} · AI proposal</b>"
            if change.entity in {"tag", "value"}:
                text_parts.append(
                    f"Name: {html.escape(_display_diff_value(proposed.get('name')))}\n"
                    f"Description: "
                    f"{html.escape(_display_diff_value(proposed.get('description')))}"
                )
            elif change.entity == "card":
                display = await _proposal_card_display_state(session, proposed)
                text_parts.append(_card_overview_text(display, heading="Card overview"))
            if change.entity == "card" and change.action != "create":
                diffs = await _proposal_card_diffs(session, current, proposed)
            elif change.entity == "card":
                diffs = []
            else:
                diffs = [
                    f"• {field.replace('_', ' ').title()}: "
                    f"{html.escape(_display_diff_value(current.get(field)))} → "
                    f"{html.escape(_display_diff_value(new_value))}"
                    for field, new_value in proposed.items()
                    if current.get(field) != new_value
                ]
            if diffs:
                text_parts.append("<b>Proposed changes</b>\n" + "\n".join(diffs))
        else:
            text_parts.append(
                "\n".join(f"• {html.escape(proposal_change_summary(change))}" for change in changes)
            )
        rows.append([save, discard])
        await session.commit()
    text = "\n\n".join(part for part in text_parts if part)
    markup = InlineKeyboardMarkup(inline_keyboard=rows)
    if replace_message_id is not None:
        await edit_registered_message(
            message,
            services,
            replace_message_id,
            text,
            kind=MessageKind.APPROVAL,
            markup=markup,
            related_id=proposal_id,
        )
    else:
        await send_registered(
            message,
            services,
            text,
            kind=MessageKind.APPROVAL,
            markup=markup,
            related_id=proposal_id,
        )


async def render_ai_outcome(
    message: Message,
    services: Services,
    outcome: AIOutcome,
) -> None:
    """Render one agent state; suspended approval batches expose only their head item."""
    if outcome.proposal_id is not None:
        await render_proposal(message, services, outcome.proposal_id)
        return
    await send_registered(
        message,
        services,
        html.escape(outcome.message),
        kind=MessageKind.DIALOGUE_ASSISTANT,
    )


async def continue_agent_approval(
    message: Message,
    services: Services,
    target_type: str,
    target_id: int,
    *,
    decision: str,
    result: dict[str, Any],
) -> bool:
    """Advance a persisted approval queue, resuming the model only after its last item."""
    if getattr(services, "advisor", None) is None or getattr(services, "history", None) is None:
        return False
    if not await services.advisor.has_pending_approval(target_type, target_id):
        return False
    resolved_text = {
        "approved": "✅ Saved.",
        "discarded": "🗑 Discarded.",
        "failed": "⚠️ Failed.",
    }.get(decision, "Resolved.")
    await send_registered(
        message,
        services,
        f"{resolved_text} Safwa is continuing…",
        kind=MessageKind.RECEIPT,
    )
    try:
        await services.guard.acquire(message.message_id)
    except Exception:
        logger.exception(
            "Could not acquire continuation lease after %s %s #%s",
            decision,
            target_type,
            target_id,
        )
        await send_registered(
            message,
            services,
            f"{resolved_text}\n"
            "⚠️ The change is resolved, but the advisor follow-up was deferred. "
            "You can continue with a new message.",
            kind=MessageKind.DIALOGUE_ASSISTANT,
        )
        return True
    try:
        try:
            await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
            dialogue = await services.history.dialogue(message.chat.id)
            outcome = await services.advisor.resolve_approval(
                target_type,
                target_id,
                decision=decision,
                result=result,
                dialogue=dialogue,
            )
        except Exception:
            logger.exception(
                "AI continuation failed after %s %s #%s",
                decision,
                target_type,
                target_id,
            )
            await send_registered(
                message,
                services,
                f"{resolved_text}\n"
                "⚠️ The change is resolved, but Safwa could not generate its follow-up. "
                "You can continue with a new message.",
                kind=MessageKind.DIALOGUE_ASSISTANT,
            )
            return True
        if outcome is None:
            return False
        await render_ai_outcome(message, services, outcome)
        return True
    finally:
        services.guard.release(message.message_id)


@router.message(F.text)
async def ordinary_text(message: Message, services: Services) -> None:
    if not message.text or message.text.startswith("/"):
        return
    async with services.sessions() as session:
        ui = await session.scalar(
            select(UiSession)
            .where(
                UiSession.owner_id == services.owner_id, UiSession.expires_at > datetime.now(UTC)
            )
            .order_by(UiSession.created_at.desc())
        )
        ui_kind = ui.kind if ui is not None else None
        ui_state = dict(ui.state) if ui is not None else {}

    if ui_kind == "item_text":
        entity = str(ui_state["entity"])
        mode = str(ui_state["mode"])
        field = str(ui_state["field"])
        item_id = ui_state.get("item_id")
        message_id = int(ui_state["message_id"])
        values = dict(ui_state.get("values", {}))
        values[field] = message.text.strip()
        async with services.sessions() as session:
            if mode == "view":
                if entity == "value":
                    await update_value_fields(session, int(item_id), **{field: values[field]})
                else:
                    await update_tag_fields(session, int(item_id), **{field: values[field]})
            await session.execute(delete(UiSession).where(UiSession.owner_id == services.owner_id))
            await session.commit()
        input_deleted = await delete_text_input(message, services)
        if not input_deleted:
            await clear_message_markup(message, message_id)
        await render_item_editor(
            message,
            services,
            entity,
            mode=mode,
            item_id=int(item_id) if item_id is not None else None,
            values=values,
            replace_message_id=message_id if input_deleted else None,
        )
        return
    if ui_kind == "card_create_text":
        async with services.sessions() as session:
            editor = await session.scalar(
                select(UiSession).where(UiSession.owner_id == services.owner_id)
            )
            if editor is None:
                raise DomainError("Card creation is no longer active")
            state = dict(editor.state or {})
            field = str(state.pop("input_field"))
            message_id = int(state.pop("message_id"))
            state[field] = message.text.strip()
            editor.kind = "card_create"
            editor.state = _sanitize_card_creation_state(state)
            await session.commit()
        input_deleted = await delete_text_input(message, services)
        if not input_deleted:
            await clear_message_markup(message, message_id)
        await render_card_creation(
            message,
            services,
            replace_message_id=message_id if input_deleted else None,
        )
        return
    if ui_kind == "card_text":
        async with services.sessions() as session:
            value = message.text.strip()
            card = await edit_card_text(session, ui_state["card_id"], ui_state["field"], value)
            await session.execute(delete(UiSession).where(UiSession.owner_id == services.owner_id))
            await session.commit()
        input_deleted = await delete_text_input(message, services)
        if not input_deleted:
            await clear_message_markup(message, int(ui_state["message_id"]))
        await render_card(
            message,
            services,
            card.id,
            replace_message_id=int(ui_state["message_id"]) if input_deleted else None,
            back=dict(ui_state.get("back", {})),
        )
        return
    if ui_kind == "card_blocked_text":
        async with services.sessions() as session:
            card = await update_card_fields(
                session,
                ui_state["card_id"],
                {
                    "blocked": True,
                    "blocked_description": message.text.strip(),
                },
            )
            await session.execute(delete(UiSession).where(UiSession.owner_id == services.owner_id))
            await session.commit()
        input_deleted = await delete_text_input(message, services)
        if not input_deleted:
            await clear_message_markup(message, int(ui_state["message_id"]))
        await render_card(
            message,
            services,
            card.id,
            replace_message_id=int(ui_state["message_id"]) if input_deleted else None,
            back=dict(ui_state.get("back", {})),
        )
        return
    await dismiss_prior_ui(message, services)
    async with services.sessions() as session:
        await register_message(
            session, message.chat.id, message.message_id, "in", MessageKind.DIALOGUE_USER
        )
        workspace = await session.get(Workspace, 1)
        starting_workspace_revision = workspace.revision
        await session.commit()
    source = HistoryEntry(
        message_id=message.message_id,
        sender_id=message.from_user.id if message.from_user else None,
        role="user",
        text=message.text,
        created_at=message.date.astimezone(UTC),
        kind=MessageKind.DIALOGUE_USER.value,
    )
    dialogue_revision = services.guard.dialogue_revision
    await services.guard.acquire(message.message_id)
    try:
        await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
        dialogue = await services.history.dialogue(message.chat.id, source_message=source)
        outcome = await services.advisor.handle(
            message.text,
            source_message_id=message.message_id,
            dialogue=dialogue,
        )
        async with services.sessions() as session:
            workspace = await session.get(Workspace, 1)
            if (
                services.guard.dialogue_revision != dialogue_revision
                or workspace.revision != starting_workspace_revision
            ):
                return
        await render_ai_outcome(message, services, outcome)
        # The foreground response is now visible. Release its lease before any
        # optional continuity work so proposal callbacks and new dialogue are not
        # rejected while summary generation is running.
        services.guard.release(message.message_id)

        async def send_summary(text: str, covered_id: int) -> None:
            sent = await message.answer(html.escape(text))
            async with services.sessions() as session:
                await register_message(
                    session,
                    sent.chat.id,
                    sent.message_id,
                    "out",
                    MessageKind.SUMMARY,
                )
                state = await session.get(SummaryState, 1)
                if state is None:
                    state = SummaryState(id=1)
                    session.add(state)
                state.summary_message_id = sent.message_id
                state.covered_message_id = covered_id
                state.estimated_tokens = estimate_tokens(text)
                await session.commit()

        await services.continuity.maybe_summarize(message.chat.id, send_summary)
    except HistoryBoundaryMissing as error:
        await send_registered(
            message,
            services,
            html.escape(str(error)),
            kind=MessageKind.ERROR,
        )
    except Exception as error:
        logger.exception("Could not handle ordinary text")
        await send_registered(
            message,
            services,
            "Safwa could not complete that request. Your planning data was not changed.\n"
            + html.escape(str(error)),
            kind=MessageKind.ERROR,
        )
    finally:
        services.guard.release(message.message_id)
