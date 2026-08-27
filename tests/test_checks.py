from __future__ import annotations

import pytest
from sqlalchemy import select

from safwa.ai.contracts import CheckToolInput
from safwa.domain import (
    DomainError,
    archive_check,
    archive_settled_items,
    card_checks,
    check_card_id,
    check_value_ids,
    create_check,
    create_value,
    delete_check,
    finish_action,
    finish_sprint,
    is_closed_repeat,
    live_repeat_instance_id,
    move_card,
    pending_checks,
    resolve_check,
    start_sprint,
    title_marks,
    toggle_card_check,
    toggle_check_value,
    unobserved_series,
    update_check_fields,
)
from safwa.domain import create_card as create_domain_card
from safwa.features.cards.model import CardStage
from safwa.features.checks.model import CheckOutcome
from safwa.models import Card, CardEvent, Check


async def create_action(session, **overrides):
    payload = {"title": "Action", "kind": "action", "stage": "today", "effort_points": 3}
    payload.update(overrides)
    return await create_domain_card(session, **payload)


async def linked_check(session, card_id, **kwargs):
    """Create a Check and attach it, which is always a Card-side write."""
    check = await create_check(session, **kwargs)
    if card_id is not None:
        await toggle_card_check(session, card_id, check.id)
    return check


async def run_sprints(session, count: int) -> None:
    """Start and end `count` Sprints, which is the clock automatic archiving runs on."""
    for index in range(count):
        await start_sprint(session, success_criteria=f"Sprint {index + 1}")
        await finish_sprint(session)
        await session.commit()


async def test_a_check_is_an_observation_not_a_task(sessions):
    """CH-WRITE-001 — tests/brd/checks.feature"""
    async with sessions() as session:
        check = await create_check(session, title="Slept seven hours?", repeatable=True)
        await session.commit()

        assert check.title == "Slept seven hours?"
        assert check.repeatable is True
        # Nothing on a Check says how big it is, where it sits, or how much it matters.
        for field in ("effort_points", "stage", "effective_stage", "priority", "hard_time"):
            assert not hasattr(check, field)

    # The model reaches a Check through one contract, and that contract says the same.
    assert set(CheckToolInput.model_fields) >= {"mode", "id", "title", "repeatable"}
    assert not {"effort_points", "stage", "priority"} & set(CheckToolInput.model_fields)


async def test_a_check_title_is_never_blank(sessions):
    """CH-WRITE-002 — tests/brd/checks.feature"""
    async with sessions() as session:
        check = await create_check(session, title="  Posture straight?  ")
        await session.commit()
        assert check.title == "Posture straight?"

        with pytest.raises(DomainError, match="Check title cannot be empty"):
            await create_check(session, title="   ")
        with pytest.raises(DomainError, match="Check title cannot be empty"):
            await update_check_fields(session, check.id, {"title": "  "})
        with pytest.raises(DomainError, match="Unsupported Check fields"):
            await update_check_fields(session, check.id, {"outcome": "passed"})


async def test_a_check_belongs_to_one_card_or_to_none(sessions):
    """CH-LINK-003 — tests/brd/checks.feature"""
    async with sessions() as session:
        market = await create_action(session, title="Go to the market")
        pharmacy = await create_action(session, title="Go to the pharmacy")
        check = await create_check(session, title="Posture straight?")
        assert await toggle_card_check(session, market.id, check.id) is True
        await session.commit()

        with pytest.raises(DomainError, match="already belongs to Card"):
            await toggle_card_check(session, pharmacy.id, check.id)

        # Moving it is taking it off the first Card and putting it on the other.
        assert await toggle_card_check(session, market.id, check.id) is False
        assert await toggle_card_check(session, pharmacy.id, check.id) is True
        await session.commit()
        assert await check_card_id(session, check.id) == pharmacy.id

        # A Check on no Card is a Check in good standing.
        loose = await create_check(session, title="Weighed myself?")
        await session.commit()
        assert await check_card_id(session, loose.id) is None
        # The link is a Card relationship, so it lands in that Card's event log.
        operations = set(
            await session.scalars(select(CardEvent.operation).where(CardEvent.card_id == market.id))
        )
        assert {"link_check", "unlink_check"} <= operations


