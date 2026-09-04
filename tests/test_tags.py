from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from safwa.features.cards.model import Card
from safwa.features.cards.use_cases import create_card, toggle_card_tag
from safwa.features.tags.model import CardTag, Tag
from safwa.features.tags.use_cases import create_tag, delete_tag, update_tag_fields
from safwa.features.workspace_mutator.remove import RemoveToolInput
from tg_agent_shell.foundation.errors import DomainError


async def _action(session, title: str, **overrides):
    return await create_card(
        session,
        title=title,
        kind=overrides.pop("kind", "action"),
        effort_points=overrides.pop("effort_points", 3),
        **overrides,
    )


async def test_a_tag_name_is_taken_whatever_the_capitals(sessions):
    """TA-NAME-002 — tests/brd/tags.feature"""
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


async def test_a_tag_is_deleted_and_its_cards_stay(sessions):
    """TA-DELETE-008 — tests/brd/tags.feature"""
    async with sessions() as session:
        tag = await create_tag(session, "Family")
        first = await _action(session, "Phone call")
        second = await _action(session, "Trip plan")
        await toggle_card_tag(session, first.id, tag.id)
        await toggle_card_tag(session, second.id, tag.id)
        await session.commit()

        _deleted, removed = await delete_tag(session, tag.id)
        await session.commit()

        assert removed == 2
        assert await session.get(Tag, tag.id) is None
        assert list(await session.scalars(select(CardTag.card_id))) == []
        assert (await session.get(Card, first.id)).archived_at is None
        assert (await session.get(Card, second.id)).archived_at is None
        with pytest.raises(DomainError, match="Tag does not exist"):
            await delete_tag(session, tag.id)


def test_the_remove_tool_refuses_to_archive_a_tag():
    """TA-DELETE-008 — tests/brd/tags.feature"""
    assert RemoveToolInput(mode="delete", entity="tag", id=1).entity == "tag"
    with pytest.raises(ValidationError, match="a tag is deleted, never archived"):
        RemoveToolInput(mode="archive", entity="tag", id=1)


async def test_a_deleted_tag_frees_its_name(sessions):
    """TA-DELETE-008 — tests/brd/tags.feature"""
    async with sessions() as session:
        tag = await create_tag(session, "Family", "Original Tag")
        await session.commit()
        await delete_tag(session, tag.id)
        await session.commit()

        again = await create_tag(session, "family")
        await session.commit()

        # A new Tag under the freed name, not the old one coming back.
        assert again.description == ""
        assert len(list(await session.scalars(select(Tag)))) == 1
