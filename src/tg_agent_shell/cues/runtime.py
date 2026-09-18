"""How a Cue becomes a message: the gate, the lease, and one Advisor turn.

The turn composes nothing and renders no item. The text it is given is the whole request,
and what comes back is the Advisor's own answer.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from aiogram import Bot
from aiogram.types import Chat, Message, User
from sqlalchemy import func, select

from telegram_llm import DialogueMessage

from ..ai.outcome import AIOutcome
from ..ai.runs import AgentRun
from ..foundation.clock import utcnow
from ..foundation.kinds import MessageKind
from ..history import TelegramMessage
from ..proposals.telegram import render_ai_outcome
from ..telegram import Services, expire_review
from ..turn import own_cancellation

logger = logging.getLogger(__name__)


class CueRuntime:
    """The hooks a poll needs to speak to the owner, bound to the bot and the Advisor."""

    def __init__(self, services: Services, bot: Bot, *, owner_id: int) -> None:
        self.services = services
        self.bot = bot
        self.owner_id = owner_id
        self._lease_revision: int | None = None

    async def expire_review(self) -> None:
        """Close the review the owner left unanswered past its time.

        It takes the lease a Cue takes, so it never runs inside the owner's own turn and
        never over one; an owner arriving while it runs finds the review closed, and their
        press or words land on a screen that says so.
        """
        review = self.services.root.reviews.expired(utcnow())
        if review is None or not self.services.turn.try_begin_background():
            return
        revision = self.services.turn.dialogue_revision
        try:
            await expire_review(self._anchor(), self.services, review.id)
        finally:
            self.services.turn.end_background(revision)

    async def can_speak(self) -> bool:
        """Whether the Advisor is free enough to be handed an unsolicited request.

        An open proposal is an unanswered question; raising a second one on top of it —
        one the owner did not even initiate — turns the chat into a stack of screens.
        """
        if self.services.turn.active:
            return False
        if self.services.root.reviews.busy:
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
        """Give back the lease this runtime took, if it took one and still holds it."""
        if self._lease_revision is not None:
            self.services.turn.end_background(self._lease_revision)
            self._lease_revision = None

    async def prepare(self, hook: str, payload: list[Any]) -> str | None:
        """The words of a hook's request, from the hook's own feature, or None to drop it."""
        return await self.services.hooks.prepare(self.services.sessions, hook, payload)

    async def delivered(self, event_id: str) -> bool:
        """Whether the turn under this id reached the chat: its message was registered,
        even if the process stopped before the rows it said were settled."""
        async with self.services.sessions() as session:
            found = await session.scalar(
                select(TelegramMessage.id).where(
                    TelegramMessage.chat_id == self.owner_id,
                    TelegramMessage.event_id == event_id,
                )
            )
        return found is not None

    async def speak(self, event_id: str, text: str) -> bool:
        """Run one Advisor turn over the request. Returns whether the answer was delivered.

        Returning False leaves the caller's own record untouched, so whatever produced the
        request is still owed a turn and the next poll asks for it again.
        """
        if not self.still_current():
            return False
        try:
            dialogue = await self.services.history.dialogue(self.owner_id)
            dialogue = [*dialogue, DialogueMessage(role="user", content=text)]
            if not self.still_current():
                return False
            # The turn is its own task, so the lease that was taken for it is what stops
            # it: the owner arriving ends the provider traffic instead of paying for an
            # answer nobody will read.
            turn = self.services.turn.start_background(
                self.services.root.handle(text, dialogue=dialogue)
            )
            try:
                outcome = await turn
            except asyncio.CancelledError:
                if own_cancellation():
                    raise
                outcome = None
            if outcome is None or not self.still_current():
                # The owner arrived mid-turn and took the turn. Their message wins, and a
                # review this turn had already opened ends with it rather than standing in
                # the store with nothing on screen.
                await self._end_unseen_review(outcome)
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

    async def _end_unseen_review(self, outcome: AIOutcome | None) -> None:
        """End the review a turn that lost the chat left open, the way an undrawn one ends.

        Nothing else can answer it: no screen for it ever reached the chat, and the store
        it stands in is what holds the Cue gate shut for the life of the process.
        """
        if outcome is None or outcome.proposal_id is None:
            return
        try:
            await self.services.root.cancel_approval_for_proposal(outcome.proposal_id)
        except Exception:
            logger.exception("Could not end a review the owner's arrival cut short")

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
