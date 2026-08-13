from __future__ import annotations

import pytest
from sqlalchemy import select

from safwa.domain import (
    DomainError,
    archive_subtree,
    card_checks,
    check_card_ids,
    create_check,
    delete_subtree,
    finish_action,
    pending_checks,
    resolve_check,
    toggle_card_check,
    update_check_fields,
)
from safwa.domain import create_card as create_domain_card
from safwa.enums import CardStage, CheckOutcome
from safwa.models import Card, CardEvent, Check


async def create_action(session, **overrides):
    payload = {"title": "Action", "kind": "action", "stage": "today", "effort_points": 3}
    payload.update(overrides)
    return await create_domain_card(session, **payload)


async def linked_check(session, *card_ids, **kwargs):
    """Create a Check and attach it, which is always a Card-side write."""
    check = await create_check(session, **kwargs)
    for card_id in card_ids:
        await toggle_card_check(session, card_id, check.id)
    return check


async def test_new_check_is_pending_and_starts_its_own_series(sessions):
    async with sessions() as session:
        card = await create_action(session, title="Go to the market")
        check = await linked_check(session, card.id, title="Milk")
        await session.commit()

        assert check.outcome is None
        assert check.resolved_at is None
        assert check.series_id == check.id
        assert [item.id for item in await pending_checks(session, card.id)] == [check.id]


async def test_done_is_gated_on_pending_checks_and_cancel_is_not(sessions):
    async with sessions() as session:
        card = await create_action(session, title="Go to the market")
        milk = await linked_check(session, card.id, title="Milk")
        await linked_check(session, card.id, title="Bread")
        await session.commit()

        with pytest.raises(DomainError) as error:
            await finish_action(session, card.id, CardStage.DONE)
        assert f"#{milk.id} Milk" in str(error.value)
        assert "Bread" in str(error.value)

    async with sessions() as session:
        # Cancelling abandons the work, so unanswered Checks must not block it.
        await finish_action(session, card.id, CardStage.CANCELLED)
        await session.commit()
        refreshed = await session.get(Card, card.id)
        assert refreshed.effective_stage == CardStage.CANCELLED.value


async def test_partial_or_unknown_check_outcomes_are_rejected(sessions):
    async with sessions() as session:
        card = await create_action(session)
        first = await linked_check(session, card.id, title="First")
        await linked_check(session, card.id, title="Second")
        await session.commit()

        with pytest.raises(DomainError, match="Resolve these Pending Checks"):
            await finish_action(
                session, card.id, CardStage.DONE, check_outcomes={first.id: "passed"}
            )
        with pytest.raises(DomainError, match="not Pending on this Card"):
            await finish_action(session, card.id, CardStage.DONE, check_outcomes={999: "passed"})


async def test_finishing_resolves_checks_and_leaves_the_card_done(sessions):
    async with sessions() as session:
        card = await create_action(session)
        milk = await linked_check(session, card.id, title="Milk")
        bread = await linked_check(session, card.id, title="Bread")
        await session.commit()

        await finish_action(
            session,
            card.id,
            CardStage.DONE,
            check_outcomes={milk.id: CheckOutcome.PASSED, bread.id: "missed"},
        )
        await session.commit()

        assert (await session.get(Card, card.id)).effective_stage == CardStage.DONE.value
        assert (await session.get(Check, milk.id)).outcome == CheckOutcome.PASSED.value
        assert (await session.get(Check, bread.id)).outcome == "missed"
        assert (await session.get(Check, milk.id)).resolved_at is not None
        assert await pending_checks(session, card.id) == []


