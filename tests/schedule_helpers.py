"""Domain fixtures with the Scheduler's terminal result supplied explicitly."""

from safwa.features.cards.use_cases import create_card as domain_card
from safwa.features.checks.use_cases import create_check as domain_check
from safwa.features.reminders.api import parse_phrase, schedule_payload
from safwa.features.schedules.api import assign_first, workspace_zone


async def configure(session, entity):
    if not entity.schedule:
        return entity
    rule = {"kind": "after_completion"}
    if entity.schedule != "after completion":
        rule = {
            "kind": "fixed",
            "timing": schedule_payload(
                parse_phrase(
                    entity.schedule,
                    now=entity.schedule_record.submitted_at,
                    tz=await workspace_zone(session),
                )
            ),
        }
    entity.schedule_record.rule = rule
    entity.schedule_record.status = "ready"
    await assign_first(session, entity)
    await session.flush()
    return entity


async def create_card(session, **fields):
    return await configure(session, await domain_card(session, **fields))


async def create_check(session, **fields):
    return await configure(session, await domain_check(session, **fields))
