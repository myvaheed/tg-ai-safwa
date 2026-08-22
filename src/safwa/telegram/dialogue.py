from __future__ import annotations

import html
import logging
from datetime import UTC, datetime, timedelta
from io import BytesIO

from aiogram import F
from aiogram.enums import ChatAction
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message
from sqlalchemy import delete, select

from ..asr import AudioClip, TranscriptionError
from ..constants import ASR_MAX_DURATION_SECONDS, ASR_MAX_FILE_BYTES
from ..domain import (
    DomainError,
    edit_card_text,
    set_sprint_success_criteria,
    update_card_fields,
    update_reminder_text,
    update_tag_fields,
    update_value_fields,
)
from ..enums import MessageKind
from ..features.profile.use_cases import profile_field, set_profile_field
from ..foundation.clock import SystemClock
from ..history import HistoryEntry, register_message
from ..models import UiSession
from ._core import Services, audio_payload, queue_owner_text, router
from ._messaging import (
    delete_screen,
    delete_text_input,
    dismiss_prior_ui,
    edit_registered_message,
    materialize_queued_dialogue,
    send_owner_turn,
    send_registered,
    send_summary,
)
from .cards import render_card, render_card_creation, sanitize_card_creation_state
from .items import render_item_editor
from .proposals import render_ai_outcome
from .reminders import render_reminder
from .sprint import render_sprint
from .text_input import reject_text_input, required_text, validate_text_input

logger = logging.getLogger(__name__)


@router.message(F.text)
async def ordinary_text(message: Message, services: Services) -> None:
    if not message.text or message.text.startswith("/"):
        return
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

    if ui_kind == "text_input":
        flow = str(ui_state.get("flow", ""))
        screen_state = dict(ui_state.get("text_input") or {})
        message_id = int(screen_state["message_id"])

        if flow == "item":
            entity = str(ui_state["entity"])
            mode = str(ui_state["mode"])
            field = str(ui_state["field"])
            item_id = ui_state.get("item_id")
            values = dict(ui_state.get("values", {}))
            try:
                value = validate_text_input(
                    message.text, required_text(f"{entity.title()} name") if field == "name" else None
                )
                values[field] = str(value)
                async with services.sessions() as session:
                    if mode == "view":
                        if entity == "value":
                            await update_value_fields(session, int(item_id), **{field: values[field]})
                        else:
                            await update_tag_fields(session, int(item_id), **{field: values[field]})
                    await session.execute(
                        delete(UiSession).where(UiSession.owner_id == services.owner_id)
                    )
                    await session.commit()
            except (DomainError, ValueError) as error:
                await reject_text_input(message, services, ui_state, str(error))
                return
            await delete_text_input(message, services)
            await render_item_editor(
                message,
                services,
                entity,
                mode=mode,
                item_id=int(item_id) if item_id is not None else None,
                values=values,
                replace_message_id=message_id,
            )
            return
        if flow == "reminder":
            reminder_id = int(ui_state["reminder_id"])
            try:
                value = validate_text_input(message.text, required_text("Reminder text"))
                async with services.sessions() as session:
                    await update_reminder_text(session, reminder_id, str(value))
                    await session.execute(
                        delete(UiSession).where(UiSession.owner_id == services.owner_id)
                    )
                    await session.commit()
            except (DomainError, ValueError) as error:
                await reject_text_input(message, services, ui_state, str(error))
                return
            await delete_text_input(message, services)
            await render_reminder(message, services, reminder_id, replace_message_id=message_id)
            return
        if flow == "sprint":
            try:
                value = validate_text_input(message.text, required_text("Success criteria"))
                async with services.sessions() as session:
                    await set_sprint_success_criteria(session, str(value))
                    await session.execute(
                        delete(UiSession).where(UiSession.owner_id == services.owner_id)
                    )
                    await session.commit()
            except (DomainError, ValueError) as error:
                await reject_text_input(message, services, ui_state, str(error))
                return
            await delete_text_input(message, services)
            await render_sprint(message, services, replace_message_id=message_id)
            return
        if flow == "settings":
            # Imported here, not above: the Settings screen lives in its feature and
            # reaches back into this package. It moves with the handlers in Phase 8.
            from ..features.profile.screens import SETTINGS_FIELDS, command_settings

            field_name = str(ui_state["field"])
            field = SETTINGS_FIELDS.get(field_name)
            if field is None:
                await reject_text_input(message, services, ui_state, "That setting is no longer available.")
                return
            try:
                value = validate_text_input(message.text, field.parse)
                async with services.sessions() as session:
                    await set_profile_field(
                        session, profile_field(field_name), value, clock=SystemClock()
                    )
                    await session.execute(
                        delete(UiSession).where(UiSession.owner_id == services.owner_id)
                    )
                    await session.commit()
            except (DomainError, ValueError) as error:
                await reject_text_input(message, services, ui_state, str(error))
                return
            await delete_text_input(message, services)
            await command_settings(
                message,
                services,
                notice=f"{field.title} updated.",
                replace_message_id=message_id,
            )
            return
        if flow == "card_create":
            field = str(ui_state["input_field"])
            try:
                validator = (
                    required_text("Card title")
                    if field == "title"
                    else required_text("Blocked description")
                    if field == "blocked_description"
                    else None
                )
                value = validate_text_input(message.text, validator)
                state = dict(ui_state)
                state.pop("text_input", None)
                state.pop("input_field", None)
                state.pop("flow", None)
                state[field] = str(value)
                async with services.sessions() as session:
                    await session.execute(
                        delete(UiSession).where(UiSession.owner_id == services.owner_id)
                    )
                    session.add(
                        UiSession(
                            owner_id=services.owner_id,
                            kind="card_create",
                            state=sanitize_card_creation_state(state),
                            expires_at=datetime.now(UTC) + timedelta(minutes=30),
                        )
                    )
                    await session.commit()
            except (DomainError, ValueError) as error:
                await reject_text_input(message, services, ui_state, str(error))
                return
            await delete_text_input(message, services)
            await render_card_creation(message, services, replace_message_id=message_id)
            return
        if flow in {"card", "card_blocked"}:
            card_id = int(ui_state["card_id"])
            field = "blocked_description" if flow == "card_blocked" else str(ui_state["field"])
            try:
                validator = (
                    required_text("Card title")
                    if field == "title"
                    else required_text("Blocked description")
                    if field == "blocked_description"
                    else None
                )
                value = validate_text_input(message.text, validator)
                async with services.sessions() as session:
                    if flow == "card_blocked":
                        card = await update_card_fields(
                            session,
                            card_id,
                            {"blocked": True, "blocked_description": str(value)},
                        )
                    else:
                        card = await edit_card_text(session, card_id, field, str(value))
                    await session.execute(
                        delete(UiSession).where(UiSession.owner_id == services.owner_id)
                    )
                    await session.commit()
            except (DomainError, ValueError) as error:
                await reject_text_input(message, services, ui_state, str(error))
                return
            await delete_text_input(message, services)
            await render_card(
                message,
                services,
                card.id,
                replace_message_id=message_id,
                back=dict(ui_state.get("back", {})),
            )
            return

    await dismiss_prior_ui(message, services)
    async with services.sessions() as session:
        await register_message(
            session, message.chat.id, message.message_id, "in", MessageKind.DIALOGUE_USER
        )
        await session.commit()
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

    # The lease is settled only now: the middleware could not know what this message said
    # until it was decoded, and a decode is long enough for the lease to have changed hands.
    if services.guard.background:
        services.guard.cancel()
    if services.guard.active and services.guard.active_source_id != message.message_id:
        if services.guard.queue_messages:
            await queue_owner_text(message, services, result.text, delete_source=False)
            return
        services.guard.cancel()

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


