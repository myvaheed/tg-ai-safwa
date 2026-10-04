"""A photo arriving in the chat, and a photo put back into it.

A photo is looked at once, as it arrives, and kept in the chat as a label the model reads
and a caption the owner wrote. Showing one again sends Telegram's own handle for the file,
which uploads nothing, and the file itself only when Telegram no longer knows that handle.
"""

from __future__ import annotations

import html
import logging
from collections.abc import Sequence
from io import BytesIO
from typing import Any

from aiogram.enums import ChatAction
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup, Message, PhotoSize
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..foundation.errors import DomainError
from ..foundation.kinds import MessageKind
from ..telegram.chat import dismiss_prior_ui, send_registered
from ..telegram.dialogue import run_dialogue_turn
from ..telegram.progress import Progress
from ..telegram.services import Services
from .library import PHOTO_MAX_SIDE, ChatMedia, Photo, media_label

logger = logging.getLogger(__name__)


def kept_size(sizes: Sequence[PhotoSize]) -> PhotoSize:
    """The largest size no longer than `PHOTO_MAX_SIDE`, or the smallest when none is."""
    fitting = [size for size in sizes if max(size.width, size.height) <= PHOTO_MAX_SIDE]
    if fitting:
        return max(fitting, key=lambda size: size.width * size.height)
    return min(sizes, key=lambda size: size.width * size.height)


async def photo_message(
    message: Message, services: Services, album: list[Message] | None = None
) -> None:
    """Label each photo the owner sent, keep it as theirs, and answer it once."""
    photos = album or [message]
    if services.media is None:
        await send_registered(
            message, services, "Image input is off.", kind=MessageKind.ERROR
        )
        return
    caption = next((photo.caption for photo in photos if photo.caption), "")
    owner = (message.from_user.full_name.strip() if message.from_user else "") or "the user"
    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    progress = Progress(message, services, "👀 Looking at the photo…")
    try:
        dialogue = [item.as_message() for item in await services.history.dialogue(message.chat.id)]
        labelled: list[tuple[Photo, str]] = []
        for index, item in enumerate(photos):
            await progress.report(index, len(photos))
            size = kept_size(item.photo)
            buffer = await item.bot.download(size.file_id, destination=BytesIO())
            photo = Photo(buffer.getvalue(), size.width, size.height, size.file_id)
            meta = await services.media.describe(
                photo, owner=owner, caption=caption, dialogue=dialogue
            )
            labelled.append((photo, meta))
        media_ids = await services.media.keep(labelled)
    except Exception as error:
        logger.exception("Could not read a photo")
        await send_registered(
            message,
            services,
            "That photo could not be read.\n" + html.escape(str(error)),
            kind=MessageKind.ERROR,
        )
        return
    finally:
        await progress.clear()

    await dismiss_prior_ui(message, services)
    said: list[str] = []
    for item, media_id, (_photo, meta) in zip(photos, media_ids, labelled, strict=True):
        words = " ".join(filter(None, (media_label(media_id, meta), item.caption)))
        said.append(words)
        await services.chat.keep(
            item,
            kind=MessageKind.DIALOGUE_USER.value,
            reads_as=({"role": "user", "content": words},),
        )
    await run_dialogue_turn(message, services, "\n".join(said), message.message_id)


async def send_media(
    message: Message,
    services: Services,
    media_ids: Sequence[int],
    *,
    kind: MessageKind,
    caption: str | None = None,
    related_id: int | None = None,
) -> list[Message]:
    """Put these photos in the chat as one message: a photo, or an album."""
    async with services.sessions() as session:
        rows = {
            row.id: row
            for row in await session.scalars(select(ChatMedia).where(ChatMedia.id.in_(media_ids)))
        }
    items = [rows[media_id] for media_id in media_ids if media_id in rows]
    if not items:
        raise DomainError("That photo is not kept")
    try:
        return await services.chat.send_photos(
            message,
            [item.file_id for item in items],
            kind=kind.value,
            caption=caption,
            related_id=related_id,
        )
    except TelegramBadRequest as error:
        # Telegram no longer knows a handle it gave out: another bot's database, or a file
        # it let go of. The photo is kept here, so it is sent again, and its new handle kept.
        logger.warning("Sending kept photos again as files: %s", error)
    sent = await services.chat.send_photos(
        message,
        [BufferedInputFile(item.data, filename=f"{item.id}.jpg") for item in items],
        kind=kind.value,
        caption=caption,
        related_id=related_id,
    )
    async with services.sessions() as session:
        for item, photo in zip(items, sent, strict=True):
            if photo.photo:
                row = await session.get(ChatMedia, item.id)
                if row is not None:
                    row.file_id = photo.photo[-1].file_id
        await session.commit()
    return sent


async def send_photo_screen(
    message: Message,
    services: Services,
    media_ids: Sequence[int],
    text: str,
    *,
    kind: MessageKind,
    markup: InlineKeyboardMarkup | None = None,
    related_id: int | None = None,
    replace: bool | None = None,
) -> Message:
    """A screen whose photos stand above its words: the album, then the words and buttons.

    An album cannot be put above a message already in the chat, so a screen this one
    replaces is taken out and the whole screen is drawn below. Each photo is kept with the
    screen's kind, so the next screen takes the album away with the words. A screen with no
    photos is an ordinary one, replaced in place.
    """
    if not media_ids:
        return await send_registered(
            message,
            services,
            text,
            kind=kind,
            markup=markup,
            related_id=related_id,
            replace=replace,
        )
    should_replace = (
        bool(message.from_user and message.from_user.is_bot) if replace is None else replace
    )
    if should_replace:
        await services.chat.remove_screen(message, message.message_id)
    await send_media(message, services, media_ids, kind=kind, related_id=related_id)
    return await send_registered(
        message,
        services,
        text,
        kind=kind,
        markup=markup,
        related_id=related_id,
        replace=False,
    )


async def open_media(
    message: Message, services: Services, media_id: int, *, replace: bool | None = None
) -> None:
    """One photo, under the words of its label.

    It stays in the chat like a message when the next screen comes: there is nothing on it to
    act on.
    """
    async with services.sessions() as session:
        media = await session.get(ChatMedia, media_id)
    if media is None:
        raise DomainError("That photo is not kept")
    should_replace = (
        bool(message.from_user and message.from_user.is_bot) if replace is None else replace
    )
    if should_replace:
        await services.chat.remove_screen(message, message.message_id)
    await send_media(
        message,
        services,
        (media_id,),
        kind=MessageKind.RECEIPT,
        caption=html.escape(media.meta),
        related_id=media_id,
    )


async def media_citation_label(session: AsyncSession, services: Any, media: ChatMedia) -> str:
    return media.meta
