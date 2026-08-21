"""Persona continuity in the background: the memory file, and the memory it maintains."""

from __future__ import annotations

from ...bootstrap.module_manifest import BackgroundContext, BackgroundTask
from ...continuity import run_memory_maintenance
from ...enums import MessageKind
from ...history import mark_message, register_message
from ...telegram import BACKGROUND_SOURCE_ID


async def _poll_memory_file(context: BackgroundContext) -> None:
    async def memory_error(text: str) -> None:
        marked_text, event_id = mark_message(f"⚠️ memory.md: {text}", MessageKind.ERROR)
        sent = await context.bot.send_message(
            context.settings.telegram_owner_id,
            marked_text,
        )
        async with context.sessions() as session:
            await register_message(
                session,
                sent.chat.id,
                sent.message_id,
                "out",
                MessageKind.ERROR,
                event_id=event_id,
            )
            await session.commit()

    await context.memory.poll(memory_error)


async def _maintain_memory(context: BackgroundContext) -> None:
    guard = context.services.guard
    await run_memory_maintenance(
        context.continuity,
        context.sessions,
        context.settings.telegram_owner_id,
        lambda: guard.active,
        context.settings.timezone,
        reserve_background=guard.reserve_background,
        dialogue_revision=lambda: guard.dialogue_revision,
        release_background=lambda: guard.release(BACKGROUND_SOURCE_ID),
    )


MEMORY_FILE_POLL = BackgroundTask("memory-file-poll", _poll_memory_file)
MEMORY_MAINTENANCE = BackgroundTask("memory-maintenance", _maintain_memory)
