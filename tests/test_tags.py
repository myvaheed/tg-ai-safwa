from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from safwa.bootstrap.modules import PROPOSALS
from safwa.constants import INBOX_TAG_NAME
from safwa.enums import ActorType
from safwa.features.cards.hooks import EMPTY_PARENT_GRACE_DAYS, empty_parents_request
from safwa.features.cards.model import Card
from safwa.features.cards.references import TAG_REFERENCE
from safwa.features.cards.use_cases import create_card, toggle_card_tag
from safwa.features.tags.model import CardTag, Tag
from safwa.features.tags.use_cases import create_tag, delete_tag, seed_inbox_tag, update_tag_fields
from safwa.features.workspace_mutator.remove import RemoveToolInput
from safwa.features.workspace_mutator.state import workspace_context
from safwa.foundation.workspace import Workspace
from tg_agent_shell.foundation.clock import utcnow
from tg_agent_shell.foundation.errors import DomainError
from tg_agent_shell.foundation.references import resolve_references
from tg_agent_shell.proposals.api import ToolPreparationError
from tg_agent_shell.proposals.prepare import ChangePreparer


async def _action(session, title: str, **overrides):
    return await create_card(
        session,
        title=title,
        kind=overrides.pop("kind", "action"),
        effort_points=overrides.pop("effort_points", 3),
        **overrides,
    )


@pytest.mark.parametrize("name, duplicate", [("Family", "FAMILY"), ("Семья", "СЕМЬЯ"), ("Straße", "STRASSE")])
async def test_a_tag_name_is_taken_whatever_the_capitals(sessions, name, duplicate):
    """TA-NAME-002 — tests/brd/tags.feature"""
    async with sessions() as session:
        await create_tag(session, name)
        other = await create_tag(session, "Work")
        await session.commit()

        with pytest.raises(DomainError, match="already exists"):
            await create_tag(session, duplicate)
        with pytest.raises(DomainError, match="already exists"):
            await update_tag_fields(session, other.id, name=duplicate)
        with pytest.raises(DomainError, match="cannot be empty"):
            await update_tag_fields(session, other.id, name=" ")
        with pytest.raises(DomainError, match="cannot be empty"):
            await create_tag(session, "")


async def test_a_tag_unicode_name_is_unique_in_storage_and_resolves_in_proposals(sessions):
    """TA-NAME-002 — tests/brd/tags.feature"""
    async with sessions() as session:
        tag = await create_tag(session, "Семья")
        await update_tag_fields(session, tag.id, name="РОДНЫЕ")
        await session.commit()
        resolved = await resolve_references(session, TAG_REFERENCE, {"tags": "родные"})
        assert resolved.ids == {tag.id}
        session.add(Tag(name="Родные"))
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()


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


async def test_ta_context_009_every_tag_reaches_safwa_by_name_and_in_order(sessions):
    """TA-CONTEXT-009 — tests/brd/tags.feature"""
    async with sessions() as session:
        work = await create_tag(session, "Work", description="Projects and meetings")
        family = await create_tag(session, "Family", description="Time together")
        await session.commit()

        state = (await workspace_context(session)).state

    lines = state.splitlines()
    start = lines.index("Available Tags:") + 1
    # Both of them, alphabetically rather than in the order they were written, and each
    # already a link: a Tag has no focus, so there is nothing for one to be left out of.
    assert lines[start:start + 2] == [
        f"- [Family](tag:{family.id}): Time together",
        f"- [Work](tag:{work.id}): Projects and meetings",
    ]


async def test_the_inbox_tag_is_seeded_once(sessions):
    """TA-INBOX-010 — tests/brd/tags.feature"""
    async with sessions() as session:
        assert await seed_inbox_tag(session) is True
        await session.commit()
        revision = (await session.get(Workspace, 1)).revision
        assert await seed_inbox_tag(session) is False
        assert (await session.get(Workspace, 1)).revision == revision
        tags = list(await session.scalars(select(Tag)))
        assert [tag.name for tag in tags] == [INBOX_TAG_NAME]
        with pytest.raises(DomainError, match="already exists"):
            await create_tag(session, INBOX_TAG_NAME.lower())


@pytest.mark.parametrize("actor", list(ActorType))
async def test_the_inbox_tag_is_protected_but_its_links_and_description_are_editable(sessions, actor):
    """TA-INBOX-010 — tests/brd/tags.feature"""
    async with sessions() as session:
        await seed_inbox_tag(session)
        tag = await session.scalar(select(Tag).where(Tag.name == INBOX_TAG_NAME))
        card = await _action(session, "A thought")
        assert await toggle_card_tag(session, card.id, tag.id, actor=actor) is True
        await session.commit()
        version = tag.version
        revision = (await session.get(Workspace, 1)).revision

        with pytest.raises(DomainError, match="cannot be deleted"):
            await delete_tag(session, tag.id, actor=actor)
        with pytest.raises(DomainError, match="cannot be renamed"):
            await update_tag_fields(session, tag.id, name="Ordinary", actor=actor)
        assert tag.name == INBOX_TAG_NAME and tag.version == version
        assert (await session.get(Workspace, 1)).revision == revision
        assert await session.get(CardTag, (card.id, tag.id)) is not None

        await update_tag_fields(session, tag.id, description="For later", actor=actor)
        assert tag.description == "For later"
        assert await toggle_card_tag(session, card.id, tag.id, actor=actor) is False
        await session.commit()
        assert await session.get(Tag, tag.id) is not None
        assert await session.get(CardTag, (card.id, tag.id)) is None


async def test_an_ai_proposal_cannot_delete_or_rename_the_inbox_tag(sessions):
    """TA-INBOX-010 — tests/brd/tags.feature"""
    async with sessions() as session:
        await seed_inbox_tag(session)
        tag = await session.scalar(select(Tag).where(Tag.name == INBOX_TAG_NAME))
        await session.commit()
        for tool, arguments in (
            ("remove", {"entity": "tag", "mode": "delete", "id": tag.id}),
            ("tag", {"mode": "update", "id": tag.id, "name": "Ordinary"}),
        ):
            change = PROPOSALS.change_from_tool(tool, arguments)
            with pytest.raises(ToolPreparationError) as refused:
                await ChangePreparer(None, None, PROPOSALS).prepare(session, change)
            assert refused.value.code == "protected_tag"
            assert 'action mode="unlink"' in refused.value.hint
            assert f'tags=["{INBOX_TAG_NAME}"]' in refused.value.hint


async def test_an_inbox_goal_is_not_asked_to_add_actions_until_the_tag_is_removed(sessions):
    """CD-EMPTY-035 — tests/brd/cards.feature"""
    async with sessions() as session:
        await seed_inbox_tag(session)
        tag = await session.scalar(select(Tag).where(Tag.name == INBOX_TAG_NAME))
        goal = await create_card(session, kind="goal", title="An unclarified goal")
        await toggle_card_tag(session, goal.id, tag.id)
        await session.commit()
        now = utcnow() + timedelta(days=EMPTY_PARENT_GRACE_DAYS + 1)
        assert await empty_parents_request(session, [], now=now) is None
        await toggle_card_tag(session, goal.id, tag.id)
        await session.commit()
        assert goal.title in await empty_parents_request(session, [], now=now)
