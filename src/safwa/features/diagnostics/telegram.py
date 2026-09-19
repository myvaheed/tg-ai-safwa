"""The one screen: what the workspace is doing."""

from __future__ import annotations

from aiogram.types import Message

from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.telegram import Services, send_registered

from ...foundation.workspace import Workspace


async def command_status(message: Message, services: Services) -> None:
    async with services.sessions() as session:
        workspace = await session.get(Workspace, 1)
    await send_registered(
        message,
        services,
        f"<b>Status</b>\nMode: {workspace.mode}\nRevision: {workspace.revision}",
        kind=MessageKind.DASHBOARD,
    )
