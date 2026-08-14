"""Turning due Reminders into one advisor turn.

The instruction text is handed to the main advisor as a request; what comes back — a
proposal, a sentence, nothing — is the advisor's call.  This module composes no message and
renders no item.

It registers no ``@router`` handlers; the scheduler poll drives it.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.types import Chat, Message, User
from sqlalchemy import func, select

from ..ai.reminder_sessions import check_relevance
from ..enums import MessageKind, ProposalStatus, RelevanceVerdict
from ..models import AgentStep, ChangeProposal, Reminder
from ..scheduler import Firing
from ._core import BACKGROUND_SOURCE_ID, Services
from .proposals import render_ai_outcome

logger = logging.getLogger(__name__)


class ReminderRuntime:
    """The three hooks `run_scheduler` needs, bound to the bot and the advisor."""

    def __init__(
        self, services: Services, bot: Bot, *, owner_id: int, timezone: str
    ) -> None:
        self.services = services
        self.bot = bot
        self.owner_id = owner_id
        self.tz = ZoneInfo(timezone)

    async def can_escalate(self) -> bool:
        """Whether the advisor is free enough to be handed an unsolicited request.

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
            # item, which is what the `resuming` status marks.
            suspended = await session.scalar(
                select(func.count(AgentStep.id)).where(
                    AgentStep.kind == "approval_batch",
                    AgentStep.metadata_json["status"].as_string().in_(["pending", "resuming"]),
                )
            )
            return not suspended

    async def evaluate(self, reminder: Reminder) -> tuple[RelevanceVerdict, str | None]:
        advisor = self.services.advisor
        return await check_relevance(
            advisor.provider,
            instruction=reminder.instruction,
            read_tool=advisor.mini_query_tool(),
            now=datetime.now(UTC),
            tz=self.tz,
        )

    async def escalate(self, firings: list[Firing]) -> bool:
        """Run one advisor turn over the batch. Returns whether the answer was delivered.

        Returning False leaves every `next_fire_at` untouched, so the rows stay due and the
        next poll retries them.
        """
        guard = self.services.guard
        if not guard.reserve_background():
            return False
        revision = guard.dialogue_revision
        try:
            outcome = await self.services.advisor.handle(
                format_escalation(firings, tz=self.tz, now=datetime.now(UTC)),
                dialogue=None,
            )
            if guard.dialogue_revision != revision:
                # The owner arrived mid-turn and took the guard. Their message wins.
                return False
            # REMINDER keeps the answer in dialogue while marking it as something the model
            # volunteered, not a reply to a message that is not there.
            await render_ai_outcome(
                self._anchor(), self.services, outcome, kind=MessageKind.REMINDER
            )
            return True
        except Exception:
            logger.exception("Reminder escalation failed")
            return False
        finally:
            guard.release(BACKGROUND_SOURCE_ID)

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


def format_escalation(firings: list[Firing], *, tz: ZoneInfo, now: datetime) -> str:
    """The whole batch as one request."""
    count = len(firings)
    header = "1 Reminder triggered." if count == 1 else f"{count} Reminders triggered."
    blocks = [header, ""]
    for position, firing in enumerate(firings, start=1):
        blocks.append(f"{position}. Reminder #{firing.reminder_id}")
        blocks.append(f"   Text: {firing.instruction}")
        blocks.append(f"   Schedule: {firing.schedule}{_history(firing, tz=tz)}")
        lateness = _lateness(firing, now=now)
        if lateness:
            blocks.append(f"   {lateness}")
        if firing.state:
            prefix = (
                "State:"
                if firing.verdict is RelevanceVerdict.TRIGGER
                else "State: NO LONGER RELEVANT —"
            )
            blocks.append(f"   {prefix} {firing.state}")
        blocks.append("")
    return "\n".join(blocks).strip()


def _history(firing: Firing, *, tz: ZoneInfo) -> str:
    if not firing.fire_count:
        return " (first time)"
    times = "once" if firing.fire_count == 1 else f"{firing.fire_count} times"
    if firing.last_fired_at is None:
        return f" (fired {times})"
    return f" (fired {times}, last {firing.last_fired_at.astimezone(tz):%Y-%m-%d %H:%M})"


def _lateness(firing: Firing, *, now: datetime) -> str:
    minutes = int((now - firing.due_at).total_seconds() // 60)
    if minutes < 15:
        return ""
    if minutes < 120:
        return f"Was due {minutes} minutes ago."
    hours = minutes // 60
    if hours < 48:
        return f"Was due {hours} hours ago."
    return f"Was due {hours // 24} days ago."
