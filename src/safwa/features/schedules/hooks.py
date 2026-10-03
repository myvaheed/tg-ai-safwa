"""Compile only changed source; recover unfinished setup once an hour."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import timedelta

from sqlalchemy import select

from tg_agent_shell.foundation.changes import Committed, record_change
from tg_agent_shell.foundation.clock import utcnow
from tg_agent_shell.hooks.contracts import (
    Advise,
    HookSpec,
    OnCommitted,
    OnStarted,
    OnTick,
    Run,
    RunContext,
)

from ..cards.model import Card, CardStage
from ..cards.use_cases import CARD_TODAY
from ..checks.model import Check
from ..planning.api import refresh_schedule_commitment
from .api import SCHEDULE_CHANGED, SCHEDULE_UNCLEAR, assign_first, set_schedule, workspace_zone
from .model import ScheduleDefinition

logger = logging.getLogger(__name__)


async def changed(event: Committed):
    return (event.subject_id,)


async def compile_revision(id: int, context: RunContext):
    async with context.sessions() as session:
        definition = await session.get(ScheduleDefinition, id)
        if (
            not definition
            or definition.valid_until
            or definition.status not in {"pending", "error"}
        ):
            return
        model = Card if definition.type == "card" else Check
        if await session.scalar(select(model.id).where(model.schedule_id == id).limit(1)) is None:
            definition.valid_until = utcnow()
            await session.commit()
            return
        text, submitted_at, tz = (
            definition.source_text,
            definition.submitted_at,
            await workspace_zone(session),
        )
    try:
        rule, question = await context.resources.schedule_compiler.compile(text, submitted_at, tz)
        status = "ready" if rule else "needs_clarification"
    except Exception:
        logger.exception("Schedule %s could not be compiled", id)
        rule, question, status = None, None, "error"
    async with context.sessions() as session:
        definition = await session.get(ScheduleDefinition, id)
        if (
            not definition
            or definition.valid_until
            or definition.status not in {"pending", "error"}
        ):
            return
        definition.rule, definition.question, definition.status = rule, question, status
        model = Card if definition.type == "card" else Check
        entities = await session.scalars(select(model).where(model.schedule_id == id))
        for entity in entities:
            await assign_first(session, entity)
            if isinstance(entity, Card):
                await refresh_schedule_commitment(session, entity)
            if rule and isinstance(entity, Card) and entity.effective_stage == CardStage.TODAY.value:
                record_change(session, CARD_TODAY, entity.id)
        if question:
            record_change(session, SCHEDULE_UNCLEAR, id)
        await session.commit()


async def every(event):
    return (event,)


async def recover(event, context: RunContext):
    async with context.sessions() as session:
        for model in (Card, Check):
            entities = await session.scalars(
                select(model).where(model.schedule.is_not(None), model.schedule_id.is_(None))
            )
            for entity in entities:
                text, entity.schedule = entity.schedule, None
                await set_schedule(session, entity, text)
        await session.commit()
        ids = list(
            await session.scalars(
                select(ScheduleDefinition.id).where(
                    ScheduleDefinition.valid_until.is_(None),
                    ScheduleDefinition.status.in_(["pending", "error"]),
                )
            )
        )
    for id in ids:
        await compile_revision(id, context)


async def clarification(session, ids: Sequence[int]):
    rows = await session.scalars(
        select(ScheduleDefinition).where(
            ScheduleDefinition.id.in_(ids),
            ScheduleDefinition.valid_until.is_(None),
            ScheduleDefinition.status == "needs_clarification",
        )
    )
    questions = []
    for row in rows:
        model = Card if row.type == "card" else Check
        current = await session.scalar(
            select(model).where(model.schedule_id == row.id).order_by(model.id.desc()).limit(1)
        )
        if current is not None:
            questions.append(
                f"{row.type} #{current.id} '{current.title}', Schedule '{row.source_text}': {row.question}"
            )
    return (
        "Ask these scheduling questions. After the user answers, route workspace_mutator to update Schedule with the original timing and their answer.\n"
        + "\n".join(questions)
        if questions
        else None
    )


SCHEDULER_HOOK = HookSpec(
    name="schedules.compile",
    owner="schedules",
    on=(OnCommitted(SCHEDULE_CHANGED),),
    evaluate=changed,
    effect=Run(compile_revision),
    title="Scheduler",
    description="Compiles a changed Schedule once.",
)
SCHEDULE_RECOVERY_HOOK = HookSpec(
    name="schedules.recover",
    owner="schedules",
    on=(OnStarted(), OnTick(every=timedelta(hours=1))),
    evaluate=every,
    effect=Run(recover),
    title="Unfinished schedules",
    description="Recovers missing or failed setup each hour.",
)
SCHEDULE_CLARIFICATION_HOOK = HookSpec(
    name="schedules.clarify",
    owner="schedules",
    on=(OnCommitted(SCHEDULE_UNCLEAR),),
    evaluate=changed,
    effect=Advise(clarification),
    title="Schedule clarification",
    description="Asks for the missing detail once per revision.",
)
