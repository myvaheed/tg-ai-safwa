"""Compile a changed Schedule after its commit; retry the unfinished ones each hour."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.foundation.changes import Committed, record_change
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
from .api import SCHEDULE_CHANGED, SCHEDULE_UNCLEAR, assign_first, schedule_target, workspace_zone
from .model import ScheduleDefinition

logger = logging.getLogger(__name__)

CLARIFICATION_REQUEST = (
    "These Schedules need an answer:\n{items}\n"
    "Ask the user in one message. When they answer, route to workspace_mutator to set the "
    "Schedule to the original text with their answer."
)


async def changed(event: Committed) -> tuple[int, ...]:
    return (event.subject_id,)


async def _pending(session: AsyncSession, definition_id: int) -> ScheduleDefinition | None:
    """The revision, while it is still the current one waiting for its rule."""
    definition = await session.get(ScheduleDefinition, definition_id)
    if definition is None or definition.valid_until or definition.status != "pending":
        return None
    return definition


async def _owner(session: AsyncSession, definition: ScheduleDefinition) -> Card | Check | None:
    model = Card if definition.type == "card" else Check
    return await session.scalar(select(model).where(model.schedule_id == definition.id))


async def compile_revision(definition_id: int, context: RunContext) -> None:
    async with context.sessions() as session:
        definition = await _pending(session, definition_id)
        if definition is None:
            return
        entity = await _owner(session, definition)
        text, submitted_at = definition.source_text, definition.submitted_at
        tz, target = await workspace_zone(session), schedule_target(entity)
    try:
        rule, question = await context.resources.schedule_compiler.compile(
            text, submitted_at, tz, target
        )
    except Exception:
        logger.exception("Schedule %s could not be compiled; recovery retries it", definition_id)
        return
    async with context.sessions() as session:
        # Edited while the model was reading it: the newer revision is compiled on its own.
        definition = await _pending(session, definition_id)
        if definition is None:
            return
        definition.rule, definition.question = rule, question
        definition.status = "ready" if rule else "needs_clarification"
        entity = await _owner(session, definition)
        await assign_first(session, entity)
        if isinstance(entity, Card):
            await refresh_schedule_commitment(session, entity)
            if rule and entity.effective_stage == CardStage.TODAY.value:
                record_change(session, CARD_TODAY, entity.id)
        if question:
            record_change(session, SCHEDULE_UNCLEAR, definition_id)
        await session.commit()


async def every(event: object) -> tuple[object, ...]:
    return (event,)


async def recover(event: object, context: RunContext) -> None:
    async with context.sessions() as session:
        ids = list(
            await session.scalars(
                select(ScheduleDefinition.id).where(
                    ScheduleDefinition.valid_until.is_(None),
                    ScheduleDefinition.status == "pending",
                )
            )
        )
    for definition_id in ids:
        await compile_revision(definition_id, context)


async def clarification(session: AsyncSession, ids: Sequence[int]) -> str | None:
    lines = []
    for definition in await session.scalars(
        select(ScheduleDefinition)
        .where(
            ScheduleDefinition.id.in_(ids),
            ScheduleDefinition.valid_until.is_(None),
            ScheduleDefinition.status == "needs_clarification",
        )
        .order_by(ScheduleDefinition.id)
    ):
        entity = await _owner(session, definition)
        kind = "Check" if isinstance(entity, Check) else entity.kind.capitalize()
        label = "Deadline" if schedule_target(entity) == "deadline" else "Schedule"
        lines.append(
            f"- #{entity.id} «{entity.title}» ({kind}), {label} «{definition.source_text}»: "
            f"{definition.question}"
        )
    return CLARIFICATION_REQUEST.format(items="\n".join(lines)) if lines else None


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
    description="Compiles each hour the Schedules not compiled yet.",
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
