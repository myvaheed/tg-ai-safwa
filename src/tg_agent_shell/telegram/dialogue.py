from __future__ import annotations

import html
import logging
from datetime import UTC, datetime
from io import BytesIO

from aiogram import F
from aiogram.enums import ChatAction
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message
from sqlalchemy import select

from telegram_llm import AudioClip, HistoryEntry, TranscriptionError

from ..foundation.kinds import MessageKind
from ..proposals.telegram import render_ai_outcome
from .chat import (
    delete_screen,
    dismiss_prior_ui,
    edit_registered_message,
    end_turn,
    open_turn_notice,
    send_owner_turn,
    send_registered,
)
from .model import UiSession
from .services import Services, audio_payload, router
from .text_input import handle_text_input

logger = logging.getLogger(__name__)

# Longer audio is refused with a plain message rather than left to time out.
ASR_MAX_DURATION_SECONDS = 1_800
# The Bot API refuses to serve a file larger than this, whatever the provider accepts.
ASR_MAX_FILE_BYTES = 20 * 1024 * 1024


@router.message(F.text & ~F.text.startswith("/"))
async def ordinary_text(message: Message, services: Services) -> None:
    async with services.sessions() as session:
        ui = await session.scalar(
            select(UiSession)
            .where(
                UiSession.owner_id == services.owner_id, UiSession.expires_at > datetime.now(UTC)
            )
            .order_by(UiSession.created_at.desc())
        )
        ui_kind = ui.kind if ui is not None else None
        ui_state = dict(ui.state) if ui is not None else {}

    if ui_kind == "text_input" and await handle_text_input(message, services, ui_state):
        return

    await dismiss_prior_ui(message, services)
    source = HistoryEntry(
        message_id=message.message_id,
        sender_id=message.from_user.id if message.from_user else None,
        role="user",
        text=message.text,
        created_at=message.date.astimezone(UTC),
        kind=MessageKind.DIALOGUE_USER.value,
    )
    await run_dialogue_turn(message, services, message.text, source)


@router.message(F.voice | F.audio | F.video_note)
async def voice_message(message: Message, services: Services) -> None:
    audio = audio_payload(message)
    if audio is None:
        return
    if services.transcriber is None:
        await send_registered(
            message,
            services,
            "Voice input is off. Set SAFWA_ASR_PROVIDER to turn it on.",
            kind=MessageKind.ERROR,
        )
        return
    duration = int(getattr(audio, "duration", 0) or 0)
    if duration > ASR_MAX_DURATION_SECONDS:
        await send_registered(
            message,
            services,
            f"That recording is {duration // 60} minutes long. Safwa transcribes up to "
            f"{ASR_MAX_DURATION_SECONDS // 60}.",
            kind=MessageKind.ERROR,
        )
        return
    if int(getattr(audio, "file_size", 0) or 0) > ASR_MAX_FILE_BYTES:
        await send_registered(
            message,
            services,
            "That recording is too large for Telegram to hand over.",
            kind=MessageKind.ERROR,
        )
        return

    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    progress = _TranscriptionProgress(message, services)
    try:
        buffer = await message.bot.download(audio.file_id, destination=BytesIO())
        result = await services.transcriber.transcribe(
            AudioClip(
                data=buffer.getvalue(),
                filename=_audio_filename(message),
                mime_type=getattr(audio, "mime_type", None) or "audio/ogg",
                duration_seconds=float(duration),
            ),
            progress=progress.report,
        )
    except (TranscriptionError, TelegramAPIError) as error:
        await send_registered(
            message,
            services,
            "Safwa could not transcribe that recording.\n" + html.escape(str(error)),
            kind=MessageKind.ERROR,
        )
        return
    finally:
        await progress.clear()

    await dismiss_prior_ui(message, services)
    sent = await send_owner_turn(message, services, result.text)
    source = HistoryEntry(
        message_id=sent.message_id,
        sender_id=message.bot.id,
        role="user",
        text=result.text,
        created_at=sent.date.astimezone(UTC),
        kind=MessageKind.DIALOGUE_USER.value,
    )
    await run_dialogue_turn(message, services, result.text, source)


class _TranscriptionProgress:
    """A throwaway percentage while a local decode runs, deleted once it ends.

    It is a `STATUS` message, so it never becomes dialogue, and it is created on the
    first report rather than up front: a hosted endpoint reports nothing and a short
    clip is done before the first edit would land.
    """

    def __init__(self, message: Message, services: Services) -> None:
        self.message = message
        self.services = services
        self.message_id: int | None = None

    async def report(self, done: float, total: float) -> None:
        text = f"🎧 Transcribing… {int(done / total * 100) if total else 0}%"
        if self.message_id is None:
            sent = await send_registered(
                self.message, self.services, text, kind=MessageKind.STATUS, replace=False
            )
            self.message_id = sent.message_id
            return
        await edit_registered_message(
            self.message, self.services, self.message_id, text, kind=MessageKind.STATUS
        )

    async def clear(self) -> None:
        if self.message_id is None:
            return
        await delete_screen(self.message, self.services, self.message_id)
        self.message_id = None


def _audio_filename(message: Message) -> str:
    """A real extension, because the endpoint reads the container from the name."""
    if message.voice is not None:
        return "voice.ogg"
    if message.video_note is not None:
        return "note.mp4"
    return (message.audio.file_name if message.audio else None) or "audio.mp3"


async def run_after_turn(message: Message, services: Services) -> None:
    """Run the work the application does once the owner has been answered.

    The answer is already in the chat and the turn is already given back, so a failure
    here is not a failure of the request. It is said as itself, and the rest still runs:
    one broken piece of after-work must not silence the others, and must never tell the
    owner their request did not go through.
    """
    for after in services.after_turn:
        name = getattr(after, "__qualname__", None) or repr(after)
        try:
            await after(message, services)
        except Exception as error:
            logger.exception("The after-turn work %s failed", name)
            await send_registered(
                message,
                services,
                f"{html.escape(name)}, which runs after the answer, failed: "
                f"{html.escape(str(error))}\nYour answer above stands.",
                kind=MessageKind.ERROR,
            )


async def run_dialogue_turn(
    message: Message, services: Services, request: str, source: HistoryEntry
) -> None:
    """Answer one owner turn.

    `message` is the owner event that holds the turn. `source` is the dialogue turn
    itself, which is a different message whenever the owner's words reached the chat as a
    bot message rather than as their own text.
    """
    dialogue_revision = services.turn.dialogue_revision
    try:
        services.turn.begin(message.message_id)
        await open_turn_notice(message, services)
        await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
        dialogue = await services.history.dialogue(message.chat.id, source_message=source)
        outcome = await services.root.handle(
            request,
            source_message_id=source.message_id,
            dialogue=dialogue,
        )
        # Only the owner invalidates their own answer. The workspace revision does not:
        # an autoapproved change bumps it from inside this very turn.
        if services.turn.dialogue_revision != dialogue_revision:
            logger.info("Discarding an answer the owner already moved past")
            return
        await render_ai_outcome(message, services, outcome)
        await end_turn(message, services)

        await run_after_turn(message, services)
    except Exception as error:
        logger.exception("Could not complete an advisor turn")
        await send_registered(
            message,
            services,
            "Safwa could not complete that request. Your planning data was not changed.\n"
            + html.escape(str(error)),
            kind=MessageKind.ERROR,
        )
    finally:
        # The turn is given back under its own `finally`: an await in a cancelled task
        # raises `CancelledError` straight past `except Exception`, and losing this would
        # leave the turn held by a task that no longer runs, from which point every
        # command is silently dropped by the middleware.
        await end_turn(message, services)
