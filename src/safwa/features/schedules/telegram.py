"""A Schedule typed into an editor is compiled before it is written."""

from __future__ import annotations

from typing import Any

from aiogram.enums import ChatAction
from aiogram.types import Message

from tg_agent_shell.foundation.clock import utcnow
from tg_agent_shell.foundation.errors import DomainError
from tg_agent_shell.telegram import Services

from .api import ScheduleTarget, workspace_zone


async def compile_typed_schedule(
    message: Message, services: Services, target: ScheduleTarget, text: str
) -> dict[str, Any]:
    """The text and its rule. A question from the Scheduler refuses the value, so the
    editor shows it and keeps waiting; off clears the Schedule."""
    if text.casefold() == "off":
        return {"text": None, "rule": None}
    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    async with services.sessions() as session:
        tz = await workspace_zone(session)
    rule, question = await services.features.schedule_compiler.compile(text, utcnow(), tz, target)
    if question:
        raise DomainError(question)
    return {"text": text, "rule": rule}
