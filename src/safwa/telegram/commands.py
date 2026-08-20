from __future__ import annotations

import html
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import time
from typing import Any

from aiogram import Bot, F
from aiogram.enums import ChatAction
from aiogram.filters import Command
from aiogram.types import (
    BotCommand,
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import delete, func, select

from ..analytics import render_retrospective_png, retrospective_data, retrospective_recommendations
from ..constants import SPRINT_LENGTH_MAX_DAYS, SPRINT_LENGTH_MIN_DAYS
from ..continuity import MemoryMaintenanceResult, record_memory_run
from ..domain import (
    DomainError,
)
from ..enums import CardStage, MessageKind
from ..history import mark_message, register_message
from ..models import (
    Card,
    FeedbackQueue,
    SavedRequest,
    Sprint,
    Tag,
    UiSession,
    UserProfile,
    Value,
    Workspace,
)
from ..reminders import parse_clock_or_off
from ._core import BACKGROUND_SOURCE_ID, Services, router, sprint_is_active
from ._messaging import (
    dismiss_prior_ui,
    edit_registered_message,
    materialize_queued_dialogue,
    send_registered,
    send_summary,
    token_button,
)
from ._presentation import (
    menu_markup,
    menu_row,
    retro_back_row,
    start_payload,
    with_notice,
)
from .cards import render_dashboard, start_manual_card_creation
from .plan import handle_plan_start, is_plan_link
from .reminders import render_reminders
from .screens import open_citation
from .sprint import render_sprint, render_today
from .text_input import TextInputScreen, render_text_input

logger = logging.getLogger(__name__)

# Published to Telegram by `sync_bot_commands`, which drops Today outside a Sprint.
BOT_COMMANDS = [
    BotCommand(command="start", description="Open Safwa"),
    BotCommand(command="today", description="Today dashboard"),
    BotCommand(command="sprint", description="Planning or Sprint"),
    BotCommand(command="backlog", description="Backlog dashboard"),
    BotCommand(command="values", description="Values in focus"),
    BotCommand(command="tags", description="Manage Tags"),
    BotCommand(command="requests", description="Saved AI Requests"),
    BotCommand(command="retro", description="Latest retrospective"),
    BotCommand(command="feedback", description="Pending completion feedback"),
    BotCommand(command="reminders", description="Your Reminders"),
    BotCommand(command="settings", description="Profile and reminders"),
    BotCommand(command="syncmem", description="Sync Telegram dialogue into memory"),
    BotCommand(command="mem", description="Add a durable memory fact"),
    BotCommand(command="memory", description="Inspect memory.md"),
    BotCommand(command="summarize", description="Summarize the dialogue now"),
    BotCommand(command="status", description="Safwa diagnostics"),
    BotCommand(command="cancel", description="Cancel generation"),
]


@router.message.middleware()
async def dismiss_screens_before_a_command(
    handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
    event: Message,
    data: dict[str, Any],
) -> Any:
    """A command is the owner leaving whatever screen was open, so it answers none of them.

    Registered once here rather than called from twenty handlers.  Ordinary text dismisses
    from `dialogue.ordinary_text` instead, because typed field input must reach its live
    editor untouched.
    """
    if (event.text or "").lstrip().startswith("/") and not is_plan_link(event.text):
        await dismiss_prior_ui(event, data["services"])
    return await handler(event, data)


@router.message(Command("start"))
async def command_start(message: Message, services: Services) -> None:
    payload = start_payload(message.text)
    if payload is not None:
        if not await handle_plan_start(message, services, payload):
            await open_citation(message, services, payload)
        return
    async with services.sessions() as session:
        sprint_active = await sprint_is_active(session)
    await send_registered(
        message,
        services,
        "<b>Safwa</b>\nYour personal agile advisor. Choose a dashboard or just write to me.",
        kind=MessageKind.DASHBOARD,
        markup=menu_markup(sprint_active=sprint_active),
    )


@router.message(Command("summarize"))
async def command_summarize(message: Message, services: Services) -> None:
    """Cut the context deliberately: post a Summary now instead of waiting for the budget."""
    if not services.guard.reserve_background():
        await send_registered(
            message,
            services,
            "Wait for the current advisor response, then retry /summarize.",
            kind=MessageKind.ERROR,
        )
        return
    revision = services.guard.dialogue_revision
    try:
        await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
        written = await services.continuity.maybe_summarize(
            message.chat.id,
            lambda text, covered_id: send_summary(message, services, text, covered_id),
            force=True,
            still_current=lambda: (
                services.guard.background and services.guard.dialogue_revision == revision
            ),
        )
    finally:
        services.guard.release(BACKGROUND_SOURCE_ID)
    if not written:
        await send_registered(
            message,
            services,
            "There is no new dialogue to summarize.",
            kind=MessageKind.RECEIPT,
        )


@router.message(Command("today"))
async def command_today(message: Message, services: Services) -> None:
    await render_today(message, services)


@router.message(Command("backlog"))
async def command_backlog(message: Message, services: Services) -> None:
    await render_dashboard(message, services, CardStage.BACKLOG, title="Backlog")


@router.message(Command("sprint"))
async def command_sprint(message: Message, services: Services) -> None:
    await render_sprint(message, services)


async def command_add(message: Message, services: Services) -> None:
    await start_manual_card_creation(message, services)


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
    if not services.guard.reserve_background():
        await send_registered(
            message,
            services,
            "Wait for the current advisor response, then retry /syncmem.",
            kind=MessageKind.ERROR,
        )
        return
    revision = services.guard.dialogue_revision
    lease_current = False
    try:
        await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
        result = await services.continuity.maintain_memory(
            message.chat.id,
            still_current=lambda: (
                services.guard.background and services.guard.dialogue_revision == revision
            ),
        )
        lease_current = services.guard.background and services.guard.dialogue_revision == revision
    finally:
        services.guard.release(BACKGROUND_SOURCE_ID)
    if not lease_current:
        return
    if result == MemoryMaintenanceResult.UPDATED:
        await record_memory_run(services.sessions)
        text, kind = "Memory synchronized from Telegram dialogue.", MessageKind.RECEIPT
    elif result == MemoryMaintenanceResult.CURRENT:
        await record_memory_run(services.sessions)
        text, kind = "Memory is already synchronized.", MessageKind.RECEIPT
    elif result == MemoryMaintenanceResult.BUSY:
        text, kind = "Memory synchronization is already running.", MessageKind.ERROR
    else:
        text, kind = "memory.md needs attention; synchronization was not run.", MessageKind.ERROR
    await send_registered(message, services, text, kind=kind)


@router.message(Command("mem"))
async def command_remember(message: Message, services: Services) -> None:
    fact = (message.text or "").partition(" ")[2].strip()
    if not fact:
        await send_registered(
            message, services, "Usage: /mem one durable fact", kind=MessageKind.ERROR
        )
        return
    await services.memory.append_manual(fact)
    await send_registered(message, services, "Remembered in memory.md.", kind=MessageKind.RECEIPT)


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
    caption, event_id = mark_message(
        f"Sprint {sprint.number} retrospective\n"
        + "\n".join(retrospective_recommendations(data)),
        MessageKind.RETROSPECTIVE_PNG,
    )
    sent = await message.answer_photo(
        BufferedInputFile(png, filename=f"sprint-{sprint.number}-retro.png"),
        caption=caption,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[retro_back_row()]),
    )
    async with services.sessions() as session:
        await register_message(
            session,
            sent.chat.id,
            sent.message_id,
            "out",
            MessageKind.RETROSPECTIVE_PNG,
            sprint.id,
            event_id,
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
                with_notice("No completion feedback pending.", notice),
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
        with_notice(
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


@router.message(Command("reminders"))
async def command_reminders(message: Message, services: Services) -> None:
    """Show the triggers the owner set; creation and timing stay advisor-only."""
    await render_reminders(message, services)


@dataclass(frozen=True, slots=True)
class SettingsField:
    """One `user_profile` value the Settings screen edits through a button and a prompt.

    `parse` raises `ValueError` carrying the sentence the retry prompt shows, so a bad
    answer is answered by the same screen rather than an error message somewhere else.
    """

    title: str
    label: str
    instruction: str
    parse: Callable[[str], Any]
    show: Callable[[Any], str]


def _parse_sprint_length(raw: str) -> int:
    if not raw.isdigit() or not SPRINT_LENGTH_MIN_DAYS <= int(raw) <= SPRINT_LENGTH_MAX_DAYS:
        raise ValueError(
            f"Send a whole number between {SPRINT_LENGTH_MIN_DAYS} and {SPRINT_LENGTH_MAX_DAYS}."
        )
    return int(raw)


def _parse_capacity(raw: str) -> int | None:
    if raw.lower() == "off":
        return None
    if not raw.isdigit() or int(raw) <= 0:
        raise ValueError("Send a positive number of effort points, or off.")
    return int(raw)


def _parse_daily_time(raw: str) -> time | None:
    try:
        return parse_clock_or_off(raw)
    except ValueError:
        raise ValueError("Send a time as HH:MM, for example 22:00, or off.") from None


def _clock(value: time | None) -> str:
    return value.strftime("%H:%M") if value else "off"


# Rendered in this order, both as lines on the Settings screen and as its buttons.
SETTINGS_FIELDS: dict[str, SettingsField] = {
    "about_me": SettingsField(
        title="About me",
        label="👤 About me",
        instruction="Send what Safwa should know about you. Send off to clear it.",
        parse=lambda raw: "" if raw.lower() == "off" else raw,
        show=lambda value: value or "off",
    ),
    "advisor_instructions": SettingsField(
        title="Advisor instructions",
        label="🧭 Advisor instructions",
        instruction="Send standing instructions for Safwa. Send off to clear them.",
        parse=lambda raw: "" if raw.lower() == "off" else raw,
        show=lambda value: value or "off",
    ),
    "sprint_length_days": SettingsField(
        title="Sprint length",
        label="🏁 Sprint length",
        instruction=(
            f"Send a number of days between {SPRINT_LENGTH_MIN_DAYS} and "
            f"{SPRINT_LENGTH_MAX_DAYS}. It applies to the next Sprint you start."
        ),
        parse=_parse_sprint_length,
        show=lambda value: f"{value} days",
    ),
    "capacity_effort_points": SettingsField(
        title="Sprint capacity",
        label="🎯 Sprint capacity",
        instruction="Send the effort points one Sprint holds, or off to stop tracking it.",
        parse=_parse_capacity,
        show=lambda value: f"{value} EP" if value else "off",
    ),
    "memory_update_time": SettingsField(
        title="Memory sync",
        label="🧠 Memory sync",
        instruction=(
            "Send the local time the dialogue is folded into memory.md, as HH:MM, or off."
        ),
        parse=_parse_daily_time,
        show=_clock,
    ),
    "diary_time": SettingsField(
        title="Diary",
        label="📔 Diary time",
        instruction=(
            "Send the local time Safwa writes up your day, as HH:MM, or off to stop asking."
        ),
        parse=_parse_daily_time,
        show=_clock,
    ),
    "diary_instructions": SettingsField(
        title="Diary instruction",
        label="✍️ Diary instruction",
        instruction=(
            "Send a standing instruction for the Diary — what to always notice, or how to "
            "write it. Send off to drop it."
        ),
        parse=lambda raw: "" if raw.lower() == "off" else raw,
        show=lambda value: value or "off",
    ),
}


@router.message(Command("settings"))
async def command_settings(
    message: Message,
    services: Services,
    *,
    notice: str | None = None,
    replace_message_id: int | None = None,
) -> None:
    async with services.sessions() as session:
        profile = await session.get(UserProfile, 1)
        workspace = await session.get(Workspace, 1)
        if profile is None or workspace is None:
            raise DomainError("Workspace is not initialized")
        lines = [
            "<b>Settings</b>",
            f"About me: {html.escape(profile.about_me or '—')}",
            f"Advisor instructions: {html.escape(profile.advisor_instructions or '—')}",
        ]
        buttons = []
        for name, field in SETTINGS_FIELDS.items():
            lines.append(
                f"{field.title}: {html.escape(field.show(getattr(profile, name)))}"
            )
            buttons.append(
                await token_button(
                    session, services.owner_id, field.label, "settings_edit", {"field": name}
                )
            )
        lines.append(f"Timezone: {html.escape(workspace.timezone)}")
        lines.append("Tap a setting to change it.")
        await session.commit()
    rows = [buttons[index : index + 2] for index in range(0, len(buttons), 2)]
    text = with_notice("\n".join(lines), notice)
    markup = InlineKeyboardMarkup(inline_keyboard=[*rows, menu_row()])
    if replace_message_id is not None:
        await edit_registered_message(
            message,
            services,
            replace_message_id,
            text,
            kind=MessageKind.DASHBOARD,
            markup=markup,
        )
    else:
        await send_registered(message, services, text, kind=MessageKind.DASHBOARD, markup=markup)


async def render_settings_field_prompt(
    message: Message, services: Services, field_name: str, *, notice: str | None = None
) -> None:
    field = SETTINGS_FIELDS[field_name]
    async with services.sessions() as session:
        profile = await session.get(UserProfile, 1)
        if profile is None:
            raise DomainError("Workspace is not initialized")
        current = field.show(getattr(profile, field_name))
    await render_text_input(
        message,
        services,
        screen=TextInputScreen(
            title=field.title,
            current_value=current,
            instruction=field.instruction,
            back_action="settings_back",
            back_payload={},
        ),
        state={"flow": "settings", "field": field_name},
        notice=notice,
    )


async def sync_bot_commands(bot: Bot, *, sprint_active: bool) -> None:
    """Publish the command list. Today is dropped while the workspace is in Planning."""
    commands = [
        command
        for command in BOT_COMMANDS
        if sprint_active or command.command != "today"
    ]
    await bot.set_my_commands(commands)


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
    await materialize_queued_dialogue(message, services)
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
    # Walking into the menu is an answer too: whatever else was open is refused.
    await dismiss_prior_ui(callback.message, services)
    handlers = {
        "home": command_start,
        "today": command_today,
        "sprint": command_sprint,
        "backlog": command_backlog,
        "add": command_add,
        "values": command_values,
        "tags": command_tags,
        "requests": command_requests,
        "reminders": command_reminders,
        "retro": command_retro,
        "settings": command_settings,
    }
    handler = handlers.get(action)
    if handler is None:
        logger.warning("Unknown nav action: %s", action)
        await callback.answer("This action is no longer available.", show_alert=True)
        return
    if action != "add":
        async with services.sessions() as session:
            await session.execute(delete(UiSession).where(UiSession.owner_id == services.owner_id))
            await session.commit()
    await handler(callback.message, services)
