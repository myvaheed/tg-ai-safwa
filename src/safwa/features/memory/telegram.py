"""The three commands that reach the memory file.

Memory draws no buttons: every one of these answers with a receipt and gives the turn
straight back, because the work itself runs in the background under the turn's lease.
"""

from __future__ import annotations

import html

from aiogram.types import Message

from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.telegram import Services, send_registered

from .api import memory_store, memory_upkeep
from .store import MemoryFileError
from .upkeep import MemoryMaintenanceResult
from .use_cases import record_memory_run


async def command_memory(message: Message, services: Services) -> None:
    snapshot = await memory_store(services).sync()
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
        lambda still_current: memory_upkeep(services).maintain_memory(
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
        await memory_store(services).append_manual(fact)
    except MemoryFileError as error:
        await send_registered(message, services, str(error), kind=MessageKind.ERROR)
        return
    await send_registered(message, services, "Remembered in memory.md.", kind=MessageKind.RECEIPT)
