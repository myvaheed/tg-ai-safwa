"""How a Reminder reaches the owner: the proposal screen, and the escalation turn.

The escalation composes no message and renders no item — the instruction text is handed to
the main advisor as a request, and what comes back is the advisor's call. It registers no
``@router`` handlers; this feature's own poll drives it.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.types import Chat, Message, User
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...ai.context import DialogueMessage
from ...ai.contracts import AgentChange
from ...enums import MessageKind, ProposalStatus
from ...models import AgentRun, AgentStep, ChangeProposal, ProposalChange
from ...telegram._core import BACKGROUND_SOURCE_ID, Services
from ...telegram.proposals import render_ai_outcome
from ..proposals.api import (
    ACTION_VERBS,
    ProposalScreen,
    detail_lines,
    result_value,
)
from .background import Firing
from .model import Reminder

logger = logging.getLogger(__name__)


class ReminderProposalPresenter:
    entity = "reminder"

    def raw_details(self, change: AgentChange) -> list[str]:
        return detail_lines(dict(change.values))

    async def details(
        self, session: AsyncSession, change: ProposalChange, fallback: AgentChange | None
    ) -> list[str]:
        fallback_lines = self.raw_details(fallback) if fallback is not None else []
        return fallback_lines or detail_lines(dict(change.values))

    async def summary(
        self, session: AsyncSession, change: ProposalChange, details: list[str]
    ) -> str:
        values = dict(change.values)
        reminder = (
            await session.get(Reminder, change.entity_id)
            if change.entity_id is not None
            else None
        )
        text = str(values.get("instruction") or (reminder.instruction if reminder else ""))
        head = f"Reminder “{result_value(text)}”" if text else f"Reminder #{change.entity_id}"
        # A Reminder has no archive, so the only removal `remove` can send reads as one.
        verb = (
            "Delete"
            if change.action == "archive"
            else ACTION_VERBS.get(change.action, change.action.title())
        )
        schedule = values.get("schedule_text")
        return f"{verb} {head}" + (f" ({schedule})" if schedule else "")

    async def screen(
        self, session: AsyncSession, change: ProposalChange
    ) -> ProposalScreen | None:
        # A Reminder is instruction plus timing; the generic change list already says both.
        return None


class ReminderRuntime:
    """The three hooks `run_scheduler` needs, bound to the bot and the advisor."""

    def __init__(
        self, services: Services, bot: Bot, *, owner_id: int, timezone: str
    ) -> None:
        self.services = services
        self.bot = bot
        self.owner_id = owner_id
        self.tz = ZoneInfo(timezone)
        self._lease_revision: int | None = None

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

    async def escalate(self, firings: list[Firing]) -> bool:
        """Run one advisor turn over the batch. Returns whether the answer was delivered.

        Returning False leaves every `next_fire_at` untouched, so the rows stay due and the
        next poll retries them.
        """
        if not self.still_current():
            return False
        try:
            request = format_escalation(firings, tz=self.tz, now=datetime.now(UTC))
            dialogue = await self.services.history.dialogue(self.owner_id)
            dialogue = [*dialogue, DialogueMessage(role="user", content=request)]
            if not self.still_current():
                return False
            outcome = await self.services.advisor.handle(
                request,
                dialogue=dialogue,
            )
            if not self.still_current():
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
    blocks = [
        header,
        (
            "If a Reminder mentions Safwa items, check their current state with query_safwa "
            "first: it may no longer apply. Then answer it as you would answer the user."
        ),
        "",
    ]
    for position, firing in enumerate(firings, start=1):
        blocks.append(f"{position}. Reminder #{firing.reminder_id}")
        blocks.append(f"   Text: {firing.instruction}")
        blocks.append(f"   Schedule: {firing.schedule}{_history(firing, tz=tz)}")
        lateness = _lateness(firing, now=now)
        if lateness:
            blocks.append(f"   {lateness}")
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
