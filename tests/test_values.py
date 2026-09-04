from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy import select, text

from safwa.bootstrap.modules import AI_VIEWS
from safwa.features.cards.model import Card, CardCheck, CardStage
from safwa.features.cards.use_cases import (
    archive_subtree,
    create_card,
    finish_action,
    toggle_card_check,
    toggle_card_value,
)
from safwa.features.checks.model import Check, CheckOutcome
from safwa.features.checks.use_cases import (
    archive_check,
    check_value_ids,
    create_check,
    delete_check,
    resolve_check,
    toggle_check_value,
)
from safwa.features.tags.use_cases import create_tag
from safwa.features.values.model import CardValue, CheckValue, Value
from safwa.features.values.use_cases import create_value, delete_value, update_value_fields
from safwa.features.workspace_mutator.remove import RemoveToolInput
from safwa.features.workspace_mutator.state import (
    CONTEXT_CRITICAL_CARD_LIMIT,
    workspace_context,
)
from tg_agent_shell.ai.sql import create_ai_views
from tg_agent_shell.foundation.errors import DomainError


async def _action(session, title: str, **overrides):
    return await create_card(
        session,
        title=title,
        kind=overrides.pop("kind", "action"),
        effort_points=overrides.pop("effort_points", 3),
        **overrides,
    )


async def _views(session):
    await (await session.connection()).run_sync(
        lambda connection: create_ai_views(connection, AI_VIEWS)
    )


async def value_card_ids(session, value_id: int) -> list[int]:
    return sorted(
        await session.scalars(select(CardValue.card_id).where(CardValue.value_id == value_id))
    )



async def test_a_value_name_is_taken_whatever_the_capitals(sessions):
    """VL-NAME-005 — tests/brd/values.feature"""
    async with sessions() as session:
        await create_value(session, "Fitness")
        other = await create_value(session, "Tidiness")
        await session.commit()

        with pytest.raises(DomainError, match="already exists"):
            await create_value(session, "FITNESS")
        with pytest.raises(DomainError, match="already exists"):
            await update_value_fields(session, other.id, name="fitness")
        with pytest.raises(DomainError, match="cannot be empty"):
            await update_value_fields(session, other.id, name="   ")
        with pytest.raises(DomainError, match="cannot be empty"):
            await create_value(session, "  ")


async def test_a_check_can_carry_a_value_of_its_own(sessions):
    """VL-CHECK-010 — tests/brd/values.feature"""
    async with sessions() as session:
        health = await create_value(session, "Health")
        tidiness = await create_value(session, "Tidiness")
        card = await _action(session, "Morning run")
        check = await create_check(session, title="Did I sleep seven hours?")
        await toggle_card_check(session, card.id, check.id)
        await toggle_card_value(session, card.id, tidiness.id)
        await session.commit()

        assert await toggle_check_value(session, check.id, health.id) is True
        await session.commit()

        assert await check_value_ids(session, check.id) == [health.id]
        # A Check's Values are its own: the Card it belongs to kept exactly what it had.
        assert await value_card_ids(session, health.id) == []
        assert await value_card_ids(session, tidiness.id) == [card.id]

        assert await toggle_check_value(session, check.id, health.id) is False
        await session.commit()
        assert await check_value_ids(session, check.id) == []


async def test_archiving_a_check_keeps_its_values_and_deleting_takes_them(sessions):
    """VL-CHECK-011 — tests/brd/values.feature"""
    async with sessions() as session:
        value = await create_value(session, "Health")
        card = await _action(session, "Morning run")
        check = await create_check(session, title="Did I sleep seven hours?")
        await toggle_card_check(session, card.id, check.id)
        await toggle_check_value(session, check.id, value.id)
        await resolve_check(session, check.id, CheckOutcome.PASSED)
        await session.commit()

        await archive_check(session, check.id)
        await session.commit()
        assert await check_value_ids(session, check.id) == [value.id]

        await delete_check(session, check.id)
        await session.commit()
        assert await session.get(Check, check.id) is None
        assert list(await session.scalars(select(CheckValue.value_id))) == []