async def test_a_check_with_no_answer_is_pending(sessions):
    """CH-ANSWER-004 — tests/brd/checks.feature"""
    async with sessions() as session:
        card = await create_action(session, title="Go to the market")
        check = await linked_check(session, card.id, title="Milk")
        await session.commit()

        # Pending is derived from a null outcome, so nothing was stored to say so.
        assert check.outcome is None
        assert check.resolved_at is None
        # A Check that never repeated is its own series, and a null `series_id` says so.
        assert check.series_id is None
        assert [item.id for item in await pending_checks(session, card.id)] == [check.id]


async def test_answering_a_check_again_only_changes_the_answer(sessions):
    """CH-ANSWER-005 — tests/brd/checks.feature"""
    async with sessions() as session:
        card = await create_action(session, title="Posture")
        check = await linked_check(session, card.id, title="Posture straight?")
        await session.commit()

        answered, successor = await resolve_check(session, check.id, CheckOutcome.PASSED)
        await session.commit()
        first_resolved_at = answered.resolved_at
        assert successor is None

        _, again = await resolve_check(session, check.id, CheckOutcome.MISSED)
        await session.commit()

        refreshed = await session.get(Check, check.id)
        assert refreshed.outcome == CheckOutcome.MISSED.value
        # The observation time is what the trend is keyed on, so a correction cannot move it.
        assert refreshed.resolved_at == first_resolved_at
        assert again is None
        assert len(list(await session.scalars(select(Check)))) == 1
        assert (await session.get(Card, card.id)).effective_stage == CardStage.TODAY.value


async def test_a_card_cannot_be_done_with_an_unanswered_check(sessions):
    """CH-GATE-006 — tests/brd/checks.feature"""
    async with sessions() as session:
        card = await create_action(session, title="Go to the market")
        milk = await linked_check(session, card.id, title="Milk")
        await linked_check(session, card.id, title="Bread")
        await session.commit()

        with pytest.raises(DomainError) as error:
            await finish_action(session, card.id, CardStage.DONE)
        assert f"#{milk.id} Milk" in str(error.value)
        assert "Bread" in str(error.value)
        assert (await session.get(Card, card.id)).effective_stage == CardStage.TODAY.value
        assert (await session.get(Check, milk.id)).outcome is None

        with pytest.raises(DomainError, match="Resolve these Pending Checks"):
            await finish_action(
                session, card.id, CardStage.DONE, check_outcomes={milk.id: "passed"}
            )
        with pytest.raises(DomainError, match="not Pending on this Card"):
            await finish_action(session, card.id, CardStage.DONE, check_outcomes={999: "passed"})

    async with sessions() as session:
        # Cancelling abandons the work, so an unanswered Check must not block it.
        await finish_action(session, card.id, CardStage.CANCELLED)
        await session.commit()
        assert (await session.get(Card, card.id)).effective_stage == CardStage.CANCELLED.value


async def test_finishing_a_card_answers_its_checks_at_the_same_time(sessions):
    """CH-GATE-007 — tests/brd/checks.feature"""
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


async def test_a_repeating_check_needs_one_answer_before_its_card_can_close(sessions):
    """CH-GATE-008 — tests/brd/checks.feature"""
    async with sessions() as session:
        card = await create_action(session, title="Posture")
        check = await linked_check(session, card.id, title="Posture straight?", repeatable=True)
        await session.commit()

        # Never answered on this Card, so it holds the Card.
        with pytest.raises(DomainError) as error:
            await finish_action(session, card.id, CardStage.DONE)
        assert f"#{check.id} Posture straight?" in str(error.value)

        _, successor = await resolve_check(session, check.id, CheckOutcome.PASSED)
        await session.commit()
        assert successor is not None
        # The open instance belongs to the next cycle, not to this completion.
        assert await unobserved_series(session, card.id) == []
        await finish_action(session, card.id, CardStage.DONE)
        await session.commit()
        assert (await session.get(Card, card.id)).effective_stage == CardStage.DONE.value


