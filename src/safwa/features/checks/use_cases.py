"""Writing, answering and ending a Check.

Three rules generate everything the Card lifecycle asks of this module:

* **R1** - a Card closes when every Check series on it was answered at least once, on
  this Card. `unobserved_series` is that question.
* **R2** - closing a Card deletes whatever is still Pending on it. `drop_pending_checks`.
* **R3** - reopening a Card puts each plain Check back to Pending and opens one fresh
  Pending instance of each repeating series. `reopen_checks`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...enums import ActorType
from ...foundation.clock import utcnow
from ...foundation.errors import DomainError
from ...foundation.workspace import bump_workspace
from ..cards.model import CardCheck
from ..values.api import Value
from ..values.model import CheckValue
from .model import Check, CheckOutcome


async def card_checks(session: AsyncSession, card_id: int) -> list[Check]:
    """Every Check on this Card, an archived one included.

    Archiving is a matter of sight, so an answer that was archived is still an answer and
    a series that was archived is still a series. R1, R2 and R3 read this, and so does the
    screen — which marks the archived ones instead of leaving them out.
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


async def unobserved_series(session: AsyncSession, card_id: int) -> list[Check]:
    """R1: the Pending Check of every series this Card has no answer for yet.

    An open instance that an answer on this Card opened belongs to the next cycle, so it
    does not hold the Card. That is what `source_instance_id` records, and it is why a
    reopened Card asks its repeating Checks again: the instance it opens comes from no
    answer at all.
    """
    answered: dict[int, set[int]] = {}
    open_instance: dict[int, Check] = {}
    for check in await card_checks(session, card_id):
        series_id = check.series_id or check.id
        if check.outcome is None:
            open_instance[series_id] = check
        else:
            answered.setdefault(series_id, set()).add(check.id)
    return [
        check
        for series_id, check in sorted(open_instance.items())
        if check.source_instance_id not in answered.get(series_id, set())
    ]


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
    del actor  # a Check keeps no event log of its own
    check = await session.get(Check, check_id)
    value = await session.get(Value, value_id)
    if check is None or check.archived_at is not None:
        raise DomainError("Check does not exist or is archived")
    if value is None:
        raise DomainError("Value does not exist")
    link = await session.get(CheckValue, {"check_id": check_id, "value_id": value_id})
    if link is None:
        session.add(CheckValue(check_id=check_id, value_id=value_id))
        linked = True
    else:
        await session.delete(link)
        linked = False
    check.version += 1
    await bump_workspace(session)
    return linked


async def create_check(session: AsyncSession, *, title: str, repeatable: bool = False) -> Check:
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
    check = Check(title=clean_title, repeatable=repeatable)
    session.add(check)
    await session.flush()
    await bump_workspace(session)
    return check


async def update_check_fields(session: AsyncSession, check_id: int, fields: dict[str, Any]) -> Check:
    check = await session.get(Check, check_id)
    if check is None or check.archived_at is not None:
        raise DomainError("Check does not exist or is archived")
    unknown = set(fields) - {"title", "repeatable"}
    if unknown:
        raise DomainError("Unsupported Check fields: " + ", ".join(sorted(unknown)))
    for name, value in fields.items():
        if name == "title":
            value = str(value).strip()
        if name == "title" and not value:
            raise DomainError("Check title cannot be empty")
        setattr(check, name, value)
    check.version += 1
    await bump_workspace(session)
    return check


async def archive_check(session: AsyncSession, check_id: int, archive: bool = True) -> Check:
    check = await session.get(Check, check_id)
    if check is None:
        raise DomainError("Check does not exist")
    if archive and check.outcome is None:
        raise DomainError("Only an answered Check can be archived")
    check.archived_at = utcnow() if archive else None
    check.version += 1
    await bump_workspace(session)
    return check


async def delete_check(session: AsyncSession, check_id: int) -> None:
    """Delete one Check outright, answered or not, with every link it carried."""
    check = await session.get(Check, check_id)
    if check is None:
        raise DomainError("Check does not exist")
    await _delete_checks(session, [check.id])
    await bump_workspace(session)


async def _delete_checks(session: AsyncSession, check_ids: list[int]) -> None:
    if not check_ids:
        return
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
        repeatable=source.repeatable,
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


async def _spawn_check_successor(session: AsyncSession, check: Check) -> Check | None:
    """Open the next instance of a repeating series, on the one Card the series is on."""
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
    return await _copy_check(session, check, series_id, await check_card_id(session, check.id))


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
    check.outcome = resolved.value
    check.resolved_by = actor.value
    if was_pending:
        # resolved_at is the observation time the trend is keyed on, so a later
        # correction must not move the data point; updated_at carries that edit.
        check.resolved_at = utcnow()
    check.version += 1
    if not (was_pending and spawn and check.repeatable):
        return None
    return await _spawn_check_successor(session, check)


