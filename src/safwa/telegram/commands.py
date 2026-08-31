from __future__ import annotations

import html
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import delete, select

from ..enums import MessageKind
from ..features.continuity.memory import MemoryFileError
from ..features.continuity.persona import MemoryMaintenanceResult
from ..features.continuity.use_cases import record_memory_run
from ..foundation.screens import ScreenCommand
from ..models import (
    SavedRequest,
    Tag,
    UiSession,
    Value,
    Workspace,
)
from ..shell import (
    Services,
    dismiss_prior_ui,
    menu_markup,
    menu_row,
    open_citation,
    remove_turn_notice,
    router,
    send_registered,
    send_summary,
    sprint_is_active,
    start_payload,
    token_button,
)
from .plan import handle_plan_start, is_plan_link
from .reminders import render_reminders
from .sprint import render_sprint, render_today

logger = logging.getLogger(__name__)

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


async def command_summarize(message: Message, services: Services) -> None:
    """Cut the context deliberately: post a Summary now instead of waiting for the budget."""
    written = await services.turn.run_background(
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


async def command_today(message: Message, services: Services) -> None:
    await render_today(message, services)


async def command_sprint(message: Message, services: Services) -> None:
    await render_sprint(message, services)


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


async def command_memory(message: Message, services: Services) -> None:
    snapshot = await services.memory.sync()
    text = f"<b>Persistent memory</b> · {snapshot.estimated_tokens}/4000 tokens\n" + (
        "\n".join(f"{i}. {html.escape(fact)}" for i, fact in enumerate(snapshot.facts, 1))
        or "Empty"
    )
    await send_registered(message, services, text, kind=MessageKind.DASHBOARD)


async def command_syncmem(message: Message, services: Services) -> None:
    if (message.text or "").partition(" ")[2].strip():
        await send_registered(message, services, "Usage: /syncmem", kind=MessageKind.ERROR)
        return
    result = await services.turn.run_background(
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


async def command_reminders(message: Message, services: Services) -> None:
    """Show the triggers the owner set; creation and timing stay advisor-only."""
    await render_reminders(message, services)


def register_commands(target: Router, commands: tuple[ScreenCommand, ...]) -> None:
    """Bind every declared screen to its command line, once, when the application is built.

    Registered before `dialogue.ordinary_text` would ever see the message, because that
    handler declines anything starting with a slash in its own filter.
    """
    for screen in commands:
        if screen.command is not None:
            target.message.register(screen.handler, Command(screen.command))


async def sync_bot_commands(
    bot: Bot, commands: tuple[ScreenCommand, ...], *, sprint_active: bool
) -> None:
    """Publish the command list. Today is dropped while the workspace is in Planning."""
    await bot.set_my_commands(
        [
            BotCommand(command=screen.command, description=screen.description)
            for screen in commands
            if screen.command is not None and (sprint_active or not screen.needs_sprint)
        ]
    )


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


async def command_cancel(message: Message, services: Services) -> None:
    await remove_turn_notice(message, services, services.turn.cancel())
    await send_registered(
        message, services, "Current generation cancelled.", kind=MessageKind.RECEIPT
    )


SHELL_COMMANDS: tuple[ScreenCommand, ...] = (
    ScreenCommand(
        handler=command_start, command="start", description="Open Safwa", nav="home"
    ),
    ScreenCommand(handler=command_status, command="status", description="Safwa diagnostics"),
    ScreenCommand(handler=command_cancel, command="cancel", description="Cancel generation"),
)


@router.callback_query(F.data.startswith("nav:"))
async def navigation(callback: CallbackQuery, services: Services) -> None:
    await callback.answer()
    if not callback.message:
        return
    action = callback.data.split(":", 1)[1]
    # Walking into the menu is an answer too: whatever else was open is refused.
    await dismiss_prior_ui(callback.message, services)
    handler = next(
        (screen.handler for screen in services.commands if screen.nav == action), None
    )
    if handler is None:
        logger.warning("Unknown nav action: %s", action)
        await callback.answer("This action is no longer available.", show_alert=True)
        return
    if action != "add":
        async with services.sessions() as session:
            await session.execute(delete(UiSession).where(UiSession.owner_id == services.owner_id))
            await session.commit()
    await handler(callback.message, services)
