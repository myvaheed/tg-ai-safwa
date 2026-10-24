"""A subsession in the chat: which agent the owner's words go to, how one opens, and how
it hands its result back.

The stretch of the chat it holds is `history.py`'s, kept by message id. What is here is what
the chat sees of it: the frame it opens with, the answers of its agent, the result it closes
with in the conversation it was opened from, and the stretch taken out of the chat after.
Every step runs under the turn the caller holds.
"""

from __future__ import annotations

import html
import logging

from aiogram.types import Message

from telegram_llm import markdown_to_telegram_html

from ..ai.outcome import AIOutcome
from ..foundation.kinds import MessageKind
from ..history import (
    CONVERSATION_KINDS,
    Subsession,
    close_subsession,
    current_subsession,
    result_event,
    start_subsession,
    undelivered_subsessions,
)
from .chat import keep_typing, send_prose, send_registered
from .contributions import ScreenCommand
from .screens import render_citations
from .services import Services

logger = logging.getLogger(__name__)

# The result a subsession closes with when the owner ends it, or when its agent is gone.
EXIT_RESULT = "The conversation was ended without a result."
NOTHING_TO_EXIT = "No conversation with an agent is open."


async def talking(message: Message, services: Services) -> str | None:
    """The agent the owner's words go to: the open subsession's, or None for the root's.

    What a subsession still owes the chat is settled first: a result that never reached it
    is delivered, and one whose agent is no longer registered is closed, as /exit closes it.
    """
    async with services.sessions() as session:
        owed = await undelivered_subsessions(session, message.chat.id)
        subsession = await current_subsession(session, message.chat.id)
    for closed in owed:
        await deliver_result(message, services, closed)
    if subsession is None:
        return None
    if subsession.kind not in services.root.talks:
        await finish_subsession(message, services, EXIT_RESULT)
        return None
    return subsession.kind


async def enter_subsession(
    message: Message, services: Services, kind: str, *, event_id: str | None = None
) -> AIOutcome:
    """Open a subsession with `kind`'s agent after the newest message, frame it, and have
    its agent speak first. The database refuses a second open subsession in the chat."""
    async with services.sessions() as session:
        await start_subsession(session, message.chat.id, kind)
        await session.commit()
    await send_registered(
        message,
        services,
        f"{html.escape(_title(services, kind))}\n/exit ends it.",
        kind=MessageKind.RECEIPT,
        event_id=event_id,
        replace=False,
    )
    async with keep_typing(message):
        return await services.root.talk(kind)


async def finish_subsession(message: Message, services: Services, result: str) -> bool:
    """Close the open subsession with its result, then hand that result back.

    Of two closings, the first decides: one that finds it closed already does nothing, and
    says so by returning False.
    """
    async with services.sessions() as session:
        subsession = await current_subsession(session, message.chat.id)
        if subsession is None or not await close_subsession(session, subsession.id, result):
            return False
        await session.commit()
    async with services.sessions() as session:
        closed = await session.get(Subsession, subsession.id)
    if closed is not None:
        await deliver_result(message, services, closed)
    return True


async def deliver_result(message: Message, services: Services, subsession: Subsession) -> None:
    """Put a closed subsession's result in the conversation it was opened from, then take its
    stretch out of the chat.

    The result is a message of the conversation's own, newer than the whole stretch. Taking
    the stretch out is the chat's tidiness, not the boundary: a message Telegram keeps is
    still no part of the conversation, so a stretch it refuses to delete is left as it is.
    """
    # Closed, so both are set.
    assert subsession.through_message_id is not None and subsession.result is not None
    async with services.sessions() as session:
        words = await render_citations(
            session, services, markdown_to_telegram_html(subsession.result)
        )
    await send_prose(
        message,
        services,
        f"<b>{html.escape(_title(services, subsession.kind))}</b>\n{words}",
        kind=MessageKind.EVENT,
        event_id=result_event(subsession.id),
        replace=False,
    )
    try:
        await services.chat.clear(
            message.bot,
            message.chat.id,
            subsession.through_message_id,
            first=subsession.after_message_id + 1,
            keep=CONVERSATION_KINDS,
        )
    except Exception:
        logger.exception("Could not take subsession #%s out of the chat", subsession.id)


async def command_exit(message: Message, services: Services) -> None:
    """End the open subsession without a result, under a turn of its own.

    A review open in it was already closed, as before any command.
    """
    if not services.turn.try_begin(message.message_id):
        return
    try:
        if not await finish_subsession(message, services, EXIT_RESULT):
            await send_registered(
                message, services, NOTHING_TO_EXIT, kind=MessageKind.RECEIPT, replace=False
            )
    finally:
        services.turn.end(message.message_id)


# Published only by an application that declares an agent a subsession is opened with.
EXIT_COMMAND = ScreenCommand(
    handler=command_exit, command="exit", description="End the conversation with an agent"
)


def _title(services: Services, kind: str) -> str:
    """What the chat calls a subsession with `kind`, or its name once the agent is gone."""
    talk = services.root.talks.get(kind)
    return talk.title if talk is not None and talk.title else kind
