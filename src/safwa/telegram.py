from __future__ import annotations

import html
import json
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
    DomainError,
    StaleStateError,
    archive_subtree,
    archive_tag,
    archive_value,
    create_tag,
    create_value,
    delete_subtree,
    edit_card_text,
    finish_action,
    finish_sprint,
    move_card,
    set_card_parent,
    set_feedback,
    set_value_focus,
    snooze_reminders,
    sprint_metrics,
    start_sprint,
    toggle_card_category,
    toggle_card_dependency,
    toggle_card_energy_type,
    toggle_card_tag,
    toggle_card_value,
    update_card_fields,
    update_profile,
    update_tag_fields,
    update_value_fields,
)
from .drafts import DraftService
from .enums import (
    CardKind,
    CardStage,
    Category,
    DraftStatus,
    EnergyType,
    MessageKind,
    Priority,
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
    CardDependency,
    CardDraft,
    CardDraftBundle,
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


def _draft_list(values: list[object]) -> str:
    return ", ".join(html.escape(str(value)) for value in values) or "—"


def _draft_provenance(value: object) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return repr(value)


async def card_draft_discard_summary(session: AsyncSession, draft: CardDraft) -> str:
    """Render every user-visible field of one proposed Card before discarding it."""
    from .models import DraftCategory, DraftDependency, DraftEnergyType, DraftTag, DraftValue

    parent_text: str
    if draft.parent_draft_id is not None:
        parent_draft = await session.get(CardDraft, draft.parent_draft_id)
        parent_text = (
            f"{parent_draft.title or '(untitled)'} (proposed {parent_draft.kind.title()})"
            if parent_draft is not None
            else f"Missing proposed card #{draft.parent_draft_id}"
        )
    elif draft.parent_id is not None:
        parent = await session.get(Card, draft.parent_id)
        parent_text = parent.title if parent is not None else f"Missing card #{draft.parent_id}"
    else:
        parent_text = "Root" if draft.root_confirmed else "Unresolved"

    categories = list(
        await session.scalars(
            select(DraftCategory.category)
            .where(DraftCategory.draft_id == draft.id)
            .order_by(DraftCategory.category)
        )
    )
    energies = list(
        await session.scalars(
            select(DraftEnergyType.energy_type)
            .where(DraftEnergyType.draft_id == draft.id)
            .order_by(DraftEnergyType.energy_type)
        )
    )
    value_ids = list(
        await session.scalars(select(DraftValue.value_id).where(DraftValue.draft_id == draft.id))
    )
    values = (
        list(
            await session.scalars(
                select(Value.name).where(Value.id.in_(value_ids)).order_by(Value.name)
            )
        )
        if value_ids
        else []
    )
    tag_ids = list(
        await session.scalars(select(DraftTag.tag_id).where(DraftTag.draft_id == draft.id))
    )
    tags = (
        list(await session.scalars(select(Tag.name).where(Tag.id.in_(tag_ids)).order_by(Tag.name)))
        if tag_ids
        else []
    )
    dependencies = list(
        await session.scalars(
            select(DraftDependency)
            .where(DraftDependency.draft_id == draft.id)
            .order_by(DraftDependency.blocker_card_id)
        )
    )
    blocker_ids = [dependency.blocker_card_id for dependency in dependencies]
    blocker_cards = (
        {
            card.id: card
            for card in await session.scalars(select(Card).where(Card.id.in_(blocker_ids)))
        }
        if blocker_ids
        else {}
    )
    blockers = []
    for dependency in dependencies:
        blocker = blocker_cards.get(dependency.blocker_card_id)
        label = (
            blocker.title if blocker is not None else f"Missing card #{dependency.blocker_card_id}"
        )
        if dependency.copy_to_repeat:
            label += " (copy to repeat)"
        blockers.append(label)

    assumptions = [
        f"{field}: {_draft_provenance(value)}"
        for field, value in sorted((draft.field_provenance or {}).items())
        if field not in {"draft_ref", "unresolved"}
    ]
    validation = list(draft.validation_errors or [])

    lines = [
        f"• <b>Create {html.escape(draft.kind.title())}: "
        f"{html.escape(draft.title or '(untitled)')}</b>",
        f"Parent: {html.escape(parent_text)}",
        f"Stage: {html.escape(draft.stage.title())}",
        f"Note: {html.escape(draft.note or '—')}",
        f"Priority: {html.escape(draft.priority.title())}",
        f"Hard Time: {'Yes' if draft.hard_time else 'No'}",
    ]
    if draft.kind == CardKind.ACTION.value:
        lines.extend(
            [
                f"Effort: {draft.effort_points if draft.effort_points is not None else 'Unresolved'}",
                f"Repeatable: {'Yes' if draft.repeatable else 'No'}",
                f"Categories: {_draft_list(categories)}",
                f"Energy: {_draft_list(energies)}",
            ]
        )
    lines.extend(
        [
            f"Values: {_draft_list(values)}",
            f"Tags: {_draft_list(tags)}",
            f"Blockers: {_draft_list(blockers)}",
            f"AI assumptions: {_draft_list(assumptions)}",
            f"Unresolved / validation: {_draft_list(validation)}",
        ]
    )
    return "\n".join(lines)


async def discarded_card_bundle_message(
    session: AsyncSession,
    drafts: list[CardDraft],
    *,
    continued_conversation: bool,
) -> str:
    details = [
        await card_draft_discard_summary(session, draft)
        for draft in drafts
        if draft.status not in {DraftStatus.COMMITTED.value, DraftStatus.DISCARDED.value}
    ]
    reason = (
        "You continued the conversation without saving it."
        if continued_conversation
        else "Nothing was saved."
    )
    return "<b>Proposal discarded</b>\n" + reason + "\n\n" + "\n\n".join(details)


async def dismiss_prior_ui(message: Message, services: Services) -> None:
    """Ensure an interaction screen is never left active above new dialogue."""
    ui_kinds = {
        MessageKind.DASHBOARD.value,
        MessageKind.DRAFT_REVIEW.value,
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

    resolved_proposals: set[int] = set()
    resolved_bundles: set[int] = set()
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
                    proposal.status = "rejected"
                    replacement = (
                        "<b>Proposal discarded</b>\n"
                        "You continued the conversation without saving it.\n\n"
                        + "\n".join(
                            f"• {html.escape(proposal_change_summary(change))}"
                            for change in changes
                        )
                    )
                    await session.commit()
                    resolved_proposals.add(proposal.id)
                    advisor = getattr(services, "advisor", None)
                    if advisor is not None:
                        await advisor.cancel_approval_for_target("proposal", proposal.id)
                elif screen.related_id in resolved_proposals:
                    replacement = None
        elif screen.kind == MessageKind.DRAFT_REVIEW.value and screen.related_id:
            async with services.sessions() as session:
                draft = await session.get(CardDraft, screen.related_id)
                bundle = (
                    await session.get(CardDraftBundle, draft.bundle_id)
                    if draft is not None
                    else None
                )
                if (
                    bundle is not None
                    and bundle.origin == "ai"
                    and bundle.status not in {"committed", "discarded", "expired"}
                ):
                    draft_service = DraftService(session)
                    drafts = await draft_service.get_bundle_drafts(bundle.id)
                    replacement = await discarded_card_bundle_message(
                        session,
                        drafts,
                        continued_conversation=True,
                    )
                    await draft_service.discard_bundle(bundle.id)
                    await session.commit()
                    resolved_bundles.add(bundle.id)
                    advisor = getattr(services, "advisor", None)
                    if advisor is not None:
                        await advisor.cancel_approval_for_target("draft_bundle", bundle.id)
                elif bundle is not None and bundle.id in resolved_bundles:
                    replacement = None

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
                InlineKeyboardButton(text="📝 Drafts", callback_data="nav:drafts"),
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


async def render_dashboard(
    message: Message,
    services: Services,
    stage: CardStage,
    *,
    title: str,
    page: int = 0,
) -> None:
    priority_order = {Priority.CRITICAL.value: 0, Priority.MEDIUM.value: 1, Priority.LOW.value: 2}
    async with services.sessions() as session:
        cards = list(
            await session.scalars(
                select(Card).where(
                    Card.effective_stage == stage.value,
                    Card.archived_at.is_(None),
                )
            )
        )
        cards.sort(key=lambda c: (not c.hard_time, priority_order[c.priority], c.created_at))
        page_size = 5
        max_page = max(0, (len(cards) - 1) // page_size)
        page = min(max(page, 0), max_page)
        visible = cards[page * page_size : (page + 1) * page_size]
        rows: list[list[InlineKeyboardButton]] = []
        descriptions: list[str] = []
        for card in visible:
            blocker_count = int(
                await session.scalar(
                    select(func.count())
                    .select_from(CardDependency)
                    .where(CardDependency.blocked_card_id == card.id)
                )
                or 0
            )
            metadata = [
                card.kind.title(),
                card.priority.title(),
                f"{card.effort_points or '—'} EP",
            ]
            if card.hard_time:
                metadata.append("Hard time")
            if card.repeatable:
                metadata.append("Repeat")
            if blocker_count:
                metadata.append(f"{blocker_count} blocker{'s' if blocker_count != 1 else ''}")
            label = f"{card.title} · {' · '.join(metadata)}"
            descriptions.append(f"• {label}")
            rows.append(
                [
                    await token_button(
                        session,
                        services.owner_id,
                        label[:60],
                        "card_view",
                        {
                            "id": card.id,
                            "back": {
                                "kind": "dashboard",
                                "stage": stage.value,
                                "title": title,
                                "page": page,
                            },
                        },
                    )
                ]
            )
        paging: list[InlineKeyboardButton] = []
        if page > 0:
            paging.append(
                await token_button(
                    session,
                    services.owner_id,
                    "◀ Previous",
                    "dashboard_page",
                    {"stage": stage.value, "title": title, "page": page - 1},
                )
            )
        if page < max_page:
            paging.append(
                await token_button(
                    session,
                    services.owner_id,
                    "Next ▶",
                    "dashboard_page",
                    {"stage": stage.value, "title": title, "page": page + 1},
                )
            )
        if paging:
            rows.append(paging)
        rows.append(menu_row())
        await session.commit()
    text = f"<b>{html.escape(title)}</b> · page {page + 1}/{max_page + 1}\n"
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


async def draft_review_markup(
    session: AsyncSession, services: Services, draft: CardDraft
) -> InlineKeyboardMarkup:
    fields = [
        ("🧩 Kind", "draft_choose_kind", {"id": draft.id}),
        ("✏️ Title", "draft_edit_text", {"id": draft.id, "field": "title"}),
        ("🌳 Parent", "draft_choose_parent", {"id": draft.id}),
        ("📍 Stage", "draft_choose_stage", {"id": draft.id}),
        ("📝 Note", "draft_edit_text", {"id": draft.id, "field": "note"}),
        ("⚠️ Priority", "draft_choose_priority", {"id": draft.id}),
        ("⏱ Hard Time", "draft_toggle", {"id": draft.id, "field": "hard_time"}),
        ("🔢 Effort", "draft_choose_effort", {"id": draft.id}),
        ("🔁 Repeat", "draft_toggle", {"id": draft.id, "field": "repeatable"}),
        ("🏷 Categories", "draft_choose_categories", {"id": draft.id}),
        ("⚡ Energy", "draft_choose_energy", {"id": draft.id}),
        ("💎 Values", "draft_choose_values", {"id": draft.id}),
        ("🏷 Tags", "draft_choose_tags", {"id": draft.id}),
        ("🚧 Blockers", "draft_choose_blockers", {"id": draft.id}),
    ]
    if draft.kind != CardKind.ACTION.value:
        action_only = {
            "draft_choose_effort",
            "draft_choose_categories",
            "draft_choose_energy",
        }
        fields = [
            field
            for field in fields
            if field[1] not in action_only
            and not (field[1] == "draft_toggle" and field[2].get("field") == "repeatable")
        ]
    buttons = [await token_button(session, services.owner_id, *item) for item in fields]
    rows = [buttons[index : index + 2] for index in range(0, len(buttons), 2)]
    bundle_drafts = list(
        await session.scalars(
            select(CardDraft)
            .where(
                CardDraft.bundle_id == draft.bundle_id,
                CardDraft.status != DraftStatus.DISCARDED.value,
            )
            .order_by(CardDraft.created_at, CardDraft.id)
        )
    )
    bundle = await session.get(CardDraftBundle, draft.bundle_id)
    ai_origin = bundle is not None and bundle.origin == "ai"
    if len(bundle_drafts) == 1 and not draft.validation_errors:
        rows.append(
            [
                await token_button(
                    session,
                    services.owner_id,
                    "✅ Save" if ai_origin else "✅ Create",
                    "draft_commit",
                    {"id": draft.id},
                )
            ]
        )
    elif len(bundle_drafts) > 1:
        position = next(i for i, item in enumerate(bundle_drafts) if item.id == draft.id)
        navigation: list[InlineKeyboardButton] = []
        if position:
            navigation.append(
                await token_button(
                    session,
                    services.owner_id,
                    "◀ Previous",
                    "draft_view",
                    {"id": bundle_drafts[position - 1].id},
                )
            )
        if position + 1 < len(bundle_drafts):
            navigation.append(
                await token_button(
                    session,
                    services.owner_id,
                    "Next ▶",
                    "draft_view",
                    {"id": bundle_drafts[position + 1].id},
                )
            )
        if navigation:
            rows.append(navigation)
        if not draft.validation_errors and draft.reviewed_at is None:
            rows.append(
                [
                    await token_button(
                        session,
                        services.owner_id,
                        "✓ Mark reviewed",
                        "draft_mark_reviewed",
                        {"id": draft.id},
                    )
                ]
            )
        ready_to_commit = all(
            item.reviewed_at is not None and not item.validation_errors for item in bundle_drafts
        )
        if ready_to_commit:
            rows.append(
                [
                    await token_button(
                        session,
                        services.owner_id,
                        (
                            f"✅ Save all {len(bundle_drafts)}"
                            if ai_origin
                            else f"✅ Create all {len(bundle_drafts)}"
                        ),
                        "bundle_commit",
                        {"id": draft.bundle_id},
                    )
                ]
            )
    rows.append(
        [
            await token_button(
                session, services.owner_id, "🗑 Discard", "draft_discard", {"id": draft.id}
            )
        ]
    )
    if not ai_origin:
        rows.append([InlineKeyboardButton(text="↩️ Back", callback_data="nav:drafts")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def render_draft(
    message: Message,
    services: Services,
    draft_id: int,
    *,
    replace_message_id: int | None = None,
) -> None:
    async with services.sessions() as session:
        draft = await session.get(CardDraft, draft_id)
        if draft is None:
            await send_registered(
                message, services, "Draft no longer exists.", kind=MessageKind.ERROR
            )
            return
        await DraftService(session).validate(draft)
        parent = await session.get(Card, draft.parent_id) if draft.parent_id else None
        # Draft tag rows are read with explicit imports to keep committed and draft data isolated.
        from .models import DraftCategory, DraftDependency, DraftEnergyType, DraftTag, DraftValue

        categories = list(
            await session.scalars(
                select(DraftCategory.category).where(DraftCategory.draft_id == draft.id)
            )
        )
        energies = list(
            await session.scalars(
                select(DraftEnergyType.energy_type).where(DraftEnergyType.draft_id == draft.id)
            )
        )
        value_ids = list(
            await session.scalars(
                select(DraftValue.value_id).where(DraftValue.draft_id == draft.id)
            )
        )
        values = (
            list(await session.scalars(select(Value).where(Value.id.in_(value_ids))))
            if value_ids
            else []
        )
        tag_ids = list(
            await session.scalars(select(DraftTag.tag_id).where(DraftTag.draft_id == draft.id))
        )
        tags = (
            list(await session.scalars(select(Tag).where(Tag.id.in_(tag_ids)))) if tag_ids else []
        )
        blocker_ids = list(
            await session.scalars(
                select(DraftDependency.blocker_card_id).where(DraftDependency.draft_id == draft.id)
            )
        )
        blockers = (
            list(await session.scalars(select(Card).where(Card.id.in_(blocker_ids))))
            if blocker_ids
            else []
        )
        markup = await draft_review_markup(session, services, draft)
        await session.commit()
        errors = "\n".join(f"⚠️ {html.escape(error)}" for error in draft.validation_errors)
        action_details = ""
        if draft.kind == CardKind.ACTION.value:
            action_details = (
                f"Effort: {draft.effort_points or 'Unresolved'}\n"
                f"Repeatable: {'Yes' if draft.repeatable else 'No'}\n"
                f"Categories: {', '.join(categories) or '—'}\n"
                f"Energy: {', '.join(energies) or '—'}\n"
            )
        text = (
            "<b>Review card draft</b>\n"
            f"Kind: {draft.kind.title()}\n"
            f"Title: <b>{html.escape(draft.title or '—')}</b>\n"
            f"Parent: {html.escape(parent.title if parent else ('Root' if draft.root_confirmed else 'Unresolved'))}\n"
            f"Stage: {draft.stage.title()}\n"
            f"Note: {html.escape(draft.note or '—')}\n"
            f"Priority: {draft.priority.title()} · Hard Time: {'Yes' if draft.hard_time else 'No'}\n"
            f"{action_details}"
            f"Values: {', '.join(value.name for value in values) or '—'}\n"
            f"Tags: {', '.join(tag.name for tag in tags) or '—'}\n"
            f"Blockers: {', '.join(card.title for card in blockers) or '—'}"
        )
        if errors:
            text += "\n\n" + errors
    if replace_message_id is not None:
        await edit_registered_message(
            message,
            services,
            replace_message_id,
            text,
            kind=MessageKind.DRAFT_REVIEW,
            markup=markup,
            related_id=draft_id,
        )
    else:
        await send_registered(
            message,
            services,
            text,
            kind=MessageKind.DRAFT_REVIEW,
            markup=markup,
            related_id=draft_id,
        )


async def start_manual_draft(message: Message, services: Services) -> None:
    async with services.sessions() as session:
        bundle = await DraftService(session).create_bundle(
            "manual",
            [
                {
                    "kind": CardKind.ACTION.value,
                    "title": "",
                    "root_confirmed": True,
                    "stage": CardStage.BACKLOG.value,
                }
            ],
        )
        draft_id = bundle.active_draft_id
        await session.commit()
    if draft_id:
        await render_draft(message, services, draft_id)


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
    await start_manual_draft(message, services)


@router.message(Command("drafts"))
async def command_drafts(message: Message, services: Services) -> None:
    async with services.sessions() as session:
        drafts = list(
            await session.scalars(
                select(CardDraft)
                .where(
                    CardDraft.status.in_(
                        [
                            DraftStatus.EDITING.value,
                            DraftStatus.READY.value,
                            DraftStatus.REVIEWED.value,
                        ]
                    )
                )
                .order_by(CardDraft.updated_at.desc())
            )
        )
        rows = [
            [
                await token_button(
                    session,
                    services.owner_id,
                    draft.title or "Untitled",
                    "draft_view",
                    {"id": draft.id},
                )
            ]
            for draft in drafts[:20]
        ]
        await session.commit()
    await send_registered(
        message,
        services,
        f"<b>Drafts</b> · {len(drafts)} pending",
        kind=MessageKind.DASHBOARD,
        markup=InlineKeyboardMarkup(inline_keyboard=rows + [menu_row()]),
    )


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
    if entity not in {"tag", "value"} or mode not in {"create", "view"}:
        raise DomainError("Unsupported item editor")
    linked_count = 0
    async with services.sessions() as session:
        item: Tag | Value | None = None
        if mode == "view":
            model = Tag if entity == "tag" else Value
            item = await session.get(model, item_id)
            if item is None or item.archived_at is not None:
                raise DomainError(f"{entity.title()} does not exist")
            editor_values = {"name": item.name, "description": item.description}
            link_model = CardTag if entity == "tag" else CardValue
            link_field = CardTag.tag_id if entity == "tag" else CardValue.value_id
            linked_count = int(
                await session.scalar(
                    select(func.count()).select_from(link_model).where(link_field == item.id)
                )
                or 0
            )
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
        cards = (await request_cards(session, request.query_sql))[:25]
        rows = [
            [
                await token_button(
                    session,
                    services.owner_id,
                    f"{card.kind.title()} · {card.title}"[:60],
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
    details += f"\n\n{len(cards)} matching card{'s' if len(cards) != 1 else ''}"
    if len(cards) == 25:
        details += " (showing first 25)"
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


async def render_feedback(message: Message, services: Services) -> None:
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
                message, services, "No completion feedback pending.", kind=MessageKind.DASHBOARD
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
        f"<b>Feedback 1/{len(pending)}</b>\nDid you like doing <b>{html.escape(card.title)}</b>?",
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
        drafts = (
            await session.scalar(
                select(func.count(CardDraft.id)).where(
                    CardDraft.status.in_(["editing", "ready", "reviewed"])
                )
            )
            or 0
        )
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
        f"Drafts: {drafts}\nFeedback: {feedback}\nMemory: {'OK' if memory.valid else 'ERROR'}",
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
        "drafts": command_drafts,
        "values": command_values,
        "tags": command_tags,
        "requests": command_requests,
        "advisor": command_advisor,
        "retro": command_retro,
        "settings": command_settings,
    }
    await handlers[action](callback.message, services)


async def choice_screen(
    message: Message,
    services: Services,
    title: str,
    choices: list[tuple[str, str, dict[str, Any]]],
    *,
    back: tuple[str, str, dict[str, Any]] | None = None,
) -> None:
    async with services.sessions() as session:
        rows = [
            [await token_button(session, services.owner_id, text, action, payload)]
            for text, action, payload in choices
        ]
        if back:
            rows.append([await token_button(session, services.owner_id, *back)])
        await session.commit()
    await send_registered(
        message,
        services,
        f"<b>{html.escape(title)}</b>",
        kind=MessageKind.DRAFT_REVIEW,
        markup=InlineKeyboardMarkup(inline_keyboard=rows),
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

    try:
        if action in {"value_create_prompt", "tag_create_prompt"}:
            entity = "value" if action == "value_create_prompt" else "tag"
            await render_item_editor(callback.message, services, entity, mode="create")
            return
        if action == "item_view":
            await render_item_editor(
                callback.message,
                services,
                payload["entity"],
                mode="view",
                item_id=payload["id"],
            )
            return
        if action == "item_edit_text":
            await render_item_text_prompt(
                callback.message,
                services,
                entity=payload["entity"],
                mode=payload["mode"],
                item_id=payload.get("id"),
                field=payload["field"],
            )
            return
        if action == "item_text_back":
            async with services.sessions() as session:
                editor = await session.scalar(
                    select(UiSession).where(UiSession.owner_id == services.owner_id)
                )
                values = dict(editor.state.get("values", {})) if editor is not None else {}
            await render_item_editor(
                callback.message,
                services,
                payload["entity"],
                mode=payload["mode"],
                item_id=payload.get("id"),
                values=values,
            )
            return
        if action == "item_create":
            async with services.sessions() as session:
                editor = await session.scalar(
                    select(UiSession).where(UiSession.owner_id == services.owner_id)
                )
                if editor is None or editor.kind != "item_editor":
                    raise DomainError("Item editor expired")
                values = dict(editor.state.get("values", {}))
                if payload["entity"] == "value":
                    item = await create_value(
                        session, values.get("name", ""), values.get("description", "")
                    )
                else:
                    item = await create_tag(
                        session, values.get("name", ""), values.get("description", "")
                    )
                await session.commit()
            await render_item_editor(
                callback.message,
                services,
                payload["entity"],
                mode="view",
                item_id=item.id,
            )
            return
        if action == "item_toggle_focus":
            async with services.sessions() as session:
                await set_value_focus(session, payload["id"])
                await session.commit()
            await render_item_editor(
                callback.message,
                services,
                "value",
                mode="view",
                item_id=payload["id"],
            )
            return
        if action == "item_archive_prompt":
            async with services.sessions() as session:
                model = Tag if payload["entity"] == "tag" else Value
                item = await session.get(model, payload["id"])
                if item is None or item.archived_at is not None:
                    raise DomainError(f"{payload['entity'].title()} does not exist")
                link_model = CardTag if payload["entity"] == "tag" else CardValue
                link_field = CardTag.tag_id if payload["entity"] == "tag" else CardValue.value_id
                linked_count = int(
                    await session.scalar(
                        select(func.count()).select_from(link_model).where(link_field == item.id)
                    )
                    or 0
                )
                confirm = await token_button(
                    session,
                    services.owner_id,
                    f"Archive {payload['entity'].title()}",
                    "item_archive_confirm",
                    {"entity": payload["entity"], "id": item.id},
                )
                back = await token_button(
                    session,
                    services.owner_id,
                    "↩️ Back",
                    "item_view",
                    {"entity": payload["entity"], "id": item.id},
                )
                await session.commit()
            await send_registered(
                callback.message,
                services,
                f"<b>Archive {html.escape(payload['entity'].title())}?</b>\n"
                f"{html.escape(item.name)} will be removed from active lists and unlinked from "
                f"{linked_count} Card(s).",
                kind=MessageKind.APPROVAL,
                markup=InlineKeyboardMarkup(inline_keyboard=[[confirm], [back]]),
                related_id=item.id,
            )
            return
        if action == "item_archive_confirm":
            async with services.sessions() as session:
                if payload["entity"] == "tag":
                    item, linked_count = await archive_tag(session, payload["id"])
                else:
                    item, linked_count = await archive_value(session, payload["id"])
                await session.execute(
                    delete(UiSession).where(UiSession.owner_id == services.owner_id)
                )
                back = await token_button(
                    session,
                    services.owner_id,
                    f"Back to {payload['entity'].title()}s",
                    "item_back",
                    {"entity": payload["entity"]},
                )
                await session.commit()
            await send_registered(
                callback.message,
                services,
                f"Archived <b>{html.escape(item.name)}</b>. Removed {linked_count} Card link(s).",
                kind=MessageKind.RECEIPT,
                markup=InlineKeyboardMarkup(inline_keyboard=[[back]]),
                related_id=item.id,
            )
            return
        if action == "item_back":
            async with services.sessions() as session:
                await session.execute(
                    delete(UiSession).where(UiSession.owner_id == services.owner_id)
                )
                await session.commit()
            if payload["entity"] == "value":
                await command_values(callback.message, services)
            else:
                await command_tags(callback.message, services)
            return
        if action == "request_view":
            await render_saved_request(callback.message, services, payload["id"])
            return
        if action == "subsession_confirm":
            await send_registered(
                callback.message,
                services,
                "<b>Compressing subsession…</b>",
                kind=MessageKind.APPROVAL,
            )
            try:
                await end_subsession(
                    callback.message,
                    services,
                    start_message_id=int(payload["start_message_id"]),
                    instruction=str(payload.get("instruction", "")),
                )
            except Exception:
                logger.exception("Could not end Safwa subsession")
                await send_registered(
                    callback.message,
                    services,
                    "Safwa could not compress the subsession. The branch was not deleted.",
                    kind=MessageKind.ERROR,
                    markup=InlineKeyboardMarkup(inline_keyboard=[menu_row()]),
                )
        elif action == "subsession_cancel":
            await send_registered(
                callback.message,
                services,
                "Subsession end cancelled. The branch is unchanged.",
                kind=MessageKind.RECEIPT,
                markup=InlineKeyboardMarkup(inline_keyboard=[menu_row()]),
            )
        elif action == "draft_view":
            await render_draft(callback.message, services, payload["id"])
        elif action == "dashboard_page":
            await render_dashboard(
                callback.message,
                services,
                CardStage(payload["stage"]),
                title=payload["title"],
                page=int(payload["page"]),
            )
        elif action == "draft_edit_text":
            async with services.sessions() as session:
                await session.execute(
                    delete(UiSession).where(UiSession.owner_id == services.owner_id)
                )
                draft = await session.get(CardDraft, payload["id"])
                if draft is None:
                    raise DomainError("Draft does not exist")
                session.add(
                    UiSession(
                        owner_id=services.owner_id,
                        kind="draft_text",
                        state={
                            "draft_id": payload["id"],
                            "field": payload["field"],
                            "message_id": callback.message.message_id,
                        },
                        expires_at=datetime.now(UTC) + timedelta(minutes=30),
                    )
                )
                back = await token_button(
                    session,
                    services.owner_id,
                    "↩️ Back",
                    "draft_view",
                    {"id": payload["id"]},
                )
                await session.commit()
            current = str(getattr(draft, payload["field"]) or "")
            await send_registered(
                callback.message,
                services,
                f"<b>Current {html.escape(payload['field'])}</b>: "
                f"{html.escape(current or '—')}\n\n"
                f"Set new {html.escape(payload['field'].title())}",
                kind=MessageKind.DRAFT_REVIEW,
                markup=InlineKeyboardMarkup(inline_keyboard=[[back]]),
                related_id=payload["id"],
            )
        elif action == "draft_toggle":
            async with services.sessions() as session:
                draft = await session.get(CardDraft, payload["id"])
                await DraftService(session).update(
                    draft.id, **{payload["field"]: not getattr(draft, payload["field"])}
                )
                await session.commit()
            await render_draft(callback.message, services, payload["id"])
        elif action.startswith("draft_choose_"):
            await handle_draft_chooser(callback.message, services, action, payload["id"])
        elif action == "draft_set":
            async with services.sessions() as session:
                await DraftService(session).update(
                    payload["id"], **{payload["field"]: payload["value"]}
                )
                await session.commit()
            await render_draft(callback.message, services, payload["id"])
        elif action == "draft_toggle_category":
            async with services.sessions() as session:
                from .models import DraftCategory

                current = set(
                    await session.scalars(
                        select(DraftCategory.category).where(
                            DraftCategory.draft_id == payload["id"]
                        )
                    )
                )
                value = Category(payload["value"])
                current.symmetric_difference_update({value.value})
                await DraftService(session).set_categories(
                    payload["id"], {Category(v) for v in current}
                )
                await session.commit()
            await render_draft(callback.message, services, payload["id"])
        elif action == "draft_toggle_energy":
            async with services.sessions() as session:
                from .models import DraftEnergyType

                current = set(
                    await session.scalars(
                        select(DraftEnergyType.energy_type).where(
                            DraftEnergyType.draft_id == payload["id"]
                        )
                    )
                )
                value = EnergyType(payload["value"])
                current.symmetric_difference_update({value.value})
                await DraftService(session).set_energy_types(
                    payload["id"], {EnergyType(v) for v in current}
                )
                await session.commit()
            await render_draft(callback.message, services, payload["id"])
        elif action == "draft_toggle_value":
            async with services.sessions() as session:
                await DraftService(session).toggle_value(payload["id"], payload["value_id"])
                await session.commit()
            await render_draft(callback.message, services, payload["id"])
        elif action == "draft_toggle_tag":
            async with services.sessions() as session:
                await DraftService(session).toggle_tag(payload["id"], payload["tag_id"])
                await session.commit()
            await render_draft(callback.message, services, payload["id"])
        elif action == "draft_toggle_blocker":
            async with services.sessions() as session:
                await DraftService(session).toggle_dependency(payload["id"], payload["card_id"])
                await session.commit()
            await render_draft(callback.message, services, payload["id"])
        elif action == "draft_set_parent":
            async with services.sessions() as session:
                card = await session.get(Card, payload["parent_id"])
                await DraftService(session).update(
                    payload["id"],
                    parent_id=card.id,
                    root_confirmed=False,
                )
                await session.commit()
            await render_draft(callback.message, services, payload["id"])
        elif action == "draft_set_root":
            async with services.sessions() as session:
                await DraftService(session).update(
                    payload["id"], parent_id=None, root_confirmed=True
                )
                await session.commit()
            await render_draft(callback.message, services, payload["id"])
        elif action == "draft_commit":
            async with services.sessions() as session:
                draft = await DraftService(session).mark_reviewed(payload["id"])
                cards = await DraftService(session).commit_bundle(draft.bundle_id)
                bundle_id = draft.bundle_id
                await session.commit()
            if await continue_agent_approval(
                callback.message,
                services,
                "draft_bundle",
                bundle_id,
                decision="approved",
                result={
                    "created_card_ids": [card.id for card in cards],
                    "created_titles": [card.title for card in cards],
                },
            ):
                return
            await send_registered(
                callback.message,
                services,
                f"✅ Created <b>{html.escape(cards[0].title)}</b>.",
                # This is a visible, meaningful assistant outcome, not an internal
                # operation receipt. Keep it in the canonical Telegram dialogue.
                kind=MessageKind.DIALOGUE_ASSISTANT,
                related_id=cards[0].id,
            )
        elif action == "draft_mark_reviewed":
            async with services.sessions() as session:
                draft = await DraftService(session).mark_reviewed(payload["id"])
                await session.commit()
            await render_draft(callback.message, services, draft.id)
        elif action == "bundle_commit":
            async with services.sessions() as session:
                cards = await DraftService(session).commit_bundle(payload["id"])
                await session.commit()
            if await continue_agent_approval(
                callback.message,
                services,
                "draft_bundle",
                payload["id"],
                decision="approved",
                result={
                    "created_card_ids": [card.id for card in cards],
                    "created_titles": [card.title for card in cards],
                },
            ):
                return
            await send_registered(
                callback.message,
                services,
                f"✅ Created {len(cards)} reviewed cards.",
                kind=MessageKind.DIALOGUE_ASSISTANT,
            )
        elif action == "draft_discard":
            async with services.sessions() as session:
                draft = await session.get(CardDraft, payload["id"])
                title = draft.title if draft is not None else "card"
                bundle = (
                    await session.get(CardDraftBundle, draft.bundle_id)
                    if draft is not None
                    else None
                )
                discarded_message = None
                if bundle is not None and bundle.origin == "ai":
                    draft_service = DraftService(session)
                    discarded = await draft_service.get_bundle_drafts(bundle.id)
                    discarded_message = await discarded_card_bundle_message(
                        session,
                        discarded,
                        continued_conversation=False,
                    )
                    await draft_service.discard_bundle(bundle.id)
                else:
                    await DraftService(session).discard(payload["id"])
                await session.commit()
            if bundle is not None and bundle.origin == "ai":
                if await continue_agent_approval(
                    callback.message,
                    services,
                    "draft_bundle",
                    bundle.id,
                    decision="discarded",
                    result={"proposal": discarded_message or "Card draft discarded"},
                ):
                    return
                await send_registered(
                    callback.message,
                    services,
                    discarded_message or "<b>Proposal discarded</b>\nNothing was saved.",
                    kind=MessageKind.DIALOGUE_ASSISTANT,
                )
                return
            # Restore the normal menu in place. Its semantic kind is UI-only and
            # must not replace the durable conversational receipt below.
            await command_start(callback.message, services)
            await send_registered(
                callback.message,
                services,
                f"🗑 Discarded draft <b>{html.escape(title)}</b>.",
                kind=MessageKind.DIALOGUE_ASSISTANT,
                replace=False,
            )
        elif action == "card_view":
            await render_card(
                callback.message,
                services,
                payload["id"],
                back=payload.get("back"),
            )
        elif action == "card_back":
            back = payload.get("back") or {"kind": "home"}
            if back["kind"] == "dashboard":
                await render_dashboard(
                    callback.message,
                    services,
                    CardStage(back["stage"]),
                    title=back["title"],
                    page=int(back.get("page", 0)),
                )
            elif back["kind"] == "request":
                await render_saved_request(callback.message, services, int(back["id"]))
            else:
                await command_start(callback.message, services)
        elif action == "card_move":
            async with services.sessions() as session:
                result = await move_card(session, payload["id"], CardStage(payload["stage"]))
                await session.commit()
            if result.warnings:
                await send_registered(
                    callback.message,
                    services,
                    "⚠️ " + html.escape("; ".join(result.warnings)),
                    kind=MessageKind.ERROR,
                )
            await render_card(callback.message, services, payload["id"])
        elif action == "card_edit_text":
            async with services.sessions() as session:
                editor = await session.scalar(
                    select(UiSession).where(UiSession.owner_id == services.owner_id)
                )
                back_state = (
                    dict(editor.state.get("back", {}))
                    if editor is not None and editor.kind == "card_editor"
                    else {}
                )
                await session.execute(
                    delete(UiSession).where(UiSession.owner_id == services.owner_id)
                )
                card = await session.get(Card, payload["id"])
                if card is None:
                    raise DomainError("Card does not exist")
                session.add(
                    UiSession(
                        owner_id=services.owner_id,
                        kind="card_text",
                        state={
                            "card_id": payload["id"],
                            "field": payload["field"],
                            "message_id": callback.message.message_id,
                            "back": back_state,
                        },
                        expires_at=datetime.now(UTC) + timedelta(minutes=30),
                    )
                )
                back = await token_button(
                    session,
                    services.owner_id,
                    "↩️ Back",
                    "card_view",
                    {"id": payload["id"]},
                )
                await session.commit()
            current = str(getattr(card, payload["field"]) or "")
            await send_registered(
                callback.message,
                services,
                f"<b>Current {html.escape(payload['field'])}</b>: "
                f"{html.escape(current or '—')}\n\n"
                f"Set new {html.escape(payload['field'].title())}",
                kind=MessageKind.DASHBOARD,
                markup=InlineKeyboardMarkup(inline_keyboard=[[back]]),
                related_id=payload["id"],
            )
        elif action in {
            "card_choose_parent",
            "card_choose_stage",
            "card_choose_priority",
            "card_choose_effort",
            "card_choose_categories",
            "card_choose_energy",
        }:
            await render_card_choices(callback.message, services, action, payload["id"])
        elif action == "card_set_field":
            async with services.sessions() as session:
                await update_card_fields(
                    session,
                    payload["id"],
                    {payload["field"]: payload["value"]},
                )
                await session.commit()
            await render_card(callback.message, services, payload["id"])
        elif action == "card_toggle_field":
            async with services.sessions() as session:
                card = await session.get(Card, payload["id"])
                if card is None:
                    raise DomainError("Card does not exist")
                await update_card_fields(
                    session,
                    card.id,
                    {payload["field"]: not bool(getattr(card, payload["field"]))},
                )
                await session.commit()
            await render_card(callback.message, services, payload["id"])
        elif action == "card_toggle_category":
            async with services.sessions() as session:
                await toggle_card_category(session, payload["id"], Category(payload["value"]))
                await session.commit()
            await render_card_choices(
                callback.message, services, "card_choose_categories", payload["id"]
            )
        elif action == "card_toggle_energy":
            async with services.sessions() as session:
                await toggle_card_energy_type(session, payload["id"], EnergyType(payload["value"]))
                await session.commit()
            await render_card_choices(
                callback.message, services, "card_choose_energy", payload["id"]
            )
        elif action == "card_set_parent":
            async with services.sessions() as session:
                await set_card_parent(session, payload["id"], payload.get("parent_id"))
                await session.commit()
            await render_card(callback.message, services, payload["id"])
        elif action == "card_choose_values":
            await render_card_choices(callback.message, services, action, payload["id"])
        elif action == "card_toggle_value":
            async with services.sessions() as session:
                await toggle_card_value(session, payload["id"], payload["value_id"])
                await session.commit()
            await render_card_choices(
                callback.message, services, "card_choose_values", payload["id"]
            )
        elif action == "card_choose_tags":
            await render_card_choices(callback.message, services, action, payload["id"])
        elif action == "card_toggle_tag":
            async with services.sessions() as session:
                await toggle_card_tag(session, payload["id"], payload["tag_id"])
                await session.commit()
            await render_card_choices(callback.message, services, "card_choose_tags", payload["id"])
        elif action == "card_choose_blockers":
            await render_card_choices(callback.message, services, action, payload["id"])
        elif action == "card_toggle_blocker":
            async with services.sessions() as session:
                await toggle_card_dependency(session, payload["id"], payload["card_id"])
                await session.commit()
            await render_card_choices(
                callback.message, services, "card_choose_blockers", payload["id"]
            )
        elif action == "card_archive":
            async with services.sessions() as session:
                count = len(await archive_subtree(session, payload["id"]))
                undo = await token_button(
                    session,
                    services.owner_id,
                    "Undo archive",
                    "card_restore",
                    {"id": payload["id"]},
                )
                await session.commit()
            await send_registered(
                callback.message,
                services,
                f"Archived {count} card(s).",
                kind=MessageKind.RECEIPT,
                markup=InlineKeyboardMarkup(inline_keyboard=[[undo], menu_row()]),
            )
        elif action == "card_restore":
            async with services.sessions() as session:
                count = len(await archive_subtree(session, payload["id"], archive=False))
                await session.commit()
            await send_registered(
                callback.message,
                services,
                f"Restored {count} card(s).",
                kind=MessageKind.RECEIPT,
            )
        elif action == "card_delete_prompt":
            async with services.sessions() as session:
                confirm = await token_button(
                    session,
                    services.owner_id,
                    "Permanently delete tree",
                    "card_delete_confirm",
                    {"id": payload["id"]},
                )
                await session.commit()
            await send_registered(
                callback.message,
                services,
                "<b>Final confirmation</b>\nThis removes the tree and its historical contribution.",
                kind=MessageKind.APPROVAL,
                markup=InlineKeyboardMarkup(inline_keyboard=[[confirm], menu_row()]),
            )
        elif action == "card_delete_confirm":
            async with services.sessions() as session:
                count = await delete_subtree(session, payload["id"])
                await session.commit()
            await send_registered(
                callback.message,
                services,
                f"Permanently deleted {count} card(s).",
                kind=MessageKind.RECEIPT,
            )
        elif action == "card_finish":
            async with services.sessions() as session:
                result = await finish_action(session, payload["id"], CardStage(payload["stage"]))
                await session.commit()
            if result.warnings:
                await send_registered(
                    callback.message,
                    services,
                    "⚠️ " + html.escape("; ".join(result.warnings)),
                    kind=MessageKind.ERROR,
                )
            if payload["stage"] == CardStage.DONE.value:
                await render_feedback(callback.message, services)
            else:
                await send_registered(
                    callback.message, services, "Card updated.", kind=MessageKind.RECEIPT
                )
        elif action == "feedback":
            async with services.sessions() as session:
                card = await set_feedback(session, payload["id"], bool(payload["liked"]))
                await session.commit()
            await render_feedback(callback.message, services)
        elif action == "sprint_start":
            async with services.sessions() as session:
                profile = await session.get(UserProfile, 1)
                sprint = await start_sprint(
                    session, capacity=profile.capacity_effort_points if profile else None
                )
                await session.commit()
            await send_registered(
                callback.message,
                services,
                f"Sprint {sprint.number} started.",
                kind=MessageKind.RECEIPT,
            )
        elif action == "sprint_finish":
            async with services.sessions() as session:
                sprint = await finish_sprint(session, reason="finished_early")
                await session.commit()
            await send_registered(
                callback.message,
                services,
                f"Sprint {sprint.number} finished early.",
                kind=MessageKind.RECEIPT,
            )
        elif action == "proposal_view":
            await render_proposal(callback.message, services, payload["id"])
        elif action == "proposal_approve":
            async with services.sessions() as session:
                destructive = await session.scalar(
                    select(ProposalChange.id).where(
                        ProposalChange.proposal_id == payload["id"],
                        ProposalChange.action == "delete",
                    )
                )
                if destructive:
                    confirm = await token_button(
                        session,
                        services.owner_id,
                        "Permanently delete",
                        "proposal_delete_confirm",
                        {"id": payload["id"]},
                    )
                    await session.commit()
                    await send_registered(
                        callback.message,
                        services,
                        "<b>Final destructive confirmation</b>\nThis permanently removes the selected tree and its historical contribution.",
                        kind=MessageKind.APPROVAL,
                        markup=InlineKeyboardMarkup(inline_keyboard=[[confirm]]),
                    )
                    return
                affected = await ProposalService(session).apply(payload["id"])
                await session.commit()
            if await continue_agent_approval(
                callback.message,
                services,
                "proposal",
                payload["id"],
                decision="approved",
                result={"affected_ids": affected},
            ):
                return
            await send_registered(
                callback.message,
                services,
                f"✅ Saved proposal. Updated {len(affected)} item(s).",
                kind=MessageKind.DIALOGUE_ASSISTANT,
            )
        elif action == "proposal_delete_confirm":
            async with services.sessions() as session:
                affected = await ProposalService(session).apply(
                    payload["id"], allow_destructive=True
                )
                await session.commit()
            if await continue_agent_approval(
                callback.message,
                services,
                "proposal",
                payload["id"],
                decision="approved",
                result={"affected_ids": affected, "destructive": True},
            ):
                return
            await send_registered(
                callback.message,
                services,
                f"Permanently deleted {len(affected)} selected item(s).",
                kind=MessageKind.RECEIPT,
            )
        elif action == "proposal_edit":
            await render_proposal_edits(callback.message, services, payload["id"])
        elif action == "proposal_remove_change":
            async with services.sessions() as session:
                change = await session.get(ProposalChange, payload["change_id"])
                if change is None or change.proposal_id != payload["id"]:
                    raise DomainError("Proposal change no longer exists")
                await session.delete(change)
                await session.commit()
            await render_proposal_edits(callback.message, services, payload["id"])
        elif action == "proposal_reject":
            async with services.sessions() as session:
                await ProposalService(session).reject(payload["id"])
                await session.commit()
            if await continue_agent_approval(
                callback.message,
                services,
                "proposal",
                payload["id"],
                decision="discarded",
                result={"message": "The user discarded this proposed change."},
            ):
                return
            await send_registered(
                callback.message,
                services,
                "🗑 Proposal discarded.",
                kind=MessageKind.DIALOGUE_ASSISTANT,
            )
        elif action == "value_toggle":
            async with services.sessions() as session:
                await set_value_focus(session, payload["id"])
                await session.commit()
            await command_values(callback.message, services)
    except StaleStateError as error:
        if action.startswith("proposal_") and payload.get("id"):
            async with services.sessions() as session:
                proposal = await session.get(ChangeProposal, payload["id"])
                if proposal:
                    proposal.status = "stale"
                    await session.commit()
        if action in {
            "proposal_approve",
            "proposal_delete_confirm",
        } and await continue_agent_approval(
            callback.message,
            services,
            "proposal",
            payload["id"],
            decision="failed",
            result={"error": str(error)},
        ):
            return
        await send_registered(
            callback.message, services, html.escape(str(error)), kind=MessageKind.ERROR
        )
    except DomainError as error:
        if action in {
            "proposal_approve",
            "proposal_delete_confirm",
        } and await continue_agent_approval(
            callback.message,
            services,
            "proposal",
            payload["id"],
            decision="failed",
            result={"error": str(error)},
        ):
            return
        if action.startswith("proposal_") and payload.get("id"):
            try:
                await render_proposal(
                    callback.message,
                    services,
                    payload["id"],
                    notice=f"⚠️ {error}",
                )
                return
            except DomainError:
                pass
        await send_registered(
            callback.message, services, html.escape(str(error)), kind=MessageKind.ERROR
        )
    except Exception:
        logger.exception("Telegram callback failed: action=%s payload=%s", action, payload)
        if action.startswith("proposal_") and payload.get("id"):
            try:
                await render_proposal(
                    callback.message,
                    services,
                    payload["id"],
                    notice=(
                        "⚠️ This action failed. The proposal is still pending; "
                        "you can retry or discard it."
                    ),
                )
                return
            except Exception:
                logger.exception("Could not restore proposal UI after callback failure")
        await send_registered(
            callback.message,
            services,
            "Safwa could not finish this action. Reopen the screen and try again.",
            kind=MessageKind.ERROR,
        )


async def handle_draft_chooser(
    message: Message, services: Services, action: str, draft_id: int
) -> None:
    choices: list[tuple[str, str, dict[str, Any]]] = []
    if action == "draft_choose_kind":
        choices = [
            (
                value.title(),
                "draft_set",
                {"id": draft_id, "field": "kind", "value": value.value},
            )
            for value in CardKind
        ]
    elif action == "draft_choose_stage":
        choices = [
            (stage.title(), "draft_set", {"id": draft_id, "field": "stage", "value": stage.value})
            for stage in [CardStage.BACKLOG, CardStage.SPRINT, CardStage.TODAY]
        ]
    elif action == "draft_choose_priority":
        choices = [
            (
                value.title(),
                "draft_set",
                {"id": draft_id, "field": "priority", "value": value.value},
            )
            for value in Priority
        ]
    elif action == "draft_choose_effort":
        choices = [
            (str(value), "draft_set", {"id": draft_id, "field": "effort_points", "value": value})
            for value in [1, 2, 3, 5, 8, 13]
        ]
    elif action == "draft_choose_categories":
        choices = [
            (value.title(), "draft_toggle_category", {"id": draft_id, "value": value.value})
            for value in Category
        ]
    elif action == "draft_choose_energy":
        choices = [
            (value.title(), "draft_toggle_energy", {"id": draft_id, "value": value.value})
            for value in EnergyType
        ]
    elif action == "draft_choose_parent":
        async with services.sessions() as session:
            cards = list(
                await session.scalars(
                    select(Card)
                    .where(
                        Card.kind != CardKind.ACTION.value,
                        Card.archived_at.is_(None),
                    )
                    .limit(20)
                )
            )
        choices = [("Root", "draft_set_root", {"id": draft_id})] + [
            (card.title, "draft_set_parent", {"id": draft_id, "parent_id": card.id})
            for card in cards
        ]
    elif action == "draft_choose_values":
        async with services.sessions() as session:
            values = list(
                await session.scalars(
                    select(Value).where(Value.archived_at.is_(None)).order_by(Value.name).limit(30)
                )
            )
        choices = [
            (value.name, "draft_toggle_value", {"id": draft_id, "value_id": value.id})
            for value in values
        ]
    elif action == "draft_choose_tags":
        async with services.sessions() as session:
            tags = list(
                await session.scalars(
                    select(Tag).where(Tag.archived_at.is_(None)).order_by(Tag.name).limit(30)
                )
            )
        choices = [
            (tag.name, "draft_toggle_tag", {"id": draft_id, "tag_id": tag.id}) for tag in tags
        ]
    elif action == "draft_choose_blockers":
        async with services.sessions() as session:
            cards = list(
                await session.scalars(
                    select(Card)
                    .where(
                        Card.archived_at.is_(None),
                        Card.effective_stage.notin_(
                            [CardStage.DONE.value, CardStage.CANCELLED.value]
                        ),
                    )
                    .order_by(Card.title)
                    .limit(30)
                )
            )
        choices = [
            (card.title, "draft_toggle_blocker", {"id": draft_id, "card_id": card.id})
            for card in cards
        ]
    if not choices:
        await choice_screen(
            message,
            services,
            "No available choices.",
            [],
            back=("↩️ Back", "draft_view", {"id": draft_id}),
        )
        return
    await choice_screen(
        message,
        services,
        "Choose",
        choices,
        back=("↩️ Back", "draft_view", {"id": draft_id}),
    )


async def render_card_choices(
    message: Message, services: Services, action: str, card_id: int
) -> None:
    """Render relationship selectors for an already committed Card.

    These selectors deliberately mirror their draft counterparts while routing every
    mutation through the domain layer.  They are kept outside the persona dialogue.
    """
    choices: list[tuple[str, str, dict[str, Any]]] = []
    async with services.sessions() as session:
        card = await session.get(Card, card_id)
        if card is None or card.archived_at is not None:
            raise DomainError("Card does not exist or is archived")
        if action == "card_choose_stage":
            choices = [
                (
                    f"{'✓ ' if card.effective_stage == stage.value else ''}{stage.value.title()}",
                    "card_move",
                    {"id": card.id, "stage": stage.value},
                )
                for stage in (CardStage.BACKLOG, CardStage.SPRINT, CardStage.TODAY)
            ]
            title = "Choose Stage"
        elif action == "card_choose_priority":
            choices = [
                (
                    f"{'✓ ' if card.priority == priority.value else ''}{priority.value.title()}",
                    "card_set_field",
                    {"id": card.id, "field": "priority", "value": priority.value},
                )
                for priority in Priority
            ]
            title = "Choose Priority"
        elif action == "card_choose_effort":
            choices = [
                (
                    f"{'✓ ' if card.effort_points == effort else ''}{effort} EP",
                    "card_set_field",
                    {"id": card.id, "field": "effort_points", "value": effort},
                )
                for effort in (1, 2, 3, 5, 8, 13)
            ]
            title = "Choose Effort"
        elif action == "card_choose_categories":
            selected = set(
                await session.scalars(
                    select(CardCategory.category).where(CardCategory.card_id == card.id)
                )
            )
            choices = [
                (
                    f"{'✓ ' if category.value in selected else ''}{category.value.title()}",
                    "card_toggle_category",
                    {"id": card.id, "value": category.value},
                )
                for category in Category
            ]
            title = "Categories"
        elif action == "card_choose_energy":
            selected = set(
                await session.scalars(
                    select(CardEnergyType.energy_type).where(CardEnergyType.card_id == card.id)
                )
            )
            choices = [
                (
                    f"{'✓ ' if energy.value in selected else ''}{energy.value.title()}",
                    "card_toggle_energy",
                    {"id": card.id, "value": energy.value},
                )
                for energy in EnergyType
            ]
            title = "Energy"
        elif action == "card_choose_parent":
            candidates = list(
                await session.scalars(
                    select(Card)
                    .where(
                        Card.id != card.id,
                        Card.kind != CardKind.ACTION.value,
                        Card.archived_at.is_(None),
                    )
                    .order_by(Card.title)
                    .limit(30)
                )
            )
            root_label = "✓ Root" if card.parent_id is None else "Root"
            choices = [(root_label, "card_set_parent", {"id": card.id, "parent_id": None})]
            choices.extend(
                (
                    f"{'✓ ' if candidate.id == card.parent_id else ''}{candidate.title}",
                    "card_set_parent",
                    {"id": card.id, "parent_id": candidate.id},
                )
                for candidate in candidates
            )
            title = "Choose parent"
        elif action == "card_choose_values":
            selected_ids = set(
                await session.scalars(
                    select(CardValue.value_id).where(CardValue.card_id == card.id)
                )
            )
            values = list(
                await session.scalars(
                    select(Value).where(Value.archived_at.is_(None)).order_by(Value.name).limit(30)
                )
            )
            choices = [
                (
                    f"{'✓ ' if value.id in selected_ids else ''}{value.name}",
                    "card_toggle_value",
                    {"id": card.id, "value_id": value.id},
                )
                for value in values
            ]
            title = "Direct Values"
        elif action == "card_choose_tags":
            selected_ids = set(
                await session.scalars(select(CardTag.tag_id).where(CardTag.card_id == card.id))
            )
            tags = list(
                await session.scalars(
                    select(Tag).where(Tag.archived_at.is_(None)).order_by(Tag.name).limit(30)
                )
            )
            choices = [
                (
                    f"{'✅ ' if tag.id in selected_ids else ''}{tag.name}",
                    "card_toggle_tag",
                    {"id": card.id, "tag_id": tag.id},
                )
                for tag in tags
            ]
            title = "Tags"
        elif action == "card_choose_blockers":
            selected_ids = set(
                await session.scalars(
                    select(CardDependency.blocker_card_id).where(
                        CardDependency.blocked_card_id == card.id
                    )
                )
            )
            candidates = list(
                await session.scalars(
                    select(Card)
                    .where(Card.id != card.id, Card.archived_at.is_(None))
                    .order_by(Card.title)
                    .limit(30)
                )
            )
            choices = [
                (
                    f"{'✓ ' if candidate.id in selected_ids else ''}{candidate.title}",
                    "card_toggle_blocker",
                    {"id": card.id, "card_id": candidate.id},
                )
                for candidate in candidates
            ]
            title = "Blockers"
        else:
            raise DomainError("Unknown Card relationship selector")
        await session.commit()
    await choice_screen(
        message,
        services,
        title,
        choices,
        back=("↩️ Back", "card_view", {"id": card_id}),
    )


def _card_overview_text(state: dict[str, Any], *, heading: str = "Card") -> str:
    kind = str(state.get("kind") or "")
    lines = [
        f"Kind: {html.escape(kind.title())}",
        f"Title: <b>{html.escape(str(state.get('title') or '—'))}</b>",
        f"Parent: {html.escape(str(state.get('parent_name') or 'Root'))}",
        f"Stage: {html.escape(str(state.get('stage') or 'backlog').title())}",
        f"Note: {html.escape(str(state.get('note') or '—'))}",
        f"Priority: {html.escape(str(state.get('priority') or 'medium').title())} · Hard Time: "
        f"{'Yes' if state.get('hard_time') else 'No'}",
    ]
    if kind == CardKind.ACTION.value:
        lines.extend(
            [
                f"Effort: {state.get('effort_points') or '—'}",
                f"Repeatable: {'Yes' if state.get('repeatable') else 'No'}",
                f"Categories: {html.escape(', '.join(state.get('categories', [])) or '—')}",
                f"Energy: {html.escape(', '.join(state.get('energy_types', [])) or '—')}",
            ]
        )
    lines.extend(
        [
            f"Values: {html.escape(', '.join(state.get('value_names', [])) or '—')}",
            f"Tags: {html.escape(', '.join(state.get('tag_names', [])) or '—')}",
            f"Blockers: {html.escape(', '.join(state.get('blocker_names', [])) or '—')}",
        ]
    )
    return f"<b>{html.escape(heading)}</b>\n" + "\n".join(lines)


async def render_card(
    message: Message,
    services: Services,
    card_id: int,
    *,
    replace_message_id: int | None = None,
    back: dict[str, Any] | None = None,
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
            return
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
        blocker_ids = list(
            await session.scalars(
                select(CardDependency.blocker_card_id).where(
                    CardDependency.blocked_card_id == card.id
                )
            )
        )
        blockers = (
            list(await session.scalars(select(Card).where(Card.id.in_(blocker_ids))))
            if blocker_ids
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
            ("🌳 Parent", "card_choose_parent", {"id": card.id}),
            ("📍 Stage", "card_choose_stage", {"id": card.id}),
            ("⚠️ Priority", "card_choose_priority", {"id": card.id}),
            ("⏱ Hard Time", "card_toggle_field", {"id": card.id, "field": "hard_time"}),
        ]
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
                ("🚧 Blockers", "card_choose_blockers", {"id": card.id}),
            ]
        )
        buttons = [await token_button(session, services.owner_id, *spec) for spec in field_specs]
        rows = [buttons[index : index + 2] for index in range(0, len(buttons), 2)]
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
        await session.commit()
    text = _card_overview_text(
        {
            "kind": card.kind,
            "title": card.title,
            "parent_name": parent.title if parent else "Root",
            "stage": card.effective_stage,
            "note": card.note,
            "priority": card.priority,
            "hard_time": card.hard_time,
            "effort_points": card.effort_points,
            "repeatable": card.repeatable,
            "categories": categories,
            "energy_types": energy_types,
            "value_names": [value.name for value in direct_values],
            "tag_names": [tag.name for tag in direct_tags],
            "blocker_names": [blocker.title for blocker in blockers],
        }
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


async def render_proposal_edits(message: Message, services: Services, proposal_id: int) -> None:
    """Allow the owner to remove individual proposed changes before approval."""
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
        rows = [
            [
                await token_button(
                    session,
                    services.owner_id,
                    f"Remove {change.position + 1}: {change.action} {change.entity}"[:60],
                    "proposal_remove_change",
                    {"id": proposal.id, "change_id": change.id},
                )
            ]
            for change in changes
        ]
        rows.append(
            [
                await token_button(
                    session, services.owner_id, "↩️ Back", "proposal_view", {"id": proposal.id}
                )
            ]
        )
        await session.commit()
    await send_registered(
        message,
        services,
        "<b>Edit proposal</b>\nRemove any change you do not want, then return to approve the remainder.",
        kind=MessageKind.APPROVAL,
        markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


async def _proposal_item_state(
    session: AsyncSession, change: ProposalChange
) -> tuple[dict[str, Any], dict[str, Any]]:
    current: dict[str, Any] = {}
    if change.entity == "card" and change.entity_id:
        card = await session.get(Card, change.entity_id)
        if card is not None:
            current = {
                "kind": card.kind,
                "title": card.title,
                "note": card.note,
                "stage": card.effective_stage,
                "priority": card.priority,
                "hard_time": card.hard_time,
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
                "blocker_ids": sorted(
                    await session.scalars(
                        select(CardDependency.blocker_card_id).where(
                            CardDependency.blocked_card_id == card.id
                        )
                    )
                ),
            }
    elif change.entity == "tag" and change.entity_id:
        tag = await session.get(Tag, change.entity_id)
        if tag is not None:
            current = {"name": tag.name, "description": tag.description}
    elif change.entity == "value" and change.entity_id:
        value = await session.get(Value, change.entity_id)
        if value is not None:
            current = {
                "name": value.name,
                "description": value.description,
                "active": value.active,
            }
    proposed = {**current, **dict(change.values)}
    if change.entity in {"tag", "value"} and change.action in {"archive", "delete"}:
        current["status"] = "Active"
        proposed["status"] = "Archived" if change.action == "archive" else "Deleted"
    if change.entity == "card":
        if change.action == "complete":
            proposed["stage"] = CardStage.DONE.value
        elif change.action == "cancel":
            proposed["stage"] = CardStage.CANCELLED.value
        elif change.action == "reopen":
            proposed["stage"] = change.values.get("stage", CardStage.BACKLOG.value)
        elif change.action in {"archive", "delete"}:
            current["status"] = "Active"
            proposed["status"] = "Archived" if change.action == "archive" else "Deleted"

        unresolved_references: list[tuple[str, str]] = []
        relation_specs = (
            ("value_ids", "value_id", "value_query", Value),
            ("tag_ids", "tag_id", "tag_query", Tag),
        )
        for plural_key, singular_key, query_key, model in relation_specs:
            if not ({plural_key, singular_key, query_key} & change.values.keys()):
                continue
            target_ids = {
                int(item)
                for item in (
                    ([change.values[singular_key]] if change.values.get(singular_key) else [])
                    + list(change.values.get(plural_key) or [])
                )
            }
            queries = change.values.get(query_key) or []
            if isinstance(queries, str):
                queries = [queries]
            for name in queries:
                match = await session.scalar(
                    select(model.id).where(
                        model.name.collate("NOCASE") == str(name),
                        model.archived_at.is_(None),
                    )
                )
                if match is not None:
                    target_ids.add(match)
                else:
                    unresolved_references.append((query_key, str(name)))
            if change.action == "link":
                proposed[plural_key] = sorted(set(current.get(plural_key, [])) | target_ids)
            elif change.action == "unlink":
                proposed[plural_key] = sorted(set(current.get(plural_key, [])) - target_ids)
            else:
                proposed[plural_key] = sorted(target_ids)
        if unresolved_references:
            proposed["_unresolved_references"] = unresolved_references
        if {"blocker_id", "blocker_ids"} & change.values.keys():
            target_ids = {
                int(item)
                for item in (
                    ([change.values["blocker_id"]] if change.values.get("blocker_id") else [])
                    + list(change.values.get("blocker_ids") or [])
                )
            }
            if change.action == "link":
                proposed["blocker_ids"] = sorted(set(current.get("blocker_ids", [])) | target_ids)
            elif change.action == "unlink":
                proposed["blocker_ids"] = sorted(set(current.get("blocker_ids", [])) - target_ids)
            else:
                proposed["blocker_ids"] = sorted(target_ids)
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
    display["parent_name"] = parent.title if parent else "Root"
    for ids_key, names_key, model, name_field in (
        ("value_ids", "value_names", Value, "name"),
        ("tag_ids", "tag_names", Tag, "name"),
        ("blocker_ids", "blocker_names", Card, "title"),
    ):
        ids = list(state.get(ids_key) or [])
        entities = (
            list(await session.scalars(select(model).where(model.id.in_(ids)))) if ids else []
        )
        by_id = {entity.id: getattr(entity, name_field) for entity in entities}
        display[names_key] = [by_id[item_id] for item_id in ids if item_id in by_id]
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
        "blocker_ids": (Card, "title"),
    }
    if field in relation_specs:
        model, name_field = relation_specs[field]
        ids = list(value or [])
        entities = (
            list(await session.scalars(select(model).where(model.id.in_(ids)))) if ids else []
        )
        by_id = {entity.id: getattr(entity, name_field) for entity in entities}
        return ", ".join(by_id[item_id] for item_id in ids if item_id in by_id) or "—"
    if field in {"categories", "energy_types"}:
        return ", ".join(str(item).title() for item in (value or [])) or "—"
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
        "effort_points": "Effort",
        "repeatable": "Repeatable",
        "categories": "Categories",
        "energy_types": "Energy",
        "value_ids": "Values",
        "tag_ids": "Tags",
        "blocker_ids": "Blockers",
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
            if change.entity == "card":
                diffs = await _proposal_card_diffs(session, current, proposed)
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
    if outcome.kind in {"answer", "clarification"}:
        await send_registered(
            message,
            services,
            html.escape(outcome.message),
            kind=MessageKind.DIALOGUE_ASSISTANT,
        )
        return
    if outcome.draft_bundle_ids:
        bundle_id = outcome.draft_bundle_ids[0]
        async with services.sessions() as session:
            bundle = await session.get(CardDraftBundle, bundle_id)
            draft_id = bundle.active_draft_id if bundle is not None else None
        if draft_id is None:
            raise DomainError("The queued Card draft is no longer available")
        await render_draft(message, services, draft_id)
        return
    if outcome.proposal_id:
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
    if ui_kind == "draft_text":
        async with services.sessions() as session:
            await DraftService(session).update(
                ui_state["draft_id"], **{ui_state["field"]: message.text.strip()}
            )
            await session.execute(delete(UiSession).where(UiSession.owner_id == services.owner_id))
            await session.commit()
        input_deleted = await delete_text_input(message, services)
        if not input_deleted:
            await clear_message_markup(message, int(ui_state["message_id"]))
        await render_draft(
            message,
            services,
            ui_state["draft_id"],
            replace_message_id=int(ui_state["message_id"]) if input_deleted else None,
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
