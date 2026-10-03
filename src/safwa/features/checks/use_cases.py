"""Writing, answering and ending a Check.

Linked Checks gate Done and reset when their ordinary Card reopens.
Independent scheduled Checks open successors according to their compiled rule.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.foundation.changes import record_change
from tg_agent_shell.foundation.clock import utcnow
from tg_agent_shell.foundation.errors import DomainError

from ...enums import ActorType
from ...foundation.log_events import CREATE, DELETE, UPDATE, record_log_event, snapshot
from ...foundation.workspace import bump_workspace
from ..cards.model import CardCheck
from ..schedules.api import (
    close_deleted_schedules,
    prepare_occurrence,
    set_schedule,
    successor_slot,
)
from ..values.api import Value
from ..values.model import CheckValue
from .model import Check, CheckOutcome

# The changes a hook may follow up on, however they were saved: a Check was created, answered,
# or answered Missed. The next instance of a repeating series is the system's, and is created
# without `CHECK_CREATED`.
CHECK_CREATED = "check.created"
CHECK_ANSWERED = "check.answered"
CHECK_MISSED = "check.missed"


async def card_checks(session: AsyncSession, card_id: int) -> list[Check]:
    """Every Check on this Card, an archived one included.

    Archiving is a matter of sight, so an answer that was archived is still an answer and
    a series that was archived is still a series. Completion and reopening read this, and
    so does the screen — which marks the archived ones instead of leaving them out.
    """
    return list(
        await session.scalars(
            select(Check)
            .join(CardCheck, CardCheck.check_id == Check.id)
            .where(CardCheck.card_id == card_id)
            .order_by(Check.id)
        )
    )


async def pending_checks(session: AsyncSession, card_id: int) -> list[Check]:
    """Pending is derived, never stored: a Check that has no outcome yet."""
    return list(
        await session.scalars(
            select(Check)
            .join(CardCheck, CardCheck.check_id == Check.id)
            .where(CardCheck.card_id == card_id, Check.outcome.is_(None))
            .order_by(Check.id)
        )
    )


async def check_card_id(session: AsyncSession, check_id: int) -> int | None:
    """The one Card a Check hangs on, or None."""
    return await session.scalar(select(CardCheck.card_id).where(CardCheck.check_id == check_id))


async def check_value_ids(session: AsyncSession, check_id: int) -> list[int]:
    return sorted(
        await session.scalars(select(CheckValue.value_id).where(CheckValue.check_id == check_id))
    )


async def toggle_check_value(
    session: AsyncSession, check_id: int, value_id: int, *, actor: ActorType = ActorType.USER_UI
) -> bool:
    """Put a Value on a Check or take it off, and say whether it is on now."""
    check = await session.get(Check, check_id)
    value = await session.get(Value, value_id)
    if check is None or check.archived_at is not None:
        raise DomainError("Check does not exist or is archived")
    if value is None:
        raise DomainError("Value does not exist")
    link = await session.get(CheckValue, {"check_id": check_id, "value_id": value_id})
    before = snapshot(check)
    if link is None:
        session.add(CheckValue(check_id=check_id, value_id=value_id))
        operation, linked = "link_value", True
    else:
        await session.delete(link)
        operation, linked = "unlink_value", False
    check.version += 1
    await _record(session, check, operation, actor, before)
    await bump_workspace(session)
    return linked


async def create_check(
    session: AsyncSession,
    *,
    title: str,
    schedule: str | None = None,
    actor: ActorType = ActorType.USER_UI,
) -> Check:
    """Create one Pending Check, attached to nothing.

    Linking is a Card action: `create_card(check_ids=...)` or `toggle_card_check`. Keeping
    it out of here leaves exactly one write path for the link, so every attach lands in the
    Card's event log.
    """
    clean_title = title.strip()
    if not clean_title:
        raise DomainError("Check title cannot be empty")
    # `series_id` stays null until a second instance exists: a Check that never repeated
    # is its own series, and `COALESCE(series_id, id)` is what says so, everywhere.
    check = Check(title=clean_title)
    session.add(check)
    await session.flush()
    await set_schedule(session, check, schedule)
    await _record(session, check, CREATE, actor)
    record_change(session, CHECK_CREATED, check.id)
    await bump_workspace(session)
    return check


async def update_check_fields(
    session: AsyncSession,
    check_id: int,
    fields: dict[str, Any],
    *,
    actor: ActorType = ActorType.USER_UI,
) -> Check:
    check = await session.get(Check, check_id)
    if check is None or check.archived_at is not None:
        raise DomainError("Check does not exist or is archived")
    unknown = set(fields) - {"title", "schedule"}
    if unknown:
        raise DomainError("Unsupported Check fields: " + ", ".join(sorted(unknown)))
    before = snapshot(check)
    for name, value in fields.items():
        if name == "title":
            value = str(value).strip()
        if name == "title" and not value:
            raise DomainError("Check title cannot be empty")
        if name == "schedule":
            if value and await check_card_id(session, check.id) is not None:
                raise DomainError("A Check with its own Schedule must stay independent")
            await set_schedule(session, check, value)
        else:
            setattr(check, name, value)
    check.version += 1
    await _record(session, check, UPDATE, actor, before)
    await bump_workspace(session)
    return check


async def archive_check(
    session: AsyncSession,
    check_id: int,
    archive: bool = True,
    *,
    actor: ActorType = ActorType.USER_UI,
) -> Check:
    check = await session.get(Check, check_id)
    if check is None:
        raise DomainError("Check does not exist")
    if archive and check.outcome is None:
        raise DomainError("Only an answered Check can be archived")
    before = snapshot(check)
    check.archived_at = utcnow() if archive else None
    check.version += 1
    await _record(session, check, "archive" if archive else "restore", actor, before)
    await bump_workspace(session)
    return check


async def delete_check(session: AsyncSession, check_id: int, *, actor: ActorType = ActorType.USER_UI) -> None:
    """Delete one Check outright, answered or not, with every link it carried."""
    check = await session.get(Check, check_id)
    if check is None:
        raise DomainError("Check does not exist")
    await _record(session, check, DELETE, actor, snapshot(check))
    await _delete_checks(session, [check.id])
    await bump_workspace(session)


async def _delete_checks(session: AsyncSession, check_ids: list[int]) -> None:
    if not check_ids:
        return
    await close_deleted_schedules(session, Check, check_ids)
    await session.execute(delete(CheckValue).where(CheckValue.check_id.in_(check_ids)))
    await session.execute(delete(CardCheck).where(CardCheck.check_id.in_(check_ids)))
    await session.execute(delete(Check).where(Check.id.in_(check_ids)))


async def _copy_check(
    session: AsyncSession, source: Check, series_id: int, card_id: int | None
) -> Check:
    """Open the next instance of a series, taking over what the source was measuring.

    A Value is carried by the Check the owner is still answering and never by a pile of
    finished ones, so it moves rather than being copied. Every path that opens the next
    instance comes through here — the answer inside a cycle, the next cycle's Card, and
    reopening — so none of them can carry the series on and leave the Values behind.

    A second instance is also what makes the series real, so the source is stamped with it
    here: before this moment its null `series_id` was the whole statement that it was alone.
    """
    source.series_id = series_id
    successor = Check(
        title=source.title,
        schedule=source.schedule,
        schedule_record=source.schedule_record,
        period_start=source.period_start,
        series_id=series_id,
        source_instance_id=source.id,
    )
    session.add(successor)
    await session.flush()
    if card_id is not None:
        session.add(CardCheck(card_id=card_id, check_id=successor.id))
    for value_id in await check_value_ids(session, source.id):
        session.add(CheckValue(check_id=successor.id, value_id=value_id))
    await session.execute(delete(CheckValue).where(CheckValue.check_id == source.id))
    return successor


async def _spawn_check_successor(
    session: AsyncSession, check: Check, slot: datetime | None
) -> Check | None:
    """Open one independent instance of the scheduled series."""
    series_id = check.series_id or check.id
    live_in_series = await session.scalar(
        select(func.count())
        .select_from(Check)
        .where(
            Check.series_id == series_id,
            Check.outcome.is_(None),
            Check.archived_at.is_(None),
        )
    )
    if live_in_series:
        return None
    successor = await _copy_check(session, check, series_id, None)
    successor.period_start = slot
    return successor


async def apply_check_outcome(
    session: AsyncSession,
    check: Check,
    outcome: CheckOutcome | str,
    actor: ActorType,
    *,
    spawn: bool,
) -> Check | None:
    resolved = CheckOutcome(outcome)
    was_pending = check.outcome is None
    if was_pending:
        await prepare_occurrence(session, check)
    before = snapshot(check)
    check.outcome = resolved.value
    check.resolved_by = actor.value
    record_change(session, CHECK_ANSWERED, check.id)
    if resolved is CheckOutcome.MISSED:
        record_change(session, CHECK_MISSED, check.id)
    if was_pending:
        # resolved_at is the observation time the trend is keyed on, so a later
        # correction must not move the data point; updated_at carries that edit.
        check.resolved_at = utcnow()
    check.version += 1
    await _record(session, check, resolved.value, actor, before)
    if not (was_pending and spawn):
        return None
    repeats, slot = await successor_slot(session, check)
    return await _spawn_check_successor(session, check, slot) if repeats else None


async def _record(
    session: AsyncSession,
    check: Check,
    operation: str,
    actor: ActorType,
    before: dict[str, Any] | None = None,
) -> None:
    await record_log_event(session, "check", check, check.title, operation, actor, before)


async def resolve_check(
    session: AsyncSession,
    check_id: int,
    outcome: CheckOutcome | str,
    *,
    actor: ActorType = ActorType.USER_UI,
) -> tuple[Check, Check | None]:
    """Answer one Check; only the first answer spawns a schedule successor."""
    check = await session.get(Check, check_id)
    if check is None or check.archived_at is not None:
        raise DomainError("Check does not exist or is archived")
    successor = await apply_check_outcome(session, check, outcome, actor, spawn=True)
    await bump_workspace(session)
    return check, successor


def check_resolutions(
    pending: list[Check],
    outcomes: dict[int, CheckOutcome | str] | None,
) -> dict[int, CheckOutcome]:
    """Every Pending linked Check needs an answer, and nothing else is taken."""
    supplied = {int(key): CheckOutcome(value) for key, value in (outcomes or {}).items()}
    unknown = set(supplied) - {check.id for check in pending}
    if unknown:
        raise DomainError(
            "These Checks are not Pending on this Card: "
            + ", ".join(f"#{check_id}" for check_id in sorted(unknown))
        )
    missing = [check for check in pending if check.id not in supplied]
    if missing:
        raise DomainError(
            "Resolve these Pending Checks before finishing the Card: "
            + ", ".join(f"#{check.id} {check.title}" for check in missing)
        )
    return supplied


async def clone_checks_for_successor(session: AsyncSession, card_id: int, successor_id: int) -> None:
    """A recurring Action carries each linked plain Check into its next cycle."""
    for check in await card_checks(session, card_id):
        await _copy_check(session, check, check.series_id or check.id, successor_id)


async def reopen_checks(session: AsyncSession, card_id: int) -> None:
    """The same linked observations are asked again when their ordinary Card reopens."""
    for check in await card_checks(session, card_id):
        check.outcome = None
        check.resolved_at = None
        check.resolved_by = None
        check.archived_at = None
        check.version += 1


async def delete_checks_of_cards(session: AsyncSession, card_ids: list[int]) -> None:
    """Delete the Checks on these Cards, keeping the ones that carry a Value.

    A Check with no Value is a question about that Card and nothing else. One that carries
    a Value is also a measurement of that Value, so it stays, on no Card.
    """
    if not card_ids:
        return
    linked = list(
        await session.scalars(select(CardCheck.check_id).where(CardCheck.card_id.in_(card_ids)))
    )
    kept = set(
        await session.scalars(select(CheckValue.check_id).where(CheckValue.check_id.in_(linked)))
    )
    await _delete_checks(session, [check_id for check_id in linked if check_id not in kept])
    await session.execute(delete(CardCheck).where(CardCheck.card_id.in_(card_ids)))


async def archive_settled_checks(session: AsyncSession, cutoff: datetime) -> list[int]:
    """Archive every answered Check whose observation is older than the cutoff."""
    stale = list(
        await session.scalars(
            select(Check).where(
                Check.archived_at.is_(None),
                Check.outcome.is_not(None),
                Check.resolved_at.is_not(None),
                Check.resolved_at <= cutoff,
            )
        )
    )
    stamp = utcnow()
    for check in stale:
        check.archived_at = stamp
        check.version += 1
    return [check.id for check in stale]


async def require_check_answers(
    session: AsyncSession,
    card_id: int,
    outcomes: dict[int, Any] | None,
) -> dict[int, Any]:
    """The answers this completion needs, refused before anything is written.

    Every Pending linked Check has to be answered before this Card is Done.
    """
    pending = await pending_checks(session, card_id)
    return check_resolutions(pending, outcomes)


async def settle_checks(
    session: AsyncSession,
    card_id: int,
    resolutions: dict[int, Any],
    *,
    actor: ActorType,
) -> None:
    """Write the answers a closing Card gave, then let go of what is still Pending."""
    by_id = {check.id: check for check in await pending_checks(session, card_id)}
    for check_id, outcome in sorted(resolutions.items()):
        # The Card closing is what carries the series on, so no successor opens here.
        await apply_check_outcome(session, by_id[check_id], outcome, actor, spawn=False)
