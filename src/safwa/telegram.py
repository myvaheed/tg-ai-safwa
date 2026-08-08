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

from .ai.service import AIAdvisor, ProposalService
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
    toggle_card_dependency,
    toggle_card_tag,
    toggle_card_value,
    update_profile,
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
    CardDependency,
    CardDraft,
    CardDraftBundle,
    CardTag,
    CardValue,
    ChangeProposal,
    FeedbackQueue,
    ProposalChange,
    SavedRequest,
    Sprint,
    SummaryState,
    Tag,
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
        if isinstance(event, Message) and services.guard.active:
            command = (event.text or "").lstrip().split(maxsplit=1)[0].split("@", 1)[0].casefold()
            if command == "/cancel":
                return await handler(event, data)
            if event.message_id != services.guard.active_source_id:
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
                        session, services.owner_id, label[:60], "card_view", {"id": card.id}
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
    if len(bundle_drafts) == 1 and not draft.validation_errors:
        rows.append(
            [
                await token_button(
                    session, services.owner_id, "✅ Create", "draft_commit", {"id": draft.id}
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
                        f"✅ Create all {len(bundle_drafts)}",
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
    rows.append(menu_row())
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def render_draft(message: Message, services: Services, draft_id: int) -> None:
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
                    "value_toggle",
                    {"id": value.id},
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
            [await token_button(session, services.owner_id, tag.name, "tag_view", {"id": tag.id})]
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
                    {"id": card.id},
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
            async with services.sessions() as session:
                await session.execute(
                    delete(UiSession).where(UiSession.owner_id == services.owner_id)
                )
                session.add(
                    UiSession(
                        owner_id=services.owner_id,
                        kind=f"{entity}_create",
                        state={},
                        expires_at=datetime.now(UTC) + timedelta(minutes=30),
                    )
                )
                cancel = await token_button(
                    session,
                    services.owner_id,
                    "Cancel",
                    "ui_create_cancel",
                    {"entity": entity},
                )
                await session.commit()
            await send_registered(
                callback.message,
                services,
                f"Send the new {entity.title()} name.",
                kind=MessageKind.DASHBOARD,
                markup=InlineKeyboardMarkup(inline_keyboard=[[cancel]]),
                replace=False,
            )
            return
        if action == "ui_create_cancel":
            async with services.sessions() as session:
                await session.execute(
                    delete(UiSession).where(UiSession.owner_id == services.owner_id)
                )
                await session.commit()
            if payload.get("entity") == "value":
                await command_values(callback.message, services)
            else:
                await command_tags(callback.message, services)
            return
        if action == "tag_view":
            async with services.sessions() as session:
                tag = await session.get(Tag, payload["id"])
                if tag is None or tag.archived_at is not None:
                    raise DomainError("Tag does not exist")
                await session.commit()
            await send_registered(
                callback.message,
                services,
                f"<b>{html.escape(tag.name)}</b>\n{html.escape(tag.description or 'No description.')}",
                kind=MessageKind.DASHBOARD,
                markup=InlineKeyboardMarkup(inline_keyboard=[menu_row()]),
            )
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
                session.add(
                    UiSession(
                        owner_id=services.owner_id,
                        kind="draft_text",
                        state={"draft_id": payload["id"], "field": payload["field"]},
                        expires_at=datetime.now(UTC) + timedelta(minutes=30),
                    )
                )
                await session.commit()
            await send_registered(
                callback.message,
                services,
                f"Send the new {payload['field']}.",
                kind=MessageKind.DRAFT_REVIEW,
                replace=False,
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
                await session.commit()
            await send_registered(
                callback.message,
                services,
                f"✅ Created <b>{html.escape(cards[0].title)}</b>.",
                kind=MessageKind.RECEIPT,
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
            await send_registered(
                callback.message,
                services,
                f"✅ Created {len(cards)} reviewed cards.",
                kind=MessageKind.RECEIPT,
            )
        elif action == "draft_discard":
            async with services.sessions() as session:
                await DraftService(session).discard(payload["id"])
                await session.commit()
            await command_start(callback.message, services)
        elif action == "card_view":
            await render_card(callback.message, services, payload["id"])
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
                await session.execute(
                    delete(UiSession).where(UiSession.owner_id == services.owner_id)
                )
                session.add(
                    UiSession(
                        owner_id=services.owner_id,
                        kind="card_text",
                        state={"card_id": payload["id"], "field": payload["field"]},
                        expires_at=datetime.now(UTC) + timedelta(minutes=30),
                    )
                )
                await session.commit()
            await send_registered(
                callback.message,
                services,
                f"Send the new {payload['field']}.",
                kind=MessageKind.DASHBOARD,
                replace=False,
            )
        elif action == "card_choose_parent":
            await render_card_choices(callback.message, services, action, payload["id"])
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
            await send_registered(
                callback.message,
                services,
                f"Approved. Updated {len(affected)} item(s).",
                kind=MessageKind.RECEIPT,
            )
            await render_feedback(callback.message, services)
        elif action == "proposal_delete_confirm":
            async with services.sessions() as session:
                affected = await ProposalService(session).apply(
                    payload["id"], allow_destructive=True
                )
                await session.commit()
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
            await send_registered(
                callback.message, services, "Proposal cancelled.", kind=MessageKind.RECEIPT
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
        await send_registered(
            callback.message, services, html.escape(str(error)), kind=MessageKind.ERROR
        )
    except DomainError as error:
        await send_registered(
            callback.message, services, html.escape(str(error)), kind=MessageKind.ERROR
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
        if action == "card_choose_parent":
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


async def render_card(message: Message, services: Services, card_id: int) -> None:
    async with services.sessions() as session:
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
        rows = [
            [
                await token_button(
                    session,
                    services.owner_id,
                    "Backlog",
                    "card_move",
                    {"id": card.id, "stage": "backlog"},
                ),
                await token_button(
                    session,
                    services.owner_id,
                    "Sprint",
                    "card_move",
                    {"id": card.id, "stage": "sprint"},
                ),
                await token_button(
                    session,
                    services.owner_id,
                    "Today",
                    "card_move",
                    {"id": card.id, "stage": "today"},
                ),
            ]
        ]
        rows.append(
            [
                await token_button(
                    session,
                    services.owner_id,
                    "✏️ Title",
                    "card_edit_text",
                    {"id": card.id, "field": "title"},
                ),
                await token_button(
                    session,
                    services.owner_id,
                    "📝 Note",
                    "card_edit_text",
                    {"id": card.id, "field": "note"},
                ),
            ]
        )
        rows.append(
            [
                await token_button(
                    session,
                    services.owner_id,
                    "🌳 Parent",
                    "card_choose_parent",
                    {"id": card.id},
                ),
                await token_button(
                    session,
                    services.owner_id,
                    "💎 Values",
                    "card_choose_values",
                    {"id": card.id},
                ),
                await token_button(
                    session,
                    services.owner_id,
                    "🏷 Tags",
                    "card_choose_tags",
                    {"id": card.id},
                ),
            ]
        )
        rows.append(
            [
                await token_button(
                    session,
                    services.owner_id,
                    "🚧 Blockers",
                    "card_choose_blockers",
                    {"id": card.id},
                )
            ]
        )
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
        rows.append(menu_row())
        await session.commit()
    details = [
        f"{card.kind.title()} · {card.effective_stage.title()} · {card.effort_points or '—'} EP",
        f"Priority: {card.priority.title()}{' · Hard time' if card.hard_time else ''}",
        f"Parent: {parent.title if parent else 'Root'}",
    ]
    if card.repeatable:
        details.append("Repeatable")
    if direct_values:
        details.append("Values: " + ", ".join(value.name for value in direct_values))
    if direct_tags:
        details.append("Tags: " + ", ".join(tag.name for tag in direct_tags))
    if blockers:
        details.append("Blockers: " + ", ".join(blocker.title for blocker in blockers))
    if card.note:
        details.append(card.note)
    await send_registered(
        message,
        services,
        f"<b>{html.escape(card.title)}</b>\n"
        + "\n".join(html.escape(detail) for detail in details),
        kind=MessageKind.DASHBOARD,
        markup=InlineKeyboardMarkup(inline_keyboard=rows),
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


async def render_proposal(message: Message, services: Services, proposal_id: int) -> None:
    async with services.sessions() as session:
        proposal = await session.get(ChangeProposal, proposal_id)
        changes = list(
            await session.scalars(
                select(ProposalChange)
                .where(ProposalChange.proposal_id == proposal.id)
                .order_by(ProposalChange.position)
            )
        )
        approve = await token_button(
            session, services.owner_id, "✅ Approve", "proposal_approve", {"id": proposal.id}
        )
        edit = await token_button(
            session, services.owner_id, "✏️ Edit", "proposal_edit", {"id": proposal.id}
        )
        reject = await token_button(
            session, services.owner_id, "✖ Cancel", "proposal_reject", {"id": proposal.id}
        )
        await session.commit()
    await send_registered(
        message,
        services,
        "<b>Review proposed changes</b>\n"
        + html.escape(proposal.message)
        + "\n\n"
        + "\n".join(
            f"• {html.escape(change.action)} {html.escape(change.entity)} "
            f"{html.escape(change.entity_id or '')} {html.escape(str(change.values))}"
            for change in changes
        ),
        kind=MessageKind.APPROVAL,
        markup=InlineKeyboardMarkup(inline_keyboard=[[approve, edit, reject], menu_row()]),
        related_id=proposal_id,
    )


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
        if ui and ui.kind in {"value_create", "tag_create"}:
            name = message.text.strip()
            if ui.kind == "value_create":
                await create_value(session, name)
            else:
                await create_tag(session, name)
            await register_message(
                session, message.chat.id, message.message_id, "in", MessageKind.UI_INPUT
            )
            entity = ui.kind.removesuffix("_create")
            await session.delete(ui)
            await session.commit()
            if entity == "value":
                await command_values(message, services)
            else:
                await command_tags(message, services)
            return
        if ui and ui.kind == "draft_text":
            await DraftService(session).update(
                ui.state["draft_id"], **{ui.state["field"]: message.text.strip()}
            )
            await register_message(
                session, message.chat.id, message.message_id, "in", MessageKind.UI_INPUT
            )
            await session.delete(ui)
            await session.commit()
            await render_draft(message, services, ui.state["draft_id"])
            return
        if ui and ui.kind == "card_text":
            value = message.text.strip()
            card = await edit_card_text(session, ui.state["card_id"], ui.state["field"], value)
            await register_message(
                session, message.chat.id, message.message_id, "in", MessageKind.UI_INPUT
            )
            await session.delete(ui)
            await session.commit()
            await render_card(message, services, card.id)
            return
        await register_message(
            session, message.chat.id, message.message_id, "in", MessageKind.DIALOGUE_USER
        )
        await session.execute(
            update(ChangeProposal).where(ChangeProposal.status == "pending").values(status="stale")
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
        if outcome.kind in {"answer", "clarification"}:
            await send_registered(
                message, services, html.escape(outcome.message), kind=MessageKind.DIALOGUE_ASSISTANT
            )
        else:
            for bundle_id in outcome.draft_bundle_ids:
                async with services.sessions() as session:
                    bundle = await session.get(CardDraftBundle, bundle_id)
                    draft_id = bundle.active_draft_id
                if draft_id:
                    await render_draft(message, services, draft_id)
            if outcome.proposal_id:
                await render_proposal(message, services, outcome.proposal_id)
            if not outcome.draft_bundle_ids and not outcome.proposal_id:
                await send_registered(
                    message,
                    services,
                    html.escape(outcome.message),
                    kind=MessageKind.DIALOGUE_ASSISTANT,
                )

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
