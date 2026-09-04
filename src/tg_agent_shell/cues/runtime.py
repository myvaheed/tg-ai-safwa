"""How a Cue becomes a message: the gate, the lease, and one Advisor turn.

The turn composes nothing and renders no item. The text it is given is the whole request,
and what comes back is the Advisor's own answer.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from aiogram import Bot
from aiogram.types import Chat, Message, User
from sqlalchemy import func, select

from telegram_llm import DialogueMessage

from ..adapters.kinds import MessageKind
from ..adapters.telegram_history import TelegramMessage
from ..ai.runs import AgentRun
from ..proposals.telegram import render_ai_outcome
from ..telegram import Services

logger = logging.getLogger(__name__)


class CueRuntime:
    """The hooks a poll needs to speak to the owner, bound to the bot and the Advisor."""

    def __init__(self, services: Services, bot: Bot, *, owner_id: int) -> None:
        self.services = services
        self.bot = bot
        self.owner_id = owner_id
        self._lease_revision: int | None = None

    async def can_speak(self) -> bool:
        """Whether the Advisor is free enough to be handed an unsolicited request.

        An open proposal is an unanswered question; raising a second one on top of it —
        one the owner did not even initiate — turns the chat into a stack of screens.
        """
        if self.services.turn.active:
            return False
        if self.services.advisor.reviews.busy:
            return False
        async with self.services.sessions() as session:
            # "Resolved completely" includes the model's continuation after the last queue
            # item: that runs with the batch already closed and the session claimed.
            claimed = await session.scalar(
                select(func.count(AgentRun.id)).where(AgentRun.claimed_at.is_not(None))
            )
            if claimed:
                return False
        if not self.services.turn.try_begin_background():
            return False
        self._lease_revision = self.services.turn.dialogue_revision
        return True

    def still_current(self) -> bool:
        return (
            self._lease_revision is not None
            and self.services.turn.background
            and self.services.turn.dialogue_revision == self._lease_revision
        )

    def release(self) -> None:
        self.services.turn.end_background()
        self._lease_revision = None

    async def speak(self, event_id: str, text: str) -> bool:
        """Run one Advisor turn over the request. Returns whether the answer was delivered.

        Returning False leaves the caller's own record untouched, so whatever produced the
        request is still owed a turn and the next poll asks for it again.
        """
        if not self.still_current():
            return False
        async with self.services.sessions() as session:
            delivered = await session.scalar(
                select(TelegramMessage.id).where(
                    TelegramMessage.chat_id == self.owner_id,
                    TelegramMessage.event_id == event_id,
                )
            )
        if delivered is not None:
            # Telegram delivery was registered but the process stopped before the Cue row
            # was deleted. The next tick finishes that local half instead of saying it again.
            return True
        try:
            dialogue = await self.services.history.dialogue(self.owner_id)
            dialogue = [*dialogue, DialogueMessage(role="user", content=text)]
            if not self.still_current():
                return False
            outcome = await self.services.advisor.handle(text, dialogue=dialogue)
            if not self.still_current():
                # The owner arrived mid-turn and took the turn. Their message wins.
                return False
            # CUE keeps the answer in dialogue while marking it as something the model
            # volunteered, not a reply to a message that is not there.
            await render_ai_outcome(
                self._anchor(),
                self.services,
                outcome,
                kind=MessageKind.CUE,
                event_id=event_id,
            )
            return True
        except Exception:
            # A review that could not be drawn was already ended by `render_ai_outcome`;
            # the Cue row remains, and the next tick retries the whole turn.
            logger.exception("A Cue failed to reach the owner")
            return False

    def _anchor(self) -> Message:
        """A stand-in for the message that would normally have started this turn.

        `from_user` is the owner, which is what makes `send_registered` post a new screen
        instead of trying to edit a message id that does not exist.
        """
        return Message(
            message_id=0,
            date=datetime.now(UTC),
            chat=Chat(id=self.owner_id, type="private"),
            from_user=User(id=self.owner_id, is_bot=False, first_name="Owner"),
        ).as_(self.bot)