async def test_an_answer_on_the_previous_action_does_not_close_the_next_one(sessions):
    """CH-GATE-008 — tests/brd/checks.feature"""
    async with sessions() as session:
        card = await create_action(session, title="Posture", repeatable=True)
        check = await linked_check(session, card.id, title="Posture straight?", repeatable=True)
        await session.commit()

        result = await finish_action(
            session, card.id, CardStage.DONE, check_outcomes={check.id: CheckOutcome.PASSED}
        )
        await session.commit()
        successor_card_id = result.successor_ids[0]

        with pytest.raises(DomainError, match="Posture straight?"):
            await finish_action(session, successor_card_id, CardStage.DONE)


async def test_answering_a_repeating_check_opens_the_next_one(sessions):
    """CH-REPEAT-009 — tests/brd/checks.feature"""
    async with sessions() as session:
        card = await create_action(session, title="Posture")
        check = await linked_check(session, card.id, title="Posture straight?", repeatable=True)
        await session.commit()

        answered, successor = await resolve_check(session, check.id, CheckOutcome.MISSED)
        await session.commit()

        assert answered.outcome == CheckOutcome.MISSED.value
        assert successor is not None
        assert successor.outcome is None
        assert successor.title == "Posture straight?"
        assert successor.repeatable is True
        assert successor.series_id == check.series_id
        assert successor.source_instance_id == check.id
        assert await check_card_id(session, successor.id) == card.id

        # The series already has one open instance, so answering the old one adds none.
        _, again = await resolve_check(session, check.id, CheckOutcome.PASSED)
        await session.commit()
        assert again is None
        assert len(await pending_checks(session, card.id)) == 1

        assert is_closed_repeat(answered) is True
        assert is_closed_repeat(successor) is False
        assert await live_repeat_instance_id(session, answered) == successor.id


async def test_a_repeating_check_on_no_card_opens_the_next_one_just_the_same(sessions):
    """CH-REPEAT-009 — tests/brd/checks.feature"""
    async with sessions() as session:
        check = await create_check(session, title="Posture straight?", repeatable=True)
        await session.commit()

        _, successor = await resolve_check(session, check.id, CheckOutcome.MISSED)
        await session.commit()

        assert successor is not None
        assert await check_card_id(session, successor.id) is None


async def test_closing_a_card_deletes_its_unanswered_check(sessions):
    """CH-CLOSE-010 — tests/brd/checks.feature"""
    async with sessions() as session:
        card = await create_action(session, title="Posture")
        check = await linked_check(session, card.id, title="Posture straight?", repeatable=True)
        await session.commit()

        _, successor = await resolve_check(session, check.id, CheckOutcome.PASSED)
        await session.commit()
        successor_id = successor.id

        await finish_action(session, card.id, CardStage.DONE)
        await session.commit()

        assert (await session.get(Card, card.id)).effective_stage == CardStage.DONE.value
        assert await session.get(Check, successor_id) is None
        # The answered instance is the record of the cycle, so it stays on the Card.
        assert [item.id for item in await card_checks(session, card.id)] == [check.id]


