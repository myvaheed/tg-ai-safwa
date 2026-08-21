"""The Sprint that reached its planned end date closes itself."""

from __future__ import annotations

from ...bootstrap.module_manifest import BackgroundContext, BackgroundTask
from ...enums import MessageKind
from ...history import mark_message, register_message
from ...scheduler import run_sprint_expiry
from ...telegram import sync_bot_commands


async def _announce_and_expire(context: BackgroundContext) -> None:
    async def announce(number: int) -> None:
        text = (
            f"⏹ Sprint {number} reached its planned end date and was closed automatically. "
            "Whatever was still open kept its stage."
        )
        marked_text, event_id = mark_message(text, MessageKind.RECEIPT)
        sent = await context.bot.send_message(context.settings.telegram_owner_id, marked_text)
        async with context.sessions() as session:
            await register_message(
                session,
                sent.chat.id,
                sent.message_id,
                "out",
                MessageKind.RECEIPT,
                event_id=event_id,
            )
            await session.commit()
        await sync_bot_commands(context.bot, sprint_active=False)

    await run_sprint_expiry(context.sessions, announce=announce)


SPRINT_EXPIRY = BackgroundTask("sprint-expiry", _announce_and_expire)
