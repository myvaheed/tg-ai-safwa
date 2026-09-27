"""How a Cue becomes a message: the gate, the lease, and one Advisor turn.

The turn composes nothing and renders no item. The text it is given is the whole request,
and what comes back is the Advisor's own answer.
"""

from __future__ import annotations

import asyncio
import html
import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

from aiogram import Bot
from aiogram.types import Message
from sqlalchemy import func, select

from telegram_llm import DialogueMessage, markdown_to_telegram_html

from ..ai.outcome import AIOutcome
from ..ai.runs import AgentRun
from ..foundation.clock import utcnow
from ..foundation.kinds import MessageKind
from ..history import TelegramMessage
from ..hooks.contracts import BeforeTurn, Shown
from ..proposals.telegram import render_ai_outcome
from ..telegram import Services, expire_review, owner_anchor, render_citations, send_prose
from ..telegram.chat import clear_draw_home
from ..telegram.dialogue import run_before_turn
from ..turn import own_cancellation
from .initiatives import TickChat

logger = logging.getLogger(__name__)


async def chat_is_free(services: Services) -> bool:
    """Whether work nobody asked for may use the chat: no turn, no review, no session.

    An open proposal is an unanswered question; raising a second one on top of it, one
    the owner did not even initiate, turns the chat into a stack of screens.
    """
    if services.turn.active or services.root.reviews.busy:
        return False
    async with services.sessions() as session:
        # "Resolved completely" includes the model's continuation after the last queue
        # item: that runs with the batch already closed and the session claimed.
        claimed = await session.scalar(
            select(func.count(AgentRun.id)).where(AgentRun.claimed_at.is_not(None))
        )
    return not claimed


async def speak_on_schedule(
    services: Services,
    anchor: Message,
    text: str,
    kind: str,
    still_current: Callable[[], bool],
) -> None:
    """Put the message of a check on a schedule in the chat, while the chat is free.

    The lease is taken for the sending alone, so the owner acting stops it, and the work
    before it runs on. A Home dashboard is drawn over everything above it.
    """
    if not still_current() or not await chat_is_free(services):
        return

    async def say(current: Callable[[], bool]) -> None:
        if not (current() and still_current()):
            return
        if kind != MessageKind.HOME.value:
            await send_prose(
                anchor, services, html.escape(text), kind=MessageKind(kind), replace=False
            )
            return
        async with services.sessions() as session:
            body = await render_citations(session, services, markdown_to_telegram_html(text))
        if current() and still_current():
            await clear_draw_home(anchor, services, body)

    await services.turn.run_background(say)


def tick_chat(services: Services, anchor: Message) -> TickChat:
    """The owner's chat, for the checks on a schedule; `anchor` stands for the owner in it."""

    async def newest() -> tuple[str, datetime | None] | None:
        notes = await services.chat.notes.messages(anchor.chat.id, limit=1)
        return (notes[0].kind, notes[0].at) if notes else None

    async def speak(text: str, kind: str, still_current: Callable[[], bool]) -> None:
        await speak_on_schedule(services, anchor, text, kind, still_current)

    async def free() -> bool:
        return await chat_is_free(services)

    return TickChat(
        owner_acted_at=lambda: services.owner_acted_at, free=free, newest=newest, speak=speak
    )


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
        """Whether the Advisor is free enough to be handed an unsolicited request, with
        the lease taken for it when it is."""
        if not await chat_is_free(self.services):
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

    async def prepare(self, hook: str, payload: list[Any]) -> str | Shown | None:
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

    async def speak(self, event_id: str, text: str, shown: tuple[str, ...] = ()) -> bool:
        """Run one Advisor turn over the request. Returns whether the answer was delivered.

        `shown` opens the answer as it is, before the Advisor's words. Returning False leaves the caller's own record untouched, so whatever produced the
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
            # answer nobody will read. What stands before the answer is in that task too.
            turn = self.services.turn.start_background(self._answer(text, dialogue, shown))
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

    async def _answer(
        self, text: str, dialogue: list[DialogueMessage], shown: tuple[str, ...]
    ) -> AIOutcome:
        """One Advisor turn, after the work that stands before any answer."""
        await run_before_turn(
            self._anchor(),
            self.services,
            BeforeTurn(
                owner_id=self.owner_id,
                chat_id=self.owner_id,
                dialogue_revision=self.services.turn.dialogue_revision,
                source="system",
            ),
            self.still_current,
        )
        return await self.services.root.handle(text, dialogue=dialogue, shown=shown)

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
        """A stand-in for the message that would normally have started this turn."""
        return owner_anchor(self.bot, self.owner_id)
