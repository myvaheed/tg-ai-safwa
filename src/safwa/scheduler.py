from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .enums import CardKind, CardStage, MessageKind
from .models import (
    Card,
    CardValue,
    FeedbackQueue,
    ReminderState,
    Sprint,
    TelegramMessage,
    UserProfile,
    Value,
    Workspace,
)

logger = logging.getLogger(__name__)


class ReminderPolicy:
    def __init__(self, timezone: str, *, now: Callable[[], datetime] | None = None) -> None:
        self.timezone = ZoneInfo(timezone)
        self.now = now or (lambda: datetime.now(UTC))

    async def eligible(self, session: AsyncSession, kind: str, dedupe_key: str) -> bool:
        profile = await session.get(UserProfile, 1)
        if profile is None or not profile.reminders_enabled:
            return False
        current_utc = self.now()
        local = current_utc.astimezone(self.timezone)
        local_midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
        daily_count = (
            await session.scalar(
                select(func.count(TelegramMessage.id)).where(
                    TelegramMessage.kind == MessageKind.REMINDER.value,
                    TelegramMessage.created_at >= local_midnight.astimezone(UTC),
                )
            )
            or 0
        )
        if daily_count >= profile.proactive_limit:
            return False
        if not profile.weekend_enabled and local.weekday() >= 5:
            return False
        current = local.timetz().replace(tzinfo=None)
        if profile.quiet_start and profile.quiet_end:
            if profile.quiet_start <= profile.quiet_end:
                quiet = profile.quiet_start <= current <= profile.quiet_end
            else:
                quiet = current >= profile.quiet_start or current <= profile.quiet_end
            if quiet:
                return False
        state = await session.get(ReminderState, kind)
        global_state = await session.get(ReminderState, "global")
        if global_state and global_state.snoozed_until and global_state.snoozed_until > current_utc:
            return False
        if state and state.snoozed_until and state.snoozed_until > current_utc:
            return False
        if state and state.last_sent_at:
            cooldown = timedelta(minutes=profile.reminder_cooldown_minutes)
            if state.last_sent_at + cooldown > current_utc:
                return False
            if state.dedupe_key == dedupe_key:
                return False
        return True

    async def candidates(self, session: AsyncSession) -> list[tuple[str, str, str]]:
        candidates: list[tuple[str, str, str]] = []
        profile = await session.get(UserProfile, 1)
        if profile is None:
            return candidates
        current_utc = self.now()
        local = current_utc.astimezone(self.timezone)
        for kind, configured, text in [
            (
                "morning",
                (profile.morning_checkin or profile.wake_time) if profile else None,
                "Offer a short morning Planning check-in.",
            ),
            (
                "evening",
                (profile.evening_checkin or profile.bed_time) if profile else None,
                "Offer a short evening reflection check-in.",
            ),
        ]:
            if configured:
                target = local.replace(
                    hour=configured.hour, minute=configured.minute, second=0, microsecond=0
                )
                if abs((local - target).total_seconds()) <= 30 * 60:
                    key = local.date().isoformat()
                    if await self.eligible(session, kind, key):
                        candidates.append((kind, key, text))
        feedback = (
            await session.scalar(
                select(func.count(FeedbackQueue.id)).where(FeedbackQueue.answered_at.is_(None))
            )
            or 0
        )
        if feedback and await self.eligible(session, "feedback", str(feedback)):
            candidates.append(
                ("feedback", str(feedback), f"You have {feedback} completion feedback item(s).")
            )
        today = list(
            await session.scalars(
                select(Card).where(
                    Card.effective_stage == CardStage.TODAY.value,
                    Card.archived_at.is_(None),
                )
            )
        )
        if today:
            key = ",".join(sorted(card.id for card in today))
            if await self.eligible(session, "today", key):
                candidates.append(
                    ("today", key, f"Your Today focus contains {len(today)} Action(s).")
                )
            if local.hour >= 15 and await self.eligible(
                session, "stale_today", f"{key}:{local.date()}"
            ):
                candidates.append(
                    (
                        "stale_today",
                        f"{key}:{local.date()}",
                        "Your Today work is still open; choose what matters for the rest of the day.",
                    )
                )
        committed_effort = (
            await session.scalar(
                select(func.coalesce(func.sum(Card.effort_points), 0)).where(
                    Card.kind == CardKind.ACTION.value,
                    Card.archived_at.is_(None),
                    Card.effective_stage.in_([CardStage.SPRINT.value, CardStage.TODAY.value]),
                )
            )
            or 0
        )
        if profile.capacity_effort_points and committed_effort > profile.capacity_effort_points:
            key = f"{committed_effort}/{profile.capacity_effort_points}"
            if await self.eligible(session, "capacity", key):
                candidates.append(
                    (
                        "capacity",
                        key,
                        f"Committed effort is {key} EP, above your configured capacity.",
                    )
                )
        drifting_repeats = list(
            await session.scalars(
                select(Card).where(
                    Card.kind == CardKind.ACTION.value,
                    Card.repeatable.is_(True),
                    Card.archived_at.is_(None),
                    Card.effective_stage == CardStage.BACKLOG.value,
                )
            )
        )
        if drifting_repeats:
            key = ",".join(sorted(card.id for card in drifting_repeats))
            if await self.eligible(session, "repeat_drift", key):
                candidates.append(
                    (
                        "repeat_drift",
                        key,
                        f"{len(drifting_repeats)} repeatable Action(s) are waiting in Backlog.",
                    )
                )
        last_user_message = await session.scalar(
            select(TelegramMessage.created_at)
            .where(
                TelegramMessage.direction == "in",
                TelegramMessage.kind == MessageKind.DIALOGUE_USER.value,
            )
            .order_by(TelegramMessage.created_at.desc())
            .limit(1)
        )
        if last_user_message and current_utc - last_user_message >= timedelta(days=3):
            key = last_user_message.date().isoformat()
            if await self.eligible(session, "inactivity", key):
                candidates.append(
                    (
                        "inactivity",
                        key,
                        "It has been a few days. Would a small planning check-in help?",
                    )
                )
        workspace = await session.get(Workspace, 1)
        if workspace and workspace.active_sprint_id:
            sprint = await session.get(Sprint, workspace.active_sprint_id)
            days_elapsed = (local.date() - sprint.planned_start_date).days
            days_left = (sprint.planned_end_date - local.date()).days
            if days_elapsed >= 7 and await self.eligible(
                session, "sprint_midpoint", f"{sprint.id}:midpoint"
            ):
                candidates.append(
                    (
                        "sprint_midpoint",
                        f"{sprint.id}:midpoint",
                        f"Sprint {sprint.number} has reached its midpoint; review scope and energy.",
                    )
                )
            if days_left <= 1 and await self.eligible(
                session, "sprint_end", f"{sprint.id}:{days_left}"
            ):
                candidates.append(
                    (
                        "sprint_end",
                        f"{sprint.id}:{days_left}",
                        f"Sprint {sprint.number} is near its planned end; offer a finish or adjustment check-in.",
                    )
                )
        blocked_count = (
            await session.scalar(
                select(func.count(Card.id)).where(
                    Card.blocked.is_(True),
                    Card.archived_at.is_(None),
                    Card.effective_stage.notin_(
                        [CardStage.DONE.value, CardStage.CANCELLED.value]
                    ),
                )
            )
            or 0
        )
        if blocked_count and await self.eligible(session, "blocked", str(blocked_count)):
            candidates.append(
                (
                    "blocked",
                    str(blocked_count),
                    f"There are {blocked_count} blocked Card(s) to review.",
                )
            )
        active_values = list(await session.scalars(select(Value).where(Value.active.is_(True))))
        for value in active_values:
            aligned = await session.scalar(
                select(CardValue.card_id)
                .join(Card, Card.id == CardValue.card_id)
                .where(
                    CardValue.value_id == value.id,
                    Card.archived_at.is_(None),
                    Card.effective_stage.in_([CardStage.SPRINT.value, CardStage.TODAY.value]),
                )
                .limit(1)
            )
            if not aligned and await self.eligible(session, "value_neglected", value.id):
                candidates.append(
                    (
                        "value_neglected",
                        value.id,
                        f"Active Value '{value.name}' has no directly linked Sprint or Today Action.",
                    )
                )
        if workspace and workspace.mode == "planning":
            selected = (
                await session.scalar(
                    select(func.count(Card.id)).where(
                        Card.archived_at.is_(None),
                        Card.effective_stage.in_(
                            [
                                CardStage.SPRINT.value,
                                CardStage.TODAY.value,
                            ]
                        ),
                    )
                )
                or 0
            )
            if selected and await self.eligible(session, "planning", str(selected)):
                candidates.append(
                    ("planning", str(selected), "Your next Sprint selection is waiting for review.")
                )
        return candidates


async def run_scheduler(
    sessions: async_sessionmaker[AsyncSession],
    policy: ReminderPolicy,
    send_reminder,
    *,
    poll_seconds: float = 30.0,
) -> None:  # type: ignore[no-untyped-def]
    while True:
        async with sessions() as session:
            candidates = await policy.candidates(session)
            if candidates:
                kind, key, text = candidates[0]
                try:
                    sent = await send_reminder(text)
                except Exception:
                    logger.exception("Reminder generation or delivery failed")
                    sent = False
                if sent:
                    state = await session.get(ReminderState, kind)
                    if state is None:
                        state = ReminderState(kind=kind)
                        session.add(state)
                    state.last_sent_at = datetime.now(UTC)
                    state.dedupe_key = key
                    await session.commit()
        await asyncio.sleep(poll_seconds)