async def test_an_answered_repeat_hands_its_values_to_its_successor(sessions):
    """VL-CHECK-012 — tests/brd/values.feature"""
    async with sessions() as session:
        value = await create_value(session, "Health")
        card = await _action(session, "Morning run")
        repeating = await create_check(
            session, title="Did I sleep seven hours?", repeatable=True
        )
        once = await create_check(session, title="Did the audit pass?")
        await toggle_card_check(session, card.id, repeating.id)
        await toggle_card_check(session, card.id, once.id)
        await toggle_check_value(session, repeating.id, value.id)
        await toggle_check_value(session, once.id, value.id)
        await session.commit()

        _answered, successor = await resolve_check(session, repeating.id, CheckOutcome.PASSED)
        await session.commit()

        assert successor is not None
        assert await check_value_ids(session, successor.id) == [value.id]
        assert await check_value_ids(session, repeating.id) == []

        # A Check that does not repeat is the record of that one observation, so it keeps them.
        await resolve_check(session, once.id, CheckOutcome.MISSED)
        await session.commit()
        assert await check_value_ids(session, once.id) == [value.id]


async def test_safwa_sees_the_values_a_check_is_about(sessions):
    """VL-READ-013 — tests/brd/values.feature"""
    async with sessions() as session:
        await _views(session)
        health = await create_value(session, "Health")
        check = await create_check(session, title="Did I sleep seven hours?")
        plain = await create_check(session, title="Did the audit pass?")
        await toggle_check_value(session, check.id, health.id)
        await session.commit()

        rows = {
            row["id"]: row["direct_values"]
            for row in (
                await session.execute(text("SELECT id, direct_values FROM ai_checks"))
            ).mappings()
        }
        assert rows[check.id] == "Health"
        assert rows[plain.id] is None


async def test_safwa_can_start_from_a_value_and_find_what_is_behind_it(sessions):
    """VL-READ-014 — tests/brd/values.feature"""
    async with sessions() as session:
        await _views(session)
        health = await create_value(session, "Health")
        run = await _action(session, "Morning run")
        stale = await _action(session, "Old plan")
        await toggle_card_value(session, run.id, health.id)
        await toggle_card_value(session, stale.id, health.id)
        evidence = await create_check(session, title="Did I sleep seven hours?")
        forgotten = await create_check(session, title="Old observation")
        await toggle_check_value(session, evidence.id, health.id)
        await toggle_check_value(session, forgotten.id, health.id)
        await resolve_check(session, forgotten.id, CheckOutcome.PASSED)
        await session.commit()

        await finish_action(session, stale.id, CardStage.CANCELLED)
        await archive_subtree(session, stale.id)
        await archive_check(session, forgotten.id)
        await session.commit()

        cards = list(
            (
                await session.execute(
                    text("SELECT title FROM ai_cards WHERE direct_values LIKE '%Health%'")
                )
            ).scalars()
        )
        checks = list(
            (
                await session.execute(
                    text("SELECT title FROM ai_checks WHERE direct_values LIKE '%Health%'")
                )
            ).scalars()
        )
        # The archived ones are in both answers, and the marker is what says they are old.
        assert cards == ["Morning run", "Old plan [📦]"]
        assert checks == ["Did I sleep seven hours?", "Old observation [📦]"]


async def test_safwa_is_told_which_values_are_in_focus(sessions):
    """VL-FOCUS-002 — tests/brd/values.feature"""
    async with sessions() as session:
        fitness = await create_value(session, "Fitness", active=True)
        await create_value(session, "Tidiness", active=False)
        family = await create_tag(session, "Family")
        await session.commit()

        context = await workspace_context(session)

    values_line = next(
        line for line in context.state.splitlines() if line.startswith("Active Values:")
    )
    tags_line = next(
        line for line in context.state.splitlines() if line.startswith("Available Tags:")
    )
    assert values_line == f"Active Values: [Fitness](value:{fitness.id})"
    assert "Tidiness" not in values_line
    assert tags_line == f"Available Tags: [Family](tag:{family.id})"