async def test_a_repeating_card_gives_fresh_checks_to_the_next_one(sessions):
    """CH-CLOSE-011 — tests/brd/checks.feature"""
    async with sessions() as session:
        card = await create_action(session, title="Go to the market", repeatable=True)
        plain = await linked_check(session, card.id, title="Milk")
        repeating = await linked_check(session, card.id, title="Posture", repeatable=True)
        await session.commit()

        result = await finish_action(
            session,
            card.id,
            CardStage.DONE,
            check_outcomes={plain.id: CheckOutcome.PASSED, repeating.id: CheckOutcome.PASSED},
        )
        await session.commit()

        successor_card_id = result.successor_ids[0]
        copies = await card_checks(session, successor_card_id)
        assert sorted(copy.title for copy in copies) == ["Milk", "Posture"]
        assert all(copy.outcome is None for copy in copies)
        assert {copy.repeatable for copy in copies} == {False, True}
        assert {copy.series_id for copy in copies} == {plain.series_id, repeating.series_id}
        # The answered instances stay with the Action that closed.
        assert sorted(item.id for item in await card_checks(session, card.id)) == sorted(
            [plain.id, repeating.id]
        )
        assert await pending_checks(session, card.id) == []
        # And the successor cannot close until each of them has been answered once here.
        with pytest.raises(DomainError, match="Resolve these Pending Checks"):
            await finish_action(session, successor_card_id, CardStage.DONE)


async def test_a_repeat_successor_gets_one_row_per_series(sessions):
    """CH-CLOSE-011 — tests/brd/checks.feature"""
    async with sessions() as session:
        card = await create_action(session, title="Posture round", repeatable=True)
        check = await linked_check(session, card.id, title="Posture", repeatable=True)
        await session.commit()

        # Answering inside the cycle leaves the answered instance plus its open successor
        # on the same Card; the clone must still produce exactly one row for the series.
        _, spawned = await resolve_check(session, check.id, CheckOutcome.PASSED)
        await session.commit()
        assert spawned is not None

        result = await finish_action(session, card.id, CardStage.DONE)
        await session.commit()

        copies = await card_checks(session, result.successor_ids[0])
        assert len(copies) == 1
        assert copies[0].outcome is None


async def test_reopening_a_card_brings_its_checks_back(sessions):
    """CH-REOPEN-012 — tests/brd/checks.feature"""
    async with sessions() as session:
        card = await create_action(session, title="Go to the market")
        plain = await linked_check(session, card.id, title="Milk")
        repeating = await linked_check(session, card.id, title="Posture", repeatable=True)
        await session.commit()

        await finish_action(
            session,
            card.id,
            CardStage.DONE,
            check_outcomes={plain.id: CheckOutcome.PASSED, repeating.id: CheckOutcome.MISSED},
        )
        await session.commit()

        await move_card(session, card.id, CardStage.TODAY)
        await session.commit()

        # The plain Check is that same Check, asked again.
        restored = await session.get(Check, plain.id)
        assert restored.outcome is None
        assert restored.resolved_at is None
        assert restored.resolved_by is None
        # The repeating series keeps its answer and opens a fresh instance instead.
        answered = await session.get(Check, repeating.id)
        assert answered.outcome == CheckOutcome.MISSED.value
        opened = [
            item
            for item in await pending_checks(session, card.id)
            if item.series_id == repeating.series_id
        ]
        assert len(opened) == 1
        assert opened[0].id != repeating.id

        with pytest.raises(DomainError, match="Resolve these Pending Checks") as error:
            await finish_action(session, card.id, CardStage.DONE)
        assert "Milk" in str(error.value) and "Posture" in str(error.value)


async def test_a_repeating_action_cannot_be_reopened_and_its_checks_do_not_move(sessions):
    """CH-REOPEN-012 — tests/brd/checks.feature"""
    async with sessions() as session:
        card = await create_action(session, title="Posture round", repeatable=True)
        check = await linked_check(session, card.id, title="Posture", repeatable=True)
        await session.commit()

        await finish_action(
            session, card.id, CardStage.DONE, check_outcomes={check.id: CheckOutcome.PASSED}
        )
        await session.commit()
        before = [(item.id, item.outcome) for item in await card_checks(session, card.id)]

        with pytest.raises(DomainError, match="closed repeating Action cannot be reopened"):
            await move_card(session, card.id, CardStage.TODAY)

        assert [(item.id, item.outcome) for item in await card_checks(session, card.id)] == before


