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

from ..ai.context import DialogueMessage
from ..enums import MessageKind, ProposalStatus
from ..features.proposals.model import BatchStatus
from ..models import AgentRun, ApprovalBatch, ChangeProposal, TelegramMessage
from ..telegram._core import BACKGROUND_SOURCE_ID, Services
from ..telegram.proposals import render_ai_outcome

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
        if self.services.guard.active:
            return False
        async with self.services.sessions() as session:
            pending = await session.scalar(
                select(func.count(ChangeProposal.id)).where(
                    ChangeProposal.status == ProposalStatus.PENDING.value
                )
            )
            if pending:
                return False
            # "Resolved completely" includes the model's continuation after the last queue
            # item: that runs with the batch already closed and the session claimed.
            suspended = await session.scalar(
                select(func.count(ApprovalBatch.id)).where(
                    ApprovalBatch.status == BatchStatus.PENDING.value
                )
            )
            if not suspended:
                suspended = await session.scalar(
                    select(func.count(AgentRun.id)).where(AgentRun.claimed_at.is_not(None))
                )
            if suspended:
                return False
        if not self.services.guard.reserve_background():
            return False
        self._lease_revision = self.services.guard.dialogue_revision
        return True

    def still_current(self) -> bool:
        return (
            self._lease_revision is not None
            and self.services.guard.background
            and self.services.guard.dialogue_revision == self._lease_revision
        )

    def release(self) -> None:
        self.services.guard.release(BACKGROUND_SOURCE_ID)
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
        outcome = None
        try:
            dialogue = await self.services.history.dialogue(self.owner_id)
            dialogue = [*dialogue, DialogueMessage(role="user", content=text)]
            if not self.still_current():
                return False
            outcome = await self.services.advisor.handle(text, dialogue=dialogue)
            if not self.still_current():
                # The owner arrived mid-turn and took the guard. Their message wins.
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
            if outcome is not None and outcome.proposal_id is not None:
                try:
                    # A proposal is committed before its Telegram screen is rendered. If
                    # rendering failed, close that unanswered batch so it cannot hold the
                    # Cue gate shut forever; the Cue remains and retries the whole turn.
                    await self.services.advisor.cancel_approval_for_target(
                        "proposal", outcome.proposal_id
                    )
                except Exception:
                    logger.exception("Could not release a failed Cue proposal")
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