async def test_a_critical_card_serving_a_focus_is_shown_to_safwa_first(sessions):
    """VL-READ-003 — tests/brd/values.feature"""
    async with sessions() as session:
        focus = await create_value(session, "Health", active=True)
        plain = await _action(session, "Plain critical", priority="critical")
        served = await _action(session, "Serves the focus", priority="critical")
        await toggle_card_value(session, served.id, focus.id)
        # More critical Cards than Safwa is handed, so the ordering has to choose.
        for index in range(CONTEXT_CRITICAL_CARD_LIMIT):
            await _action(session, f"Filler {index}", priority="critical")
        await session.commit()

        context = await workspace_context(session)

    titles = [
        line.split("](")[0].removeprefix("- [")
        for line in context.state.splitlines()
        if line.startswith("- [")
    ]
    assert len(titles) == CONTEXT_CRITICAL_CARD_LIMIT
    assert titles[0] == "Serves the focus"
    assert plain.title in titles


async def test_a_value_is_deleted_not_archived(sessions):
    """VL-DELETE-015 — tests/brd/values.feature"""
    async with sessions() as session:
        value = await create_value(session, "Health", active=True)
        card = await _action(session, "Morning run")
        check = await create_check(session, title="Did I sleep seven hours?")
        await toggle_card_check(session, card.id, check.id)
        await toggle_card_value(session, card.id, value.id)
        await toggle_check_value(session, check.id, value.id)
        await session.commit()

        deleted, removed = await delete_value(session, value.id)
        await session.commit()

        assert removed == 2
        assert await session.get(Value, deleted.id) is None
        assert await value_card_ids(session, deleted.id) == []
        assert await check_value_ids(session, check.id) == []
        # What carried it keeps everything except the link.
        assert (await session.get(Card, card.id)).archived_at is None
        assert (await session.get(Check, check.id)).archived_at is None

        with pytest.raises(DomainError, match="Value does not exist"):
            await delete_value(session, deleted.id)


def test_the_remove_tool_refuses_to_archive_a_value():
    """VL-DELETE-015 — tests/brd/values.feature"""
    assert RemoveToolInput(mode="delete", entity="value", id=1).entity == "value"
    with pytest.raises(ValidationError, match="a value is deleted, never archived"):
        RemoveToolInput(mode="archive", entity="value", id=1)


async def test_a_deleted_value_frees_its_name(sessions):
    """VL-DELETE-015 — tests/brd/values.feature"""
    async with sessions() as session:
        first = await create_value(session, "Fitness", "Original Value", active=True)
        first_id = first.id
        await session.commit()
        await delete_value(session, first_id)
        await session.commit()

        again = await create_value(session, "FITNESS")
        await session.commit()

        # A new Value under the freed name, not the old one coming back.
        assert again.description == ""
        assert again.active is False
        assert len(list(await session.scalars(select(Value)))) == 1


async def test_a_repeating_card_hands_the_checks_values_to_the_next_cycle(sessions):
    """VL-CHECK-016 — tests/brd/values.feature"""
    async with sessions() as session:
        value = await create_value(session, "Health")
        card = await _action(session, "Pull-ups", stage="today", repeatable=True)
        check = await create_check(session, title="Did 20 pull-ups?", repeatable=True)
        await toggle_card_check(session, card.id, check.id)
        await toggle_check_value(session, check.id, value.id)
        await session.commit()

        result = await finish_action(
            session, card.id, CardStage.DONE, check_outcomes={check.id: CheckOutcome.PASSED}
        )
        await session.commit()

        successor = result.successor_ids[0]
        carried = [
            check_id
            for check_id in await session.scalars(
                select(CheckValue.check_id).where(CheckValue.value_id == value.id)
            )
        ]
        next_cycle = list(
            await session.scalars(
                select(Check.id)
                .join(CardCheck, CardCheck.check_id == Check.id)
                .where(CardCheck.card_id == successor)
            )
        )
        # The Value measures the Check the owner answers next, never the finished one it left.
        assert carried == next_cycle
        assert await check_value_ids(session, check.id) == []
