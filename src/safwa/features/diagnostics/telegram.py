"""The one screen: what the workspace is doing, and whether memory can be read."""

from __future__ import annotations

import html

from aiogram.types import Message

from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.telegram import Services, send_registered

from ...foundation.workspace import Workspace
from ..memory.api import memory_health, memory_store


async def command_status(message: Message, services: Services) -> None:
    health = await memory_health(memory_store(services))
    async with services.sessions() as session:
        workspace = await session.get(Workspace, 1)
    hooks = "\n".join(
        html.escape(
            f"{registration.spec.name} · {registration.spec.owner} · "
            f"{'on' if registration.enabled else 'off'} · "
            f"{', '.join(str(on) for on in registration.spec.on)} → "
            f"{type(registration.spec.effect).__name__}"
        )
        for registration in services.hooks.registrations
    )
    await send_registered(
        message,
        services,
        f"<b>Status</b>\nMode: {workspace.mode}\nRevision: {workspace.revision}\n"
        f"Memory: {html.escape(health)}\n\n<b>Hooks</b>\n{hooks or 'None registered.'}",
        kind=MessageKind.DASHBOARD,
    )
