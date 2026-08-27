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
from ..models import AgentRun, AgentStep, ChangeProposal
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
                select(func.count(AgentStep.id)).where(
                    AgentStep.kind == "approval_batch",
                    AgentStep.metadata_json["status"].as_string() == "pending",
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

    async def speak(self, text: str) -> bool:
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
            outcome = await self.services.advisor.handle(text, dialogue=dialogue)
            if not self.still_current():
                # The owner arrived mid-turn and took the guard. Their message wins.
                return False
            # CUE keeps the answer in dialogue while marking it as something the model
            # volunteered, not a reply to a message that is not there.
            await render_ai_outcome(
                self._anchor(), self.services, outcome, kind=MessageKind.CUE
            )
            return True
        except Exception:
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
