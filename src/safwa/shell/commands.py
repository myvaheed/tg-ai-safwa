"""The screens the owner opens by name, and what publishing them to Telegram takes.

Safwa's own three commands are here — home, diagnostics and cancelling a running answer.
Every other command belongs to the feature that draws the screen behind it and reaches this
module only as a `ScreenCommand` the composition root collected.
"""

from __future__ import annotations

import html
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import BotCommand, CallbackQuery, Message
from sqlalchemy import delete

from ..enums import MessageKind
from ..foundation.models import Workspace
from ..foundation.screens import ScreenCommand
from .chat import dismiss_prior_ui, remove_turn_notice, send_registered
from .layout import menu_markup, start_payload
from .model import UiSession
from .screens import open_citation
from .services import Services, router, sprint_is_active

logger = logging.getLogger(__name__)


def _claimed_link(services: Services, payload: str | None):
    """The feature that answers this deep link itself, if one does."""
    if payload is None:
        return None
    return next((link for link in services.start_links if link.claims(payload)), None)


@router.message.middleware()
async def dismiss_screens_before_a_command(
    handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
    event: Message,
    data: dict[str, Any],
) -> Any:
    """A command is the owner leaving whatever screen was open, so it answers none of them.

    Registered once here rather than called from twenty handlers.  Ordinary text dismisses
    from `run_dialogue_turn`'s caller instead, because typed field input must reach its live
    editor untouched, and a link a feature claims replaces its own screen in place.
    """
    text = event.text or ""
    if text.lstrip().startswith("/"):
        services: Services = data["services"]
        if _claimed_link(services, start_payload(text)) is None:
            await dismiss_prior_ui(event, services)
    return await handler(event, data)


async def command_start(message: Message, services: Services) -> None:
    payload = start_payload(message.text)
    if payload is not None:
        link = _claimed_link(services, payload)
        if link is not None:
            await link.open(message, services, payload)
        else:
            await open_citation(message, services, payload)
        return
    async with services.sessions() as session:
        sprint_active = await sprint_is_active(session)
    await send_registered(
        message,
        services,
        "<b>Safwa</b>\nYour personal agile advisor. Choose a dashboard or just write to me.",
        kind=MessageKind.DASHBOARD,
        markup=menu_markup(services.commands, sprint_active=sprint_active),
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


def register_commands(target: Router, commands: tuple[ScreenCommand, ...]) -> None:
    """Bind every declared screen to its command line, once, when the application is built.

    Registered before `turn.dialogue.ordinary_text` would ever see the message, because that
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


@router.callback_query(F.data.startswith("nav:"))
async def navigation(callback: CallbackQuery, services: Services) -> None:
    if not callback.message:
        await callback.answer()
        return
    action = callback.data.split(":", 1)[1]
    # A press is answered once, so which answer it gets is decided before anything is drawn.
    handler = next(
        (screen.handler for screen in services.commands if screen.nav == action), None
    )
    if handler is None:
        logger.warning("Unknown nav action: %s", action)
        await callback.answer("This action is no longer available.", show_alert=True)
        return
    await callback.answer()
    # Walking into the menu is an answer too: whatever else was open is refused.
    await dismiss_prior_ui(callback.message, services)
    if action != "add":
        async with services.sessions() as session:
            await session.execute(delete(UiSession).where(UiSession.owner_id == services.owner_id))
            await session.commit()
    await handler(callback.message, services)