async def resolve_check(
    session: AsyncSession,
    check_id: int,
    outcome: CheckOutcome | str,
    *,
    actor: ActorType = ActorType.USER_UI,
) -> tuple[Check, Check | None]:
    """Answer one Check; only the first answer spawns a repeatable successor."""
    check = await session.get(Check, check_id)
    if check is None or check.archived_at is not None:
        raise DomainError("Check does not exist or is archived")
    successor = await apply_check_outcome(session, check, outcome, actor, spawn=True)
    await bump_workspace(session)
    return check, successor


def check_resolutions(
    pending: list[Check],
    unobserved: list[Check],
    outcomes: dict[int, CheckOutcome | str] | None,
) -> dict[int, CheckOutcome]:
    """R1 as a gate: every unobserved series needs an answer, and nothing else is taken."""
    supplied = {int(key): CheckOutcome(value) for key, value in (outcomes or {}).items()}
    unknown = set(supplied) - {check.id for check in pending}
    if unknown:
        raise DomainError(
            "These Checks are not Pending on this Card: "
            + ", ".join(f"#{check_id}" for check_id in sorted(unknown))
        )
    missing = [check for check in unobserved if check.id not in supplied]
    if missing:
        raise DomainError(
            "Resolve these Pending Checks before finishing the Card: "
            + ", ".join(f"#{check.id} {check.title}" for check in missing)
        )
    return supplied


async def drop_pending_checks(session: AsyncSession, card_id: int) -> None:
    """R2: closing a Card deletes whatever is still Pending on it.

    The Values a Pending instance carries were handed to it when the cycle before it was
    answered, so they go back to that answered instance rather than out with the row.
    """
    by_series: dict[int, list[Check]] = {}
    for check in await card_checks(session, card_id):
        by_series.setdefault(check.series_id or check.id, []).append(check)
    doomed: list[int] = []
    for _, instances in sorted(by_series.items()):
        open_instances = [check for check in instances if check.outcome is None]
        if not open_instances:
            continue
        answered = [check for check in instances if check.outcome is not None]
        keeper = answered[-1] if answered else None
        for check in open_instances:
            if keeper is not None:
                held = set(await check_value_ids(session, keeper.id))
                for value_id in await check_value_ids(session, check.id):
                    if value_id not in held:
                        session.add(CheckValue(check_id=keeper.id, value_id=value_id))
            doomed.append(check.id)
    await _delete_checks(session, doomed)


async def clone_checks_for_successor(
    session: AsyncSession, card_id: int, successor_id: int
) -> None:
    """R2's other half: one Pending copy of each Check series onto a repeat successor.

    Grouping by series matters: a Card that answered a repeating Check inside this cycle
    has more than one instance of it, and copying each would put two Pending rows of one
    series on the new Card.
    """
    latest: dict[int, Check] = {}
    for check in await card_checks(session, card_id):
        latest[check.series_id or check.id] = check
    for series_id, check in sorted(latest.items()):
        await _copy_check(session, check, series_id, successor_id)


async def reopen_checks(session: AsyncSession, card_id: int) -> None:
    """R3: a plain Check goes back to Pending; a repeating series opens the next instance.

    A plain Check is *the* observation of this Card, so reopening the work reopens the
    question. A repeating answer belongs to a cycle that is over, so the Card asks the
    next one instead of unsaying the last.
    """
    by_series: dict[int, list[Check]] = {}
    for check in await card_checks(session, card_id):
        by_series.setdefault(check.series_id or check.id, []).append(check)
    for series_id, series in sorted(by_series.items()):
        newest = series[-1]
        if newest.repeatable:
            if any(item.outcome is None and item.archived_at is None for item in series):
                continue
            fresh = await _copy_check(session, newest, series_id, card_id)
            # The reopened Card asks the question again rather than continuing the answer
            # it gave last time, which is what makes the series unobserved here again.
            fresh.source_instance_id = None
            continue
        for check in series:
            check.outcome = None
            check.resolved_at = None
            check.resolved_by = None
            # A Pending Check is never archived, so a reopened one comes back out.
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
    *,
    gated: bool,
) -> dict[int, Any]:
    """The answers this completion needs, refused before anything is written.

    `gated` is Done: every series with no answer on this Card has to be answered now.
    Cancelling abandons the work, so it asks for nothing.
    """
    pending = await pending_checks(session, card_id)
    unobserved = await unobserved_series(session, card_id) if gated else []
    return check_resolutions(pending, unobserved, outcomes)


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
    await drop_pending_checks(session, card_id)
