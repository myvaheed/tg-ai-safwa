from __future__ import annotations

import pytest
from sqlalchemy import select

from safwa.domain import (
    DomainError,
    archive_subtree,
    card_checks,
    create_check,
    create_tag,
    create_value,
    delete_subtree,
    finish_action,
    pending_checks,
    resolve_check,
    toggle_check_tag,
    toggle_check_value,
    update_check_fields,
)
from safwa.domain import create_card as create_domain_card
from safwa.enums import CardStage, CheckOutcome
from safwa.models import Card, Check, CheckTag, CheckValue


async def create_action(session, **overrides):
    payload = {"title": "Action", "kind": "action", "stage": "today", "effort_points": 3}
    payload.update(overrides)
    return await create_domain_card(session, **payload)


async def test_new_check_is_pending_and_starts_its_own_series(sessions):
    async with sessions() as session:
        card = await create_action(session, title="Go to the market")
        check = await create_check(session, title="Milk", card_id=card.id)
        await session.commit()

        assert check.outcome is None
        assert check.resolved_at is None
        assert check.series_id == check.id
        assert [item.id for item in await pending_checks(session, card.id)] == [check.id]


async def test_done_is_gated_on_pending_checks_and_cancel_is_not(sessions):
    async with sessions() as session:
        card = await create_action(session, title="Go to the market")
        milk = await create_check(session, title="Milk", card_id=card.id)
        await create_check(session, title="Bread", card_id=card.id)
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
        first = await create_check(session, title="First", card_id=card.id)
        await create_check(session, title="Second", card_id=card.id)
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
        milk = await create_check(session, title="Milk", card_id=card.id)
        bread = await create_check(session, title="Bread", card_id=card.id)
        await session.commit()

        await finish_action(
            session,
            card.id,
            CardStage.DONE,
            check_outcomes={milk.id: CheckOutcome.PASSED, bread.id: "not_applicable"},
        )
        await session.commit()

        assert (await session.get(Card, card.id)).effective_stage == CardStage.DONE.value
        assert (await session.get(Check, milk.id)).outcome == CheckOutcome.PASSED.value
        assert (await session.get(Check, bread.id)).outcome == "not_applicable"
        assert (await session.get(Check, milk.id)).resolved_at is not None
        assert await pending_checks(session, card.id) == []


async def test_repeatable_check_spawns_one_successor_and_re_answer_does_not(sessions):
    async with sessions() as session:
        card = await create_action(session, title="Posture")
        check = await create_check(session, title="Posture straight?", card_id=card.id, repeatable=True)
        await session.commit()

        resolved, successor = await resolve_check(session, check.id, CheckOutcome.FAILED)
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
        check = await create_check(session, title="Posture", card_id=card.id, repeatable=True)
        await session.commit()

        _, successor = await resolve_check(session, check.id, CheckOutcome.PASSED)
        await session.commit()
        assert successor is not None

        # The series already has a live Pending row, so answering the original again
        # cannot add a second one.
        _, again = await resolve_check(session, check.id, CheckOutcome.FAILED)
        await session.commit()
        assert again is None
        live = await session.scalars(
            select(Check).where(Check.series_id == check.series_id, Check.outcome.is_(None))
        )
        assert len(list(live)) == 1


async def test_repeat_successor_card_gets_pending_check_copies_with_flags_intact(sessions):
    async with sessions() as session:
        value = await create_value(session, "Health")
        tag = await create_tag(session, "Home")
        card = await create_action(session, title="Go to the market", repeatable=True)
        check = await create_check(
            session,
            title="Milk",
            card_id=card.id,
            repeatable=True,
            value_ids={value.id},
            tag_ids={tag.id},
        )
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

        copied_values = await session.scalars(
            select(CheckValue.value_id).where(CheckValue.check_id == copy.id)
        )
        copied_tags = await session.scalars(
            select(CheckTag.tag_id).where(CheckTag.check_id == copy.id)
        )
        assert list(copied_values) == [value.id]
        assert list(copied_tags) == [tag.id]


async def test_repeat_successor_copies_one_row_per_series(sessions):
    async with sessions() as session:
        card = await create_action(session, title="Posture round", repeatable=True)
        check = await create_check(session, title="Posture", card_id=card.id, repeatable=True)
        await session.commit()

        # Answering inside the cycle leaves the original plus its live successor on the
        # same Card; the repeat clone must still produce exactly one row for the series.
        _, spawned = await resolve_check(session, check.id, CheckOutcome.PASSED)
        await session.commit()
        assert spawned is not None

        result = await finish_action(
            session, card.id, CardStage.DONE, check_outcomes={spawned.id: CheckOutcome.FAILED}
        )
        await session.commit()
        copies = await card_checks(session, result.successor_ids[0])
        assert len(copies) == 1
        assert copies[0].outcome is None


async def test_terminal_card_never_regains_a_pending_check(sessions):
    async with sessions() as session:
        card = await create_action(session)
        check = await create_check(session, title="Posture", card_id=card.id, repeatable=True)
        await session.commit()

        await finish_action(
            session, card.id, CardStage.DONE, check_outcomes={check.id: CheckOutcome.PASSED}
        )
        await session.commit()

        # Re-answering a Check on a closed Card must not resurrect a Pending row.
        _, successor = await resolve_check(session, check.id, CheckOutcome.FAILED)
        await session.commit()
        assert successor is None
        assert await pending_checks(session, card.id) == []


async def test_standalone_check_repeats_without_a_card(sessions):
    async with sessions() as session:
        check = await create_check(session, title="Posture straight?", repeatable=True)
        await session.commit()

        _, successor = await resolve_check(session, check.id, CheckOutcome.FAILED)
        await session.commit()
        assert successor is not None
        assert successor.card_id is None


async def test_archive_and_delete_cascade_to_checks(sessions):
    async with sessions() as session:
        card = await create_action(session)
        check = await create_check(session, title="Milk", card_id=card.id)
        await session.commit()

        await archive_subtree(session, card.id)
        await session.commit()
        assert (await session.get(Check, check.id)).archived_at is not None
        assert await pending_checks(session, card.id) == []

    async with sessions() as session:
        await delete_subtree(session, card.id)
        await session.commit()
        assert await session.get(Check, check.id) is None


async def test_check_field_and_link_editing(sessions):
    async with sessions() as session:
        value = await create_value(session, "Health")
        tag = await create_tag(session, "Home")
        check = await create_check(session, title="Posture")
        await session.commit()

        await update_check_fields(session, check.id, {"title": "Posture straight?", "repeatable": True})
        await session.commit()
        assert check.title == "Posture straight?"
        assert check.repeatable is True

        assert await toggle_check_value(session, check.id, value.id) is True
        assert await toggle_check_tag(session, check.id, tag.id) is True
        assert await toggle_check_value(session, check.id, value.id) is False
        await session.commit()

        with pytest.raises(DomainError, match="Check title cannot be empty"):
            await update_check_fields(session, check.id, {"title": "  "})
        with pytest.raises(DomainError, match="Unsupported Check fields"):
            await update_check_fields(session, check.id, {"outcome": "passed"})