async def test_repeatable_check_spawns_one_successor_and_re_answer_does_not(sessions):
    async with sessions() as session:
        card = await create_action(session, title="Posture")
        check = await linked_check(session, card.id, title="Posture straight?", repeatable=True)
        await session.commit()

        resolved, successor = await resolve_check(session, check.id, CheckOutcome.MISSED)
        await session.commit()
        assert successor is not None
        assert successor.outcome is None
        assert successor.series_id == check.series_id
        assert successor.source_instance_id == check.id
        assert successor.repeatable is True
        first_resolved_at = resolved.resolved_at

        # Re-answering overwrites the outcome; it must not spawn again, and the
        # observation time must stay where the trend expects it.
        _, second = await resolve_check(session, check.id, CheckOutcome.PASSED)
        await session.commit()
        assert second is None
        refreshed = await session.get(Check, check.id)
        assert refreshed.outcome == CheckOutcome.PASSED.value
        assert refreshed.resolved_at == first_resolved_at
        assert len(await pending_checks(session, card.id)) == 1


async def test_only_one_pending_check_per_series(sessions):
    async with sessions() as session:
        card = await create_action(session)
        check = await linked_check(session, card.id, title="Posture", repeatable=True)
        await session.commit()

        _, successor = await resolve_check(session, check.id, CheckOutcome.PASSED)
        await session.commit()
        assert successor is not None

        # The series already has a live Pending row, so answering the original again
        # cannot add a second one.
        _, again = await resolve_check(session, check.id, CheckOutcome.MISSED)
        await session.commit()
        assert again is None
        live = await session.scalars(
            select(Check).where(Check.series_id == check.series_id, Check.outcome.is_(None))
        )
        assert len(list(live)) == 1


async def test_repeat_successor_card_gets_pending_check_copies_with_flags_intact(sessions):
    async with sessions() as session:
        card = await create_action(session, title="Go to the market", repeatable=True)
        check = await linked_check(session, card.id, title="Milk", repeatable=True)
        await session.commit()

        result = await finish_action(
            session, card.id, CardStage.DONE, check_outcomes={check.id: CheckOutcome.PASSED}
        )
        await session.commit()

        assert result.successor_ids
        successor_card_id = result.successor_ids[0]
        copies = await card_checks(session, successor_card_id)
        assert len(copies) == 1
        copy = copies[0]
        assert copy.outcome is None
        assert copy.title == "Milk"
        assert copy.repeatable is True
        assert copy.series_id == check.series_id
        # Closing the Card must not have spawned a successor on the Card itself, or the
        # Card would have been blocked again the moment it was finished.
        assert await pending_checks(session, card.id) == []
        # The copy belongs to the Card that repeated, not to the closed original.
        assert await check_card_ids(session, copy.id) == [successor_card_id]


async def test_repeat_successor_copies_one_row_per_series(sessions):
    async with sessions() as session:
        card = await create_action(session, title="Posture round", repeatable=True)
        check = await linked_check(session, card.id, title="Posture", repeatable=True)
        await session.commit()

        # Answering inside the cycle leaves the original plus its live successor on the
        # same Card; the repeat clone must still produce exactly one row for the series.
        _, spawned = await resolve_check(session, check.id, CheckOutcome.PASSED)
        await session.commit()
        assert spawned is not None

        result = await finish_action(
            session, card.id, CardStage.DONE, check_outcomes={spawned.id: CheckOutcome.MISSED}
        )
        await session.commit()
        copies = await card_checks(session, result.successor_ids[0])
        assert len(copies) == 1
        assert copies[0].outcome is None


async def test_terminal_card_never_regains_a_pending_check(sessions):
    async with sessions() as session:
        card = await create_action(session)
        check = await linked_check(session, card.id, title="Posture", repeatable=True)
        await session.commit()

        await finish_action(
            session, card.id, CardStage.DONE, check_outcomes={check.id: CheckOutcome.PASSED}
        )
        await session.commit()

        # Re-answering a Check on a closed Card must not resurrect a Pending row.
        _, successor = await resolve_check(session, check.id, CheckOutcome.MISSED)
        await session.commit()
        assert successor is None
        assert await pending_checks(session, card.id) == []


async def test_standalone_check_repeats_without_a_card(sessions):
    async with sessions() as session:
        check = await create_check(session, title="Posture straight?", repeatable=True)
        await session.commit()

        _, successor = await resolve_check(session, check.id, CheckOutcome.MISSED)
        await session.commit()
        assert successor is not None
        assert await check_card_ids(session, successor.id) == []


