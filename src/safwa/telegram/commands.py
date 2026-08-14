from __future__ import annotations

import html
import logging
from datetime import UTC, datetime, timedelta
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
from ..continuity import MemoryMaintenanceResult, parse_memory_update_time, record_memory_run
from ..domain import (
    DomainError,
    StaleStateError,
    snooze_reminders,
    update_profile,
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
from ._core import BACKGROUND_SOURCE_ID, Services, router, sprint_is_active
from ._messaging import (
    delete_message_range,
    materialize_queued_dialogue,
    send_registered,
    send_subsession_result,
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
from .reminders import render_reminders
from .screens import open_citation
from .sprint import render_sprint, render_today

logger = logging.getLogger(__name__)

SETTINGS_PROMPT_TTL = timedelta(minutes=30)

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
    BotCommand(command="setcapacity", description="Set Sprint capacity"),
    BotCommand(command="snooze", description="Snooze reminders (minutes)"),
    BotCommand(command="syncmem", description="Sync Telegram dialogue into memory"),
    BotCommand(command="mem", description="Add a durable memory fact"),
    BotCommand(command="setmemtime", description="Set daily memory sync time"),
    BotCommand(command="memory", description="Inspect memory.md"),
    BotCommand(command="status", description="Safwa diagnostics"),
    BotCommand(command="cancel", description="Cancel generation"),
]


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
        await send_subsession_result(message, services, active_start.text, result)
        # Keep the source branch until every result chunk is visible and registered.
        # A failed cleanup may leave a harmless duplicate; the opposite order could
        # permanently lose both the branch and its compressed result.
        await delete_message_range(message, start_message_id, message.message_id)
    finally:
        services.guard.release(message.message_id)


@router.message(Command("start"))
async def command_start(message: Message, services: Services) -> None:
    payload = start_payload(message.text)
    if payload is not None:
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


@router.message(Command("newsession"))
async def command_newsession(message: Message, services: Services) -> None:
    # The middleware cancels an in-flight answer before dispatching /newsession. Preserve
    # any text that was queued behind it and remove its temporary placeholders.
    await materialize_queued_dialogue(message, services)
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


@router.message(Command("settings"))
async def command_settings(
    message: Message, services: Services, *, notice: str | None = None
) -> None:
    async with services.sessions() as session:
        profile = await session.get(UserProfile, 1)
        workspace = await session.get(Workspace, 1)
        if profile is None or workspace is None:
            raise DomainError("Workspace is not initialized")
        memory_update_time = (
            profile.memory_update_time.strftime("%H:%M") if profile.memory_update_time else "off"
        )
        about_me = html.escape(profile.about_me or "—")
        advisor_instructions = html.escape(profile.advisor_instructions or "—")
        capacity = profile.capacity_effort_points or "—"
        length = profile.sprint_length_days
        timezone = workspace.timezone
        edit_length = await token_button(
            session, services.owner_id, "🏁 Sprint length", "settings_sprint_length"
        )
        await session.commit()
    await send_registered(
        message,
        services,
        with_notice(
            "<b>Settings</b>\n"
            f"About me: {about_me}\n"
            f"Advisor instructions: {advisor_instructions}\n"
            f"Sprint length: {length} days\n"
            f"Sprint capacity: {capacity} EP\n"
            f"Memory sync: {memory_update_time} ({timezone})\n"
            "Edit with /setabout, /setadvisor, /setcapacity, or /setmemtime HH:MM|off.",
            notice,
        ),
        kind=MessageKind.DASHBOARD,
        markup=InlineKeyboardMarkup(inline_keyboard=[[edit_length], menu_row()]),
    )


async def render_sprint_length_prompt(
    message: Message, services: Services, *, notice: str | None = None
) -> None:
    async with services.sessions() as session:
        profile = await session.get(UserProfile, 1)
        if profile is None:
            raise DomainError("Workspace is not initialized")
        current = profile.sprint_length_days
        await session.execute(delete(UiSession).where(UiSession.owner_id == services.owner_id))
        session.add(
            UiSession(
                owner_id=services.owner_id,
                kind="sprint_length",
                state={"message_id": message.message_id},
                expires_at=datetime.now(UTC) + SETTINGS_PROMPT_TTL,
            )
        )
        back = await token_button(session, services.owner_id, "↩️ Back", "settings_back")
        await session.commit()
    await send_registered(
        message,
        services,
        with_notice(
            f"<b>Sprint length</b>\nCurrently {current} days.\n"
            f"Send a number of days between {SPRINT_LENGTH_MIN_DAYS} and "
            f"{SPRINT_LENGTH_MAX_DAYS}. It applies to the next Sprint you start.",
            notice,
        ),
        kind=MessageKind.CARD_EDITOR,
        markup=InlineKeyboardMarkup(inline_keyboard=[[back]]),
        replace=False,
    )


async def sync_bot_commands(bot: Bot, *, sprint_active: bool) -> None:
    """Publish the command list. Today is dropped while the workspace is in Planning."""
    commands = [
        command
        for command in BOT_COMMANDS
        if sprint_active or command.command != "today"
    ]
    await bot.set_my_commands(commands)


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
