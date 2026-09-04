"""The one screen: what the workspace is doing, and whether memory can be read."""

from __future__ import annotations

import html

from aiogram.types import Message

from tg_agent_shell.adapters.kinds import MessageKind
from tg_agent_shell.telegram import Services, send_registered

from ...foundation.workspace import Workspace
from ..continuity.api import memory_health


async def command_status(message: Message, services: Services) -> None:
    health = await memory_health(services.memory)
    async with services.sessions() as session:
        workspace = await session.get(Workspace, 1)
    await send_registered(
        message,
        services,
        f"<b>Status</b>\nMode: {workspace.mode}\nRevision: {workspace.revision}\n"
        f"Memory: {html.escape(health)}",
        kind=MessageKind.DASHBOARD,
    )
