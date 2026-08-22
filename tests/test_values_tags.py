from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy import select, text

from safwa.ai.context import planning_context
from safwa.ai.contracts import RemoveToolInput
from safwa.ai.sql import create_ai_views
from safwa.bootstrap.modules import AI_VIEWS
from safwa.constants import CONTEXT_CRITICAL_CARD_LIMIT
from safwa.domain import (
    DomainError,
    archive_check,
    archive_subtree,
    check_value_ids,
    create_card,
    create_check,
    delete_subtree,
    resolve_check,
    toggle_card_check,
    toggle_card_tag,
    toggle_card_value,
    toggle_check_value,
)
from safwa.enums import CheckOutcome
from safwa.features.planning.model import CardValue, CheckValue
from safwa.features.planning.use_cases import (
    archive_tag,
    archive_value,
    create_tag,
    create_value,
    update_tag_fields,
    update_value_fields,
)
from safwa.models import Card, Check, Tag, Value


async def _action(session, title: str, **overrides):
    return await create_card(
        session,
        title=title,
        kind=overrides.pop("kind", "action"),
        effort_points=overrides.pop("effort_points", 3),
        **overrides,
    )


# --------------------------------------------------------------------------- naming


async def test_a_value_name_is_taken_whatever_the_capitals(sessions):
    """PL-VALUE-005 — tests/brd/values.feature"""
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


async def test_a_tag_name_is_taken_whatever_the_capitals(sessions):
    """PL-TAG-016 — tests/brd/tags.feature"""
    async with sessions() as session:
        await create_tag(session, "Family")
        other = await create_tag(session, "Work")
        await session.commit()

        with pytest.raises(DomainError, match="already exists"):
            await create_tag(session, "FAMILY")
        with pytest.raises(DomainError, match="already exists"):
            await update_tag_fields(session, other.id, name="family")
        with pytest.raises(DomainError, match="cannot be empty"):
            await update_tag_fields(session, other.id, name=" ")
        with pytest.raises(DomainError, match="cannot be empty"):
            await create_tag(session, "")


async def test_writing_down_an_archived_value_brings_that_one_back(sessions):
    """PL-VALUE-006 — tests/brd/values.feature"""
    async with sessions() as session:
        value = await create_value(session, "Fitness", "Original Value", active=True)
        card = await _action(session, "Run")
        await toggle_card_value(session, card.id, value.id)
        value_id = value.id
        await archive_value(session, value.id)
        await session.commit()

        # No description given, so the one it was archived with survives.
        restored = await create_value(session, "FITNESS")
        await session.commit()
        assert restored.id == value_id
        assert restored.archived_at is None
        assert restored.description == "Original Value"
        assert restored.active is False
        assert await session.scalar(select(Value).where(Value.name == "Fitness")) is restored
        assert len(list(await session.scalars(select(Value)))) == 1
        assert await value_card_ids(session, restored.id) == []

        with pytest.raises(DomainError, match="already exists"):
            await create_value(session, "fitness")

        await archive_value(session, restored.id)
        await session.commit()
        again = await create_value(session, "Fitness", "New Value", active=False)
        assert again.description == "New Value"


async def test_writing_down_an_archived_tag_brings_that_one_back(sessions):
    """PL-TAG-017 — tests/brd/tags.feature"""
    async with sessions() as session:
        tag = await create_tag(session, "Family", "Original Tag")
        tag_id = tag.id
        await archive_tag(session, tag.id)
        await session.commit()

        restored = await create_tag(session, "family")
        await session.commit()
        assert restored.id == tag_id
        assert restored.archived_at is None
        assert restored.description == "Original Tag"
        assert len(list(await session.scalars(select(Tag)))) == 1

        with pytest.raises(DomainError, match="already exists"):
            await create_tag(session, "FAMILY")


async def value_card_ids(session, value_id: int) -> list[int]:
    return sorted(
        await session.scalars(select(CardValue.card_id).where(CardValue.value_id == value_id))
    )


# ----------------------------------------------------------------- archiving and links


async def test_archiving_a_value_takes_it_off_cards_and_checks(sessions):
    """PL-VALUE-007 — tests/brd/values.feature"""
    async with sessions() as session:
        value = await create_value(session, "Health", active=True)
        card = await _action(session, "Morning run")
        check = await create_check(session, title="Did I sleep seven hours?")
        await toggle_card_check(session, card.id, check.id)
        await toggle_card_value(session, card.id, value.id)
        await toggle_check_value(session, check.id, value.id)
        await session.commit()

        archived, removed = await archive_value(session, value.id)
        await session.commit()

        assert removed == 2
        assert archived.active is False
        assert archived.archived_at is not None
        assert await value_card_ids(session, value.id) == []
        assert await check_value_ids(session, check.id) == []
        # The things it was on are untouched.
        assert (await session.get(Card, card.id)).archived_at is None
        assert (await session.get(Check, check.id)).archived_at is None

        with pytest.raises(DomainError, match="does not exist or is archived"):
            await archive_value(session, value.id)


async def test_archiving_a_tag_takes_it_off_its_cards_and_the_cards_stay(sessions):
    """PL-TAG-018 — tests/brd/tags.feature"""
    async with sessions() as session:
        tag = await create_tag(session, "Family")
        first = await _action(session, "Phone call")
        second = await _action(session, "Trip plan")
        await toggle_card_tag(session, first.id, tag.id)
        await toggle_card_tag(session, second.id, tag.id)
        await session.commit()

        _archived, removed = await archive_tag(session, tag.id)
        await session.commit()

        assert removed == 2
        assert (await session.get(Card, first.id)).archived_at is None
        assert (await session.get(Card, second.id)).archived_at is None
        with pytest.raises(DomainError, match="does not exist or is archived"):
            await archive_tag(session, tag.id)


