from __future__ import annotations

import html
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import Bot, F
from aiogram.filters import Command
from aiogram.types import (
    BotCommand,
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import delete, select

from ..analytics import render_retrospective_png, retrospective_data, retrospective_recommendations
from ..enums import MessageKind
from ..features.cards.model import CardStage
from ..features.continuity.memory import MemoryFileError
from ..features.continuity.persona import MemoryMaintenanceResult
from ..features.continuity.use_cases import record_memory_run
from ..history import mark_message, register_message
from ..models import (
    SavedRequest,
    Sprint,
    Tag,
    UiSession,
    Value,
    Workspace,
)
from ._core import Services, router, sprint_is_active
from ._messaging import (
    dismiss_prior_ui,
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
)
from .cards import render_dashboard, start_manual_card_creation
from .plan import handle_plan_start, is_plan_link
from .reminders import render_reminders
from .screens import open_citation
from .sprint import render_sprint, render_today

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
    written = await services.guard.run_background(
        lambda still_current: services.continuity.maybe_summarize(
            message.chat.id,
            lambda text, covered_id: send_summary(message, services, text, covered_id),
            force=True,
            still_current=still_current,
        )
    )
    if written is None:
        await send_registered(
            message,
            services,
            "Wait for the current advisor response, then retry /summarize.",
            kind=MessageKind.ERROR,
        )
        return
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
                select(Value).order_by(Value.name)
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
            await session.scalars(select(Tag).order_by(Tag.name))
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
                select(SavedRequest).order_by(SavedRequest.name)
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
    result = await services.guard.run_background(
        lambda still_current: services.continuity.maintain_memory(
            message.chat.id,
            still_current=still_current,
        )
    )
    if result is None:
        await send_registered(
            message,
            services,
            "Wait for the current advisor response, then retry /syncmem.",
            kind=MessageKind.ERROR,
        )
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
    try:
        await services.memory.append_manual(fact)
    except MemoryFileError as error:
        await send_registered(message, services, str(error), kind=MessageKind.ERROR)
        return
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


@router.message(Command("reminders"))
async def command_reminders(message: Message, services: Services) -> None:
    """Show the triggers the owner set; creation and timing stay advisor-only."""
    await render_reminders(message, services)


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
    await send_registered(
        message,
        services,
        f"<b>Status</b>\nMode: {workspace.mode}\nRevision: {workspace.revision}\n"
        f"Memory: {html.escape(memory.error or 'OK')}",
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
    # Imported here, not above: the Settings screen lives in its feature and reaches
    # back into this package. It moves with the handlers in Phase 8.
    from ..features.profile.screens import command_settings

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
