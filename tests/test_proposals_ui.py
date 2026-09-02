"""The review screen every feature's presenter is drawn into: diffs, and Save/Discard."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from ui_harness import (
    FakeMessage,
    button_texts,
    services_for,
)

from safwa.bootstrap.modules import (
    PROPOSALS,
)
from safwa.features.cards.model import Card, CardCategory, CardEnergyType
from safwa.features.cards.use_cases import create_card
from safwa.features.checks.use_cases import create_check
from safwa.features.diary.use_cases import create_diary_entry
from safwa.features.proposals.model import ChangeAction, ProposalChange
from safwa.features.proposals.store import ProposalStore
from safwa.features.proposals.telegram import render_proposal
from safwa.features.proposals.use_cases import approve_proposal
from safwa.features.tags.model import CardTag, Tag
from safwa.features.values.model import CardValue, Value
from safwa.foundation.workspace import Workspace


async def test_item_proposal_shows_diffs_and_only_save_discard_footer(sessions) -> None:
    store = ProposalStore()
    async with sessions() as session:
        tag = Tag(name="Family", description="Old description")
        session.add(tag)
        await session.flush()
        workspace = await session.get(Workspace, 1)
        proposal = store.open_proposal(
            message="Improve the Family Tag",
            workspace_revision=workspace.revision,
            changes=[
                ProposalChange(
                    entity="tag",
                    action=ChangeAction.UPDATE,
                    entity_id=tag.id,
                    expected_version=tag.version,
                    values={"description": "Relationships and home"},
                )
            ],
        )
        await session.commit()
        proposal_id = proposal.id

    message = FakeMessage(60, bot_message=True)
    await render_proposal(message, services_for(sessions, reviews=store), proposal_id)
    text, markup = message.edits[-1]
    assert "Old description" in text
    assert "Relationships and home" in text
    assert "→" in text
    buttons = button_texts(markup)
    assert buttons == ["✅ Save", "🗑 Discard"]
    assert "↩️ Back" not in buttons


async def test_card_proposal_uses_full_card_editor_with_human_diffs(sessions) -> None:
    store = ProposalStore()
    async with sessions() as session:
        card = Card(
            kind="action",
            title="Evening walk",
            note="After work",
            effort_points=3,
        )
        session.add(card)
        await session.flush()
        session.add(CardCategory(card_id=card.id, category="self"))
        workspace = await session.get(Workspace, 1)
        proposal = store.open_proposal(
            message="Change the Action's energy profile",
            workspace_revision=workspace.revision,
            changes=[
                ProposalChange(
                    entity="card",
                    action=ChangeAction.UPDATE,
                    entity_id=card.id,
                    expected_version=card.version,
                    values={
                    "categories": ["contribution", "rest"],
                    "energy_types": ["physical", "social"],
                    },
                )
            ],
        )
        await session.commit()
        proposal_id = proposal.id

    message = FakeMessage(61, bot_message=True)
    await render_proposal(message, services_for(sessions, reviews=store), proposal_id)
    text, markup = message.edits[-1]

    assert "Card overview" in text
    assert "Kind: ⭐️ Action" in text
    assert "Title: <b>Evening walk</b>" in text
    assert "Effort: 3" in text
    assert "Categories: 🌱 Self → ❤️ Contribution, 🔋 Rest" in text
    assert "Energy: — → 💪 Physical, 🤝 Social" in text
    buttons = button_texts(markup)
    assert buttons == ["✅ Save", "🗑 Discard"]
    assert "↩️ Back" not in buttons


async def test_card_check_link_proposal_shows_the_check_in_overview_and_diff(sessions) -> None:
    store = ProposalStore()
    async with sessions() as session:
        card = await create_card(session, kind="goal", title="Be healthy")
        check = await create_check(session, title="Walk upright")
        workspace = await session.get(Workspace, 1)
        proposal = store.open_proposal(
            message="Link the Check to the Goal",
            workspace_revision=workspace.revision,
            changes=[
                ProposalChange(
                    entity="card",
                    action=ChangeAction.LINK,
                    entity_id=card.id,
                    expected_version=card.version,
                    values={"check_ids": [check.id]},
                )
            ],
        )
        await session.commit()
        proposal_id = proposal.id

    message = FakeMessage(64, bot_message=True)
    await render_proposal(message, services_for(sessions, reviews=store), proposal_id)
    text, _ = message.edits[-1]

    assert "Checks: Walk upright" in text
    assert "• Checks: — → Walk upright" in text


async def test_diary_proposal_shows_the_entry_itself_and_only_save_or_discard(
    sessions,
) -> None:
    store = ProposalStore()
    async with sessions() as session:
        workspace = await session.get(Workspace, 1)
        proposal = store.open_proposal(
            message="Save today's Diary entry",
            workspace_revision=workspace.revision,
            changes=[
                ProposalChange(
                    entity="diary",
                    action=ChangeAction.UPDATE,
                    entity_id=7,
                    expected_version=1,
                    values={
                    "entry_date": "2026-08-15",
                    "body": "Сходил на рынок, вечером стало легче.",
                    "feeling_score": 6,
                    "remark": "A day that ended better than it began.",
                    },
                )
            ],
        )
        await session.commit()
        proposal_id = proposal.id

    message = FakeMessage(65, bot_message=True)
    await render_proposal(message, services_for(sessions, reviews=store), proposal_id)
    text, markup = message.edits[-1]

    assert "<b>Edit Diary entry · AI proposal</b>" in text
    assert "Date: 2026-08-15" in text
    assert "Feeling: 6 🙂" in text
    assert "This replaces the entry already saved for that day." in text
    assert "Сходил на рынок, вечером стало легче." in text
    assert "<i>A day that ended better than it began.</i>" in text
    # The screen is the day in the owner's voice plus Safwa's one line about it; a field
    # diff would only repeat the entry back at them.
    assert "<b>Proposed changes</b>" not in text
    assert button_texts(markup) == ["✅ Save", "🗑 Discard"]


async def test_diary_removal_shows_the_entry_it_would_delete(sessions) -> None:
    store = ProposalStore()
    async with sessions() as session:
        entry = await create_diary_entry(
            session, entry_date=date(2026, 8, 14), body="День, который уходит."
        )
        workspace = await session.get(Workspace, 1)
        proposal = store.open_proposal(
            message="Remove that day's Diary entry",
            workspace_revision=workspace.revision,
            changes=[
                ProposalChange(
                    entity="diary",
                    action=ChangeAction.DELETE,
                    entity_id=entry.id,
                    expected_version=entry.version,
                    values={"stamp": "abc123", "entry_date": "2026-08-14", "body": "", "remark": ""},
                )
            ],
        )
        await session.commit()
        proposal_id = proposal.id

    message = FakeMessage(66, bot_message=True)
    await render_proposal(message, services_for(sessions, reviews=store), proposal_id)
    text, markup = message.edits[-1]

    assert "<b>Remove Diary entry · AI proposal</b>" in text
    assert "This removes that day's entry for good." in text
    # The owner reads what is about to go, not an empty replacement.
    assert "День, который уходит." in text
    assert button_texts(markup) == ["✅ Save", "🗑 Discard"]


async def test_card_creation_proposal_has_no_proposed_changes_section(sessions) -> None:
    store = ProposalStore()
    async with sessions() as session:
        workspace = await session.get(Workspace, 1)
        proposal = store.open_proposal(
            message="Create a walking Action",
            workspace_revision=workspace.revision,
            changes=[
                ProposalChange(
                    entity="card",
                    action=ChangeAction.CREATE,
                    values={
                    "kind": "action",
                    "title": "Evening walk",
                    "effort_points": 3,
                    "categories": ["self"],
                    },
                )
            ],
        )
        await session.commit()
        proposal_id = proposal.id

    message = FakeMessage(62, bot_message=True)
    await render_proposal(message, services_for(sessions, reviews=store), proposal_id)
    text, markup = message.edits[-1]

    assert "Card overview" in text
    assert "Kind: ⭐️ Action" in text
    assert "Title: <b>Evening walk</b>" in text
    assert "Categories: 🌱 Self" in text
    assert "<b>Proposed changes</b>" not in text
    assert button_texts(markup) == ["✅ Save", "🗑 Discard"]


async def test_move_proposal_exposes_only_stage_control(sessions) -> None:
    store = ProposalStore()
    async with sessions() as session:
        card = Card(kind="action", title="Evening walk", effort_points=3)
        session.add(card)
        await session.flush()
        workspace = await session.get(Workspace, 1)
        proposal = store.open_proposal(
            message="Move the Action to Today",
            workspace_revision=workspace.revision,
            changes=[
                ProposalChange(
                    entity="card",
                    action=ChangeAction.MOVE,
                    entity_id=card.id,
                    expected_version=card.version,
                    values={"stage": "today"},
                )
            ],
        )
        await session.commit()
        proposal_id = proposal.id

    message = FakeMessage(63, bot_message=True)
    await render_proposal(message, services_for(sessions, reviews=store), proposal_id)
    text, markup = message.edits[-1]

    assert "Stage: Backlog → Today" in text
    assert button_texts(markup) == ["✅ Save", "🗑 Discard"]


async def test_saving_card_proposal_applies_every_editable_field(sessions) -> None:
    store = ProposalStore()
    async with sessions() as session:
        parent = Card(kind="goal", title="Be healthy")
        card = Card(kind="action", title="Walk", effort_points=2)
        value = Value(name="Health")
        tag = Tag(name="Outside")
        session.add_all([parent, card, value, tag])
        await session.flush()
        session.add_all(
            [
                CardCategory(card_id=card.id, category="self"),
                CardEnergyType(card_id=card.id, energy_type="cognitive"),
            ]
        )
        workspace = await session.get(Workspace, 1)
        proposal = store.open_proposal(
            message="Update the Action",
            workspace_revision=workspace.revision,
            changes=[
                ProposalChange(
                    entity="card",
                    action=ChangeAction.UPDATE,
                    entity_id=card.id,
                    expected_version=card.version,
                    values={
                    "priority": "critical",
                    "hard_time": True,
                    "blocked": True,
                    "blocked_description": "Waiting for access",
                    "effort_points": 5,
                    "parent_id": parent.id,
                    "categories": ["rest", "work"],
                    "energy_types": ["physical", "social"],
                    "value_ids": [value.id],
                    "tag_ids": [tag.id],
                    },
                )
            ],
        )
        await session.commit()
        proposal_id = proposal.id
        card_id = card.id

    async with sessions() as session:
        affected = await approve_proposal(session, store, PROPOSALS, proposal_id)
        await session.commit()

    assert affected == [card_id]
    async with sessions() as session:
        card = await session.get(Card, card_id)
        assert (
            card.priority,
            card.hard_time,
            card.blocked,
            card.blocked_description,
            card.effort_points,
            card.parent_id,
        ) == (
            "critical",
            True,
            True,
            "Waiting for access",
            5,
            parent.id,
        )
        assert set(
            await session.scalars(
                select(CardCategory.category).where(CardCategory.card_id == card_id)
            )
        ) == {"rest", "work"}
        assert set(
            await session.scalars(
                select(CardEnergyType.energy_type).where(CardEnergyType.card_id == card_id)
            )
        ) == {"physical", "social"}
        assert set(
            await session.scalars(select(CardValue.value_id).where(CardValue.card_id == card_id))
        ) == {value.id}
        assert set(
            await session.scalars(select(CardTag.tag_id).where(CardTag.card_id == card_id))
        ) == {tag.id}