async def test_nothing_is_linked_to_an_archived_value(sessions):
    """PL-VALUE-009 — tests/brd/values.feature"""
    async with sessions() as session:
        value = await create_value(session, "Health")
        card = await _action(session, "Morning run")
        check = await create_check(session, title="Did I sleep seven hours?")
        await archive_value(session, value.id)
        await session.commit()

        with pytest.raises(DomainError, match="Value does not exist or is archived"):
            await toggle_card_value(session, card.id, value.id)
        with pytest.raises(DomainError, match="Value does not exist or is archived"):
            await toggle_check_value(session, check.id, value.id)


async def test_a_card_is_not_given_an_archived_tag(sessions):
    """PL-TAG-020 — tests/brd/tags.feature"""
    async with sessions() as session:
        tag = await create_tag(session, "Family")
        card = await _action(session, "Phone call")
        await archive_tag(session, tag.id)
        await session.commit()

        with pytest.raises(DomainError, match="Tag does not exist or is archived"):
            await toggle_card_tag(session, card.id, tag.id)


# ------------------------------------------------------------------- a Check's Values


async def test_a_check_can_carry_a_value_of_its_own(sessions):
    """PL-VALUE-010 — tests/brd/values.feature"""
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
    """PL-VALUE-011 — tests/brd/values.feature"""
    async with sessions() as session:
        value = await create_value(session, "Health")
        card = await _action(session, "Morning run")
        check = await create_check(session, title="Did I sleep seven hours?")
        await toggle_card_check(session, card.id, check.id)
        await toggle_check_value(session, check.id, value.id)
        await session.commit()

        await archive_check(session, check.id)
        await session.commit()
        assert await check_value_ids(session, check.id) == [value.id]

        await delete_subtree(session, card.id)
        await session.commit()
        assert await session.get(Check, check.id) is None
        assert list(await session.scalars(select(CheckValue.value_id))) == []


async def test_an_answered_repeat_hands_its_values_to_its_successor(sessions):
    """PL-VALUE-012 — tests/brd/values.feature"""
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


# ----------------------------------------------------------------- what Safwa reads


async def _views(session):
    await (await session.connection()).run_sync(
        lambda connection: create_ai_views(connection, AI_VIEWS)
    )


async def test_safwa_sees_the_values_a_check_is_about(sessions):
    """PL-VALUE-013 — tests/brd/values.feature"""
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
    """PL-VALUE-014 — tests/brd/values.feature"""
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
        await session.commit()

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
        assert cards == ["Morning run"]
        assert checks == ["Did I sleep seven hours?"]


async def test_safwa_is_told_which_values_are_in_focus(sessions):
    """PL-VALUE-002 — tests/brd/values.feature"""
    async with sessions() as session:
        fitness = await create_value(session, "Fitness", active=True)
        await create_value(session, "Tidiness", active=False)
        gone = await create_value(session, "Retired", active=True)
        family = await create_tag(session, "Family")
        old_tag = await create_tag(session, "Obsolete")
        await archive_value(session, gone.id)
        await archive_tag(session, old_tag.id)
        await session.commit()

        context = await planning_context(session)

    values_line = next(
        line for line in context.state.splitlines() if line.startswith("Active Values:")
    )
    tags_line = next(
        line for line in context.state.splitlines() if line.startswith("Available Tags:")
    )
    assert values_line == f"Active Values: [Fitness](value:{fitness.id})"
    assert "Tidiness" not in values_line
    assert "Retired" not in context.state
    assert tags_line == f"Available Tags: [Family](tag:{family.id})"
    assert "Obsolete" not in context.state


async def test_a_critical_card_serving_a_focus_is_shown_to_safwa_first(sessions):
    """PL-VALUE-003 — tests/brd/values.feature"""
    async with sessions() as session:
        focus = await create_value(session, "Health", active=True)
        retired = await create_value(session, "Retired", active=True)
        await _action(session, "Plain critical", priority="critical")
        served = await _action(session, "Serves the focus", priority="critical")
        stale = await _action(session, "Serves an archived Value", priority="critical")
        await toggle_card_value(session, served.id, focus.id)
        await toggle_card_value(session, stale.id, retired.id)
        await archive_value(session, retired.id)
        # More critical Cards than Safwa is handed, so the ordering has to choose.
        for index in range(CONTEXT_CRITICAL_CARD_LIMIT):
            await _action(session, f"Filler {index}", priority="critical")
        await session.commit()

        context = await planning_context(session)

    titles = [
        line.split("](")[0].removeprefix("- [")
        for line in context.state.splitlines()
        if line.startswith("- [")
    ]
    assert len(titles) == CONTEXT_CRITICAL_CARD_LIMIT
    assert titles[0] == "Serves the focus"
    assert titles.index("Plain critical") < titles.index("Serves an archived Value")


# ----------------------------------------------------------------- archived, not deleted


def test_a_value_is_archived_never_deleted():
    """PL-VALUE-008 — tests/brd/values.feature"""
    assert RemoveToolInput(mode="archive", entity="value", id=1).entity == "value"
    with pytest.raises(ValidationError, match="a value is archived, never deleted"):
        RemoveToolInput(mode="delete", entity="value", id=1)


def test_a_tag_is_archived_never_deleted():
    """PL-TAG-019 — tests/brd/tags.feature"""
    assert RemoveToolInput(mode="archive", entity="tag", id=1).entity == "tag"
    with pytest.raises(ValidationError, match="a tag is archived, never deleted"):
        RemoveToolInput(mode="delete", entity="tag", id=1)