async def test_an_answered_check_is_archived_two_sprints_later(sessions):
    """CH-ARCHIVE-013 — tests/brd/checks.feature"""
    async with sessions() as session:
        card = await create_action(session, title="Go to the market")
        answered = await linked_check(session, card.id, title="Milk")
        waiting = await linked_check(session, None, title="Weighed myself?")
        await resolve_check(session, answered.id, CheckOutcome.PASSED)
        await session.commit()

        await run_sprints(session, 2)
        assert (await session.get(Check, answered.id)).archived_at is None

        await start_sprint(session, success_criteria="Sprint 3")
        await finish_sprint(session)
        await session.commit()

        # Its Card is still live, so the Check left on its own clock.
        assert (await session.get(Check, answered.id)).archived_at is not None
        assert (await session.get(Card, card.id)).archived_at is None
        assert (await session.get(Check, waiting.id)).archived_at is None


async def test_an_archived_answer_still_counts_for_the_gate(sessions):
    """CH-ARCHIVE-013 — tests/brd/checks.feature"""
    async with sessions() as session:
        card = await create_action(session, title="Posture round")
        check = await linked_check(session, card.id, title="Posture", repeatable=True)
        value = await create_value(session, "Health")
        await toggle_check_value(session, check.id, value.id)
        await session.commit()

        _answered, successor = await resolve_check(session, check.id, CheckOutcome.PASSED)
        await session.commit()
        assert successor is not None

        await archive_check(session, check.id)
        await session.commit()

        # The answer left the screens, not the count: the series is still observed here.
        assert await unobserved_series(session, card.id) == []
        await finish_action(session, card.id, CardStage.DONE)
        await session.commit()

        assert (await session.get(Card, card.id)).effective_stage == CardStage.DONE.value
        assert await session.get(Check, successor.id) is None
        # R2 hands the deleted instance's Values back, and an archived answer still takes them.
        assert await check_value_ids(session, check.id) == [value.id]


async def test_a_repeating_card_copies_a_series_whose_answer_was_archived(sessions):
    """CH-CLOSE-011 — tests/brd/checks.feature"""
    async with sessions() as session:
        card = await create_action(session, title="Weekly review", repeatable=True)
        check = await linked_check(session, card.id, title="Desk clear?")
        await resolve_check(session, check.id, CheckOutcome.PASSED)
        await archive_check(session, check.id)
        await session.commit()

        result = await finish_action(session, card.id, CardStage.DONE)
        await session.commit()

        copies = await card_checks(session, result.successor_ids[0])
        assert [(item.title, item.outcome) for item in copies] == [("Desk clear?", None)]


async def test_only_an_answered_check_can_be_archived_by_hand(sessions):
    """CH-ARCHIVE-013 — tests/brd/checks.feature"""
    async with sessions() as session:
        card = await create_action(session, title="Go to the market")
        check = await linked_check(session, card.id, title="Milk")
        await session.commit()

        with pytest.raises(DomainError, match="Only an answered Check can be archived"):
            await archive_check(session, check.id)

        await resolve_check(session, check.id, CheckOutcome.PASSED)
        await archive_check(session, check.id)
        await session.commit()
        assert (await session.get(Check, check.id)).archived_at is not None


async def test_deleting_a_check_deletes_it(sessions):
    """CH-DELETE-014 — tests/brd/checks.feature"""
    async with sessions() as session:
        card = await create_action(session, title="Go to the market")
        pending = await linked_check(session, card.id, title="Milk")
        answered = await linked_check(session, None, title="Weighed myself?")
        await resolve_check(session, answered.id, CheckOutcome.PASSED)
        await session.commit()

        await delete_check(session, pending.id)
        await delete_check(session, answered.id)
        await session.commit()

        assert await session.get(Check, pending.id) is None
        assert await session.get(Check, answered.id) is None
        assert await card_checks(session, card.id) == []
        assert (await session.get(Card, card.id)).effective_stage == CardStage.TODAY.value

        with pytest.raises(DomainError, match="Check does not exist"):
            await delete_check(session, pending.id)