async def run_dialogue_turn(
    message: Message, services: Services, request: str, source: HistoryEntry
) -> None:
    """Answer one owner turn, then drain whatever queued while the advisor was busy.

    `message` is the owner event that owns the generation lease. `source` is the dialogue
    turn itself, which is a different message whenever the owner's words reached the chat
    as a bot message rather than as their own text.
    """
    dialogue_revision = services.guard.dialogue_revision
    current_source = source
    current_request = request
    try:
        await services.guard.acquire(message.message_id, queue_messages=True)
        while True:
            await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
            dialogue = await services.history.dialogue(
                message.chat.id, source_message=current_source
            )
            outcome = await services.advisor.handle(
                current_request,
                source_message_id=current_source.message_id,
                dialogue=dialogue,
            )
            # Only the owner invalidates their own answer. The workspace revision does not:
            # an autoapproved change bumps it from inside this very turn.
            if services.guard.dialogue_revision != dialogue_revision:
                logger.info("Discarding an answer the owner already moved past")
                return
            await render_ai_outcome(message, services, outcome)
            queued = await materialize_queued_dialogue(message, services)
            if queued is None:
                break
            queued_message, current_request = queued
            await dismiss_prior_ui(queued_message, services)
            current_source = HistoryEntry(
                message_id=queued_message.message_id,
                sender_id=services.owner_id,
                role="user",
                text=current_request,
                created_at=queued_message.date.astimezone(UTC),
                kind=MessageKind.DIALOGUE_USER.value,
            )

        services.guard.release(message.message_id)

        await services.guard.run_background(
            lambda still_current: services.continuity.maybe_summarize(
                message.chat.id,
                lambda text, covered_id: send_summary(message, services, text, covered_id),
                still_current=still_current,
            )
        )
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
        # The lease is released under its own `finally`: restoring the queue awaits, and an
        # await in a cancelled task raises `CancelledError` straight past `except Exception`.
        # Losing the release there would leave the guard held by a task that no longer runs,
        # and from then on every command is silently dropped by the middleware.
        try:
            if services.guard.active_source_id == message.message_id:
                await materialize_queued_dialogue(message, services)
        except Exception:
            logger.exception("Could not restore queued messages after generation stopped")
        finally:
            services.guard.release(message.message_id)
