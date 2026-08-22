from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from safwa.ai.contracts import RemoveToolInput
from safwa.domain import DomainError, create_card, toggle_card_tag
from safwa.features.tags.use_cases import archive_tag, create_tag, update_tag_fields
from safwa.models import Card, Tag


async def _action(session, title: str, **overrides):
    return await create_card(
        session,
        title=title,
        kind=overrides.pop("kind", "action"),
        effort_points=overrides.pop("effort_points", 3),
        **overrides,
    )


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


async def test_a_card_is_not_given_an_archived_tag(sessions):
    """PL-TAG-020 — tests/brd/tags.feature"""
    async with sessions() as session:
        tag = await create_tag(session, "Family")
        card = await _action(session, "Phone call")
        await archive_tag(session, tag.id)
        await session.commit()

        with pytest.raises(DomainError, match="Tag does not exist or is archived"):
            await toggle_card_tag(session, card.id, tag.id)


def test_a_tag_is_archived_never_deleted():
    """PL-TAG-019 — tests/brd/tags.feature"""
    assert RemoveToolInput(mode="archive", entity="tag", id=1).entity == "tag"
    with pytest.raises(ValidationError, match="a tag is archived, never deleted"):
        RemoveToolInput(mode="delete", entity="tag", id=1)