async def test_pl_end_014_nothing_is_archived_while_the_workspace_is_in_planning(sessions):
    """PL-END-014 — tests/brd/planning.feature"""
    async with sessions() as session:
        check = await create_check(session, title="Milk")
        await resolve_check(session, check.id, CheckOutcome.PASSED)
        await session.commit()

        # Fewer Sprint endings than the wait, so the archiver has nothing to go on.
        assert await archive_settled_items(session) == ([], [])
        assert (await session.get(Check, check.id)).archived_at is None


async def test_ch_repeat_015_every_instance_names_its_series_and_the_cards(read_views):
    """CH-REPEAT-015 — tests/brd/checks.feature"""
    sessions, runner = read_views
    async with sessions() as session:
        card = await create_action(session, title="Pull up 20 times", repeatable=True)
        check = await linked_check(session, card.id, title="Pulled up today?", repeatable=True)
        _, second = await resolve_check(session, check.id, CheckOutcome.PASSED)
        await resolve_check(session, second.id, CheckOutcome.MISSED)
        result = await finish_action(session, card.id, CardStage.DONE)
        successor_card = result.successor_ids[0]
        loose = await linked_check(session, None, title="Weighed in?")
        await session.commit()
        card_id, check_id, loose_id = card.id, check.id, loose.id

    rows = (
        await runner.run(
            "SELECT id, title, status, series_id, card_id, card_series_id FROM ai_checks "
            "ORDER BY id"
        )
    ).rows
    by_id = {row["id"]: row for row in rows}

    # Every instance of the series names the same series, whichever copy of the Card it
    # landed on, and a Check that was never copied names itself.
    series = {row["series_id"] for row in rows if row["id"] != loose_id}
    assert series == {check_id}
    assert by_id[loose_id]["series_id"] == loose_id
    assert by_id[loose_id]["card_series_id"] is None

    # Counting every answer across every copy of that Action reads one list and joins nothing.
    counted = (
        await runner.run(
            f"SELECT status, count(*) AS n FROM ai_checks WHERE card_series_id = {card_id} "
            "GROUP BY status ORDER BY status"
        )
    ).rows
    assert counted == [{"status": "missed", "n": 1}, {"status": "passed", "n": 1},
                       {"status": "pending", "n": 1}]
    assert {row["card_id"] for row in rows if row["id"] != loose_id} == {card_id, successor_card}

    answered = by_id[check_id]
    assert answered["title"].startswith("Pulled up today? [🔄1, live #")


async def test_ch_repeat_015_a_card_finds_its_checks_by_naming_the_card(sessions):
    """CH-REPEAT-015 — tests/brd/checks.feature"""
    async with sessions() as session:
        card = await create_action(session, title="Posture")
        answered = await linked_check(session, card.id, title="Sat straight?")
        await resolve_check(session, answered.id, CheckOutcome.PASSED)
        await archive_check(session, answered.id)
        pending = await linked_check(session, card.id, title="Stood up?")
        await session.commit()

        # One list, archived or not: the screen marks what is archived instead of hiding it.
        assert [check.id for check in await card_checks(session, card.id)] == [
            answered.id,
            pending.id,
        ]
        assert await title_marks(session, answered) == " [📦]"


async def test_ch_archive_013_safwa_reads_an_archived_answer(read_views):
    """CH-ARCHIVE-013 — tests/brd/checks.feature"""
    sessions, runner = read_views
    async with sessions() as session:
        card = await create_action(session, title="Posture")
        check = await linked_check(session, card.id, title="Sat straight?")
        await resolve_check(session, check.id, CheckOutcome.PASSED)
        await archive_check(session, check.id)
        await session.commit()
        check_id = check.id

    rows = (await runner.run("SELECT id, title, status FROM ai_checks")).rows
    assert rows == [{"id": check_id, "title": "Sat straight? [📦]", "status": "passed"}]
