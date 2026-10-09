"""An agent's pictures use the same access gate as its final answer."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from aiogram.enums import ChatAction
from aiogram.types import BufferedInputFile, Message

if TYPE_CHECKING:
    from .manifest import AgentContext


async def publish_agent_photos(
    context: AgentContext, anchor: Message, photos: Sequence[BufferedInputFile], *, kind: str
) -> None:
    if context.publish_photos is not None:
        await context.publish_photos(anchor, photos, kind=kind)
    else:
        await context.bot.send_chat_action(anchor.chat.id, ChatAction.UPLOAD_PHOTO)
        await context.chat.send_photos(anchor, photos, kind=kind)