async def test_one_check_serves_several_cards(sessions):
    async with sessions() as session:
        market = await create_action(session, title="Go to the market")
        pharmacy = await create_action(session, title="Go to the pharmacy")
        check = await create_check(session, title="Take the tote bag")
        assert await toggle_card_check(session, market.id, check.id) is True
        assert await toggle_card_check(session, pharmacy.id, check.id) is True

        await session.commit()

        assert await check_card_ids(session, check.id) == sorted([market.id, pharmacy.id])
        assert [item.id for item in await pending_checks(session, market.id)] == [check.id]
        assert [item.id for item in await pending_checks(session, pharmacy.id)] == [check.id]

        # One answer satisfies every Card the Check hangs on вЂ” that is the point of sharing.
        await finish_action(
            session, market.id, CardStage.DONE, check_outcomes={check.id: CheckOutcome.PASSED}
        )
        await session.commit()
        assert await pending_checks(session, pharmacy.id) == []
        await finish_action(session, pharmacy.id, CardStage.DONE)
        await session.commit()
        assert (await session.get(Card, pharmacy.id)).effective_stage == CardStage.DONE.value


async def test_successor_is_linked_to_live_cards_only(sessions):
    async with sessions() as session:
        closed = await create_action(session, title="Closed")
        await finish_action(session, closed.id, CardStage.DONE)
        live = await create_action(session, title="Live")
        check = await linked_check(
            session, closed.id, live.id, title="Posture", repeatable=True
        )
        await session.commit()

        _, successor = await resolve_check(session, check.id, CheckOutcome.MISSED)
        await session.commit()

        # A terminal Card must never regain a Pending row, so the successor hangs on the
        # live Card alone.
        assert successor is not None
        assert await check_card_ids(session, successor.id) == [live.id]
        assert await pending_checks(session, closed.id) == []


async def test_archive_and_delete_keep_a_check_its_other_cards_still_need(sessions):
    async with sessions() as session:
        card = await create_action(session)
        other = await create_action(session, title="Other")
        check = await linked_check(session, card.id, title="Milk")
        shared = await linked_check(session, card.id, other.id, title="Tote bag")
        await session.commit()

        await archive_subtree(session, card.id)
        await session.commit()
        assert (await session.get(Check, check.id)).archived_at is not None
        assert (await session.get(Check, shared.id)).archived_at is None
        # Only the Check nothing else needs is archived with the subtree.
        assert [item.id for item in await pending_checks(session, card.id)] == [shared.id]
        assert [item.id for item in await pending_checks(session, other.id)] == [shared.id]

    async with sessions() as session:
        await delete_subtree(session, card.id)
        await session.commit()
        assert await session.get(Check, check.id) is None
        assert await session.get(Check, shared.id) is not None
        assert await check_card_ids(session, shared.id) == [other.id]


async def test_check_field_and_card_link_editing(sessions):
    async with sessions() as session:
        card = await create_action(session)
        check = await create_check(session, title="Posture")
        await session.commit()

        await update_check_fields(session, check.id, {"title": "Posture straight?", "repeatable": True})
        await session.commit()
        assert check.title == "Posture straight?"
        assert check.repeatable is True

        assert await toggle_card_check(session, card.id, check.id) is True
        assert await toggle_card_check(session, card.id, check.id) is False
        await session.commit()
        assert await check_card_ids(session, check.id) == []
        # The link is a Card relationship, so it lands in that Card's event log like the
        # Value and Tag links do.
        operations = set(
            await session.scalars(select(CardEvent.operation).where(CardEvent.card_id == card.id))
        )
        assert {"link_check", "unlink_check"} <= operations

        with pytest.raises(DomainError, match="Check title cannot be empty"):
            await update_check_fields(session, check.id, {"title": "  "})
        with pytest.raises(DomainError, match="Unsupported Check fields"):
            await update_check_fields(session, check.id, {"outcome": "passed"})
