from __future__ import annotations

import html
import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..domain import TAG_REFERENCE, VALUE_REFERENCE
from ..enums import CardKind, Category, EnergyType, MessageKind, Priority, ProposalStatus
from ..history import SUBSESSION_RESULT_HEADER, register_message
from ..models import (
    CallbackToken,
    Card,
    ChangeProposal,
    ProposalChange,
    TelegramMessage,
    UiSession,
)
from ._core import Services

logger = logging.getLogger(__name__)


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


_PAGE_SIZE = 5
# Value and Tag selectors grow with the workspace, so they page instead of truncating.
_SELECTOR_PAGE_SIZE = 10
_NAMED_CHOICE_FIELDS = frozenset({"values", "tags"})
_REQUEST_RESULT_LIMIT = 25
# Tag and Value share one field-oriented item screen; the spec supplies the differences.
_ITEM_REFERENCES = {"tag": TAG_REFERENCE, "value": VALUE_REFERENCE}


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
