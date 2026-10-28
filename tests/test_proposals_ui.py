"""The review screen every feature's presenter is drawn into: marked fields, and Save/Discard."""

from __future__ import annotations

from datetime import date

import pytest
from schedule_helpers import create_card, create_check, rule_for
from sqlalchemy import select
from ui_harness import (
    FakeMessage,
    button_texts,
    services_for,
)

from safwa.bootstrap.modules import (
    PROPOSALS,
)
from safwa.features.cards.hierarchy import card_parent_ids
from safwa.features.cards.model import Card, CardCategory, CardEnergyType
from safwa.features.diary.use_cases import create_diary_entry
from safwa.features.tags.model import CardTag, Tag
from safwa.features.values.model import CardValue, Value
from safwa.foundation.workspace import Workspace
from tg_agent_shell.proposals.api import Effect, ShownField
from tg_agent_shell.proposals.marks import (
    LONG_TEXT_CHARS,
    SHORT_VALUE_WORDS,
    field_line,
    marked,
)
from tg_agent_shell.proposals.model import ChangeAction, ProposalChange
from tg_agent_shell.proposals.store import ProposalStore
from tg_agent_shell.proposals.telegram import render_proposal
from tg_agent_shell.proposals.use_cases import approve_proposal


async def test_item_proposal_marks_the_edit_and_only_save_discard_footer(sessions) -> None:
    store = ProposalStore()
    async with sessions() as session:
        tag = Tag(name="Family", description="Old description")
        session.add(tag)
        await session.flush()
        workspace = await session.get(Workspace, 1)
        proposal = store.open_proposal(
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
    assert text.startswith("<b>✏️ Update Tag</b>")
    assert "<i>Name:</i> Family" in text
    assert "<i>Description:</i> <s>Old description</s> <b>Relationships and home</b>" in text
    assert "→" not in text
    buttons = button_texts(markup)
    assert buttons == ["✅ Update", "🗑 Discard"]
    assert "↩️ Back" not in buttons


async def test_card_proposal_marks_only_what_changes(sessions, effort_on) -> None:
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
        session.add(CardCategory(card_id=card.id, category="growth"))
        workspace = await session.get(Workspace, 1)
        proposal = store.open_proposal(
            workspace_revision=workspace.revision,
            changes=[
                ProposalChange(
                    entity="card",
                    action=ChangeAction.UPDATE,
                    entity_id=card.id,
                    expected_version=card.version,
                    values={
                    "categories": ["people", "rest"],
                    "energy_types": ["physical", "emotional"],
                    },
                )
            ],
        )
        await session.commit()
        proposal_id = proposal.id

    message = FakeMessage(61, bot_message=True)
    await render_proposal(message, services_for(sessions, reviews=store), proposal_id)
    text, markup = message.edits[-1]

    assert text.startswith("<b>✏️ Update Action</b>")
    assert "<i>Kind:</i> ⭐️ Action" in text
    assert "<i>Title:</i> Evening walk" in text
    # What the edit leaves as it is stays off the screen.
    assert "<i>Effort:</i>" not in text and "<i>Note:</i>" not in text
    assert "<i>Categories:</i> <s>🌱 Growth</s>, <b>🫂 People</b>, <b>🔋 Rest</b>" in text
    assert "<i>Energy:</i> <b>💪 Physical</b>, <b>🎭 Emotional</b>" in text
    buttons = button_texts(markup)
    assert buttons == ["✅ Update", "🗑 Discard"]
    assert "↩️ Back" not in buttons


@pytest.mark.parametrize(
    "parent_depth, depth, marker",
    [(0, 0, "🎯"), (1, 0, "🎯"), (0, 1, "🎯²"), (0, 3, "🎯⁴")],
)
async def test_cd_tree_002_the_review_screen_shows_the_goal_becoming_a_subgoal(sessions, parent_depth, depth, marker) -> None:
    """CD-TREE-002 — tests/brd/cards.feature"""
    store = ProposalStore()
    async with sessions() as session:
        health = await create_card(session, kind="goal", title="Health")
        leaf = health
        for level in range(depth):
            leaf = await create_card(session, kind="goal", title=f"Child {level}", parent_ids={leaf.id})
        ancestor = None
        for level in range(parent_depth):
            ancestor = await create_card(
                session,
                kind="goal",
                title=f"Ancestor {level}",
                parent_ids={ancestor.id} if ancestor else set(),
            )
        life = await create_card(
            session, kind="goal", title="Life", parent_ids={ancestor.id} if ancestor else set()
        )
        workspace = await session.get(Workspace, 1)
        proposal = store.open_proposal(
            workspace_revision=workspace.revision,
            changes=[
                ProposalChange(
                    entity="card",
                    action=ChangeAction.LINK,
                    entity_id=health.id,
                    expected_version=health.version,
                    values={"parents": life.id},
                )
            ],
        )
        await session.commit()
        proposal_id = proposal.id
        health_id, life_id = health.id, life.id

    message = FakeMessage(62, bot_message=True)
    await render_proposal(message, services_for(sessions, reviews=store), proposal_id)
    text, _markup = message.edits[-1]
    assert f"<i>Kind:</i> {marker} Subgoal" in text
    assert "Goal</s>" not in text
    assert "<i>Parents:</i> <b>Life</b>" in text

    async with sessions() as session:
        await approve_proposal(session, store, PROPOSALS, proposal_id)
        await session.commit()
    async with sessions() as session:
        health = await session.get(Card, health_id)
        assert (health.kind, await card_parent_ids(session, health_id)) == ("goal", [life_id])


async def test_card_check_link_proposal_shows_the_check_in_overview_and_diff(sessions) -> None:
    store = ProposalStore()
    async with sessions() as session:
        card = await create_card(session, kind="goal", title="Be healthy")
        check = await create_check(session, title="Walk upright")
        workspace = await session.get(Workspace, 1)
        proposal = store.open_proposal(
            workspace_revision=workspace.revision,
            changes=[
                ProposalChange(
                    entity="card",
                    action=ChangeAction.LINK,
                    entity_id=card.id,
                    expected_version=card.version,
                    values={"checks": [check.id]},
                )
            ],
        )
        await session.commit()
        proposal_id = proposal.id

    message = FakeMessage(64, bot_message=True)
    await render_proposal(message, services_for(sessions, reviews=store), proposal_id)
    text, _ = message.edits[-1]

    assert "<i>Checks:</i> <b>Walk upright</b>" in text


async def test_diary_proposal_shows_the_entry_itself_and_only_save_or_discard(
    sessions,
) -> None:
    store = ProposalStore()
    async with sessions() as session:
        # The day it rewrites is saved, with words of its own.
        saved = await create_diary_entry(session, entry_date=date(2026, 8, 15), body="Утро.")
        workspace = await session.get(Workspace, 1)
        proposal = store.open_proposal(
            workspace_revision=workspace.revision,
            changes=[
                ProposalChange(
                    entity="diary",
                    action=ChangeAction.UPDATE,
                    entity_id=saved.id,
                    expected_version=saved.version,
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

    assert text.startswith("<b>✏️ Update Diary entry</b>")
    assert "<i>Date:</i> 2026-08-15" in text
    assert "<i>Feeling:</i> <b>6 🙂</b>" in text
    # Little of the saved day is left, so the new words read whole.
    assert "<i>Entry:</i> <b>Сходил на рынок, вечером стало легче.</b>" in text
    assert "Утро." not in text
    assert "<i>A day that ended better than it began.</i>" in text
    assert button_texts(markup) == ["✅ Update", "🗑 Discard"]


async def test_diary_removal_shows_the_entry_it_would_delete(sessions) -> None:
    store = ProposalStore()
    async with sessions() as session:
        entry = await create_diary_entry(
            session, entry_date=date(2026, 8, 14), body="День, который уходит."
        )
        workspace = await session.get(Workspace, 1)
        proposal = store.open_proposal(
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

    assert text.startswith("<b>🗑 Delete Diary entry</b>")
    assert "This removes that day's entry for good." in text
    # The owner reads what is about to go, plain, not an empty replacement.
    assert "<i>Entry:</i> День, который уходит." in text
    assert "<s>" not in text
    assert button_texts(markup) == ["✅ Delete", "🗑 Discard"]


async def test_card_creation_proposal_reads_plain_and_hides_what_was_not_chosen(
    sessions,
) -> None:
    store = ProposalStore()
    async with sessions() as session:
        workspace = await session.get(Workspace, 1)
        proposal = store.open_proposal(
            workspace_revision=workspace.revision,
            changes=[
                ProposalChange(
                    entity="card",
                    action=ChangeAction.CREATE,
                    values={
                    "kind": "action",
                    "title": "Evening walk",
                    "effort_points": 3,
                    "categories": ["growth"],
                    },
                )
            ],
        )
        await session.commit()
        proposal_id = proposal.id

    message = FakeMessage(62, bot_message=True)
    await render_proposal(message, services_for(sessions, reviews=store), proposal_id)
    text, markup = message.edits[-1]

    assert text.startswith("<b>➕ Create Action</b>")
    assert "<i>Kind:</i> ⭐️ Action" in text
    assert "<i>Title:</i> Evening walk" in text
    assert "<i>Categories:</i> 🌱 Growth" in text
    # Nothing is marked on a new item, and its defaults and blanks say nothing.
    assert "<s>" not in text and "<i>Title:</i> <b>" not in text
    assert "<i>Stage:</i>" not in text and "<i>Priority:</i>" not in text and "<i>Note:</i>" not in text
    assert button_texts(markup) == ["✅ Create", "🗑 Discard"]


async def test_move_proposal_exposes_only_stage_control(sessions) -> None:
    store = ProposalStore()
    async with sessions() as session:
        card = Card(kind="action", title="Evening walk", effort_points=3)
        session.add(card)
        await session.flush()
        workspace = await session.get(Workspace, 1)
        proposal = store.open_proposal(
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

    assert "<i>Stage:</i> <s>Backlog</s> <b>Today</b>" in text
    assert button_texts(markup) == ["✅ Update", "🗑 Discard"]


async def test_saving_card_proposal_applies_every_editable_field(sessions) -> None:
    store = ProposalStore()
    async with sessions() as session:
        parent = Card(kind="goal", title="Be healthy")
        card = Card(kind="action", title="Walk", effort_points=2)
        value = Value(name="Health", order=1)
        tag = Tag(name="Outside")
        session.add_all([parent, card, value, tag])
        await session.flush()
        session.add_all(
            [
                CardCategory(card_id=card.id, category="growth"),
                CardEnergyType(card_id=card.id, energy_type="cognitive"),
            ]
        )
        workspace = await session.get(Workspace, 1)
        nine = "daily 09:00"
        proposal = store.open_proposal(
            workspace_revision=workspace.revision,
            changes=[
                ProposalChange(
                    entity="card",
                    action=ChangeAction.UPDATE,
                    entity_id=card.id,
                    expected_version=card.version,
                    values={
                    "priority": "critical",
                    "schedule": nine,
                    "schedule_rule": await rule_for(session, nine),
                    "blocked_description": "Waiting for access",
                    "effort_points": 5,
                    "parents": [parent.id],
                    "categories": ["rest", "work"],
                    "energy_types": ["physical", "emotional"],
                    "values": [value.id],
                    "tags": [tag.id],
                    },
                )
            ],
        )
        await session.commit()
        proposal_id = proposal.id
        card_id = card.id

    # The review screen says the schedule in words, and how the Scheduler read it.
    message = FakeMessage(63, bot_message=True)
    await render_proposal(message, services_for(sessions, reviews=store), proposal_id)
    text, _markup = message.edits[-1]
    assert "<i>Schedule:</i> <b>daily 09:00</b>" in text
    assert "Every day at 09:00." in text

    async with sessions() as session:
        affected = await approve_proposal(session, store, PROPOSALS, proposal_id)
        await session.commit()

    assert affected == [card_id]
    async with sessions() as session:
        card = await session.get(Card, card_id)
        assert card.schedule == "daily 09:00"
        assert card.schedule_record.rule["timing"]["schedule_kind"] == "daily"
        assert (
            card.priority,
            card.schedule,
            card.blocked,
            card.blocked_description,
            card.effort_points,
            await card_parent_ids(session, card_id),
        ) == (
            "critical",
            "daily 09:00",
            True,
            "Waiting for access",
            5,
            [parent.id],
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
        ) == {"physical", "emotional"}
        assert set(
            await session.scalars(select(CardValue.value_id).where(CardValue.card_id == card_id))
        ) == {value.id}
        assert set(
            await session.scalars(select(CardTag.tag_id).where(CardTag.card_id == card_id))
        ) == {tag.id}


async def test_pr_cite_032_a_review_names_what_it_cites_and_links_none(sessions) -> None:
    """PR-CITE-032 — tests/brd/tg_agent_shell/proposals.feature"""
    store = ProposalStore()
    title = "Spend one quiet evening a week with the family"
    async with sessions() as session:
        value = Value(name="Family", description="Who I live for", order=1)
        session.add(value)
        goal = await create_card(session, kind="goal", title=title)
        workspace = await session.get(Workspace, 1)
        proposal = store.open_proposal(
            workspace_revision=workspace.revision,
            changes=[
                ProposalChange(
                    entity="card",
                    action=ChangeAction.CREATE,
                    values={
                        "kind": "action",
                        "title": "Cook together",
                        "note": (
                            f"For [Family](value:{value.id}) under [the evening](card:{goal.id}),"
                            " not [gone](tag:999)."
                        ),
                    },
                )
            ],
        )
        await session.commit()
        proposal_id = proposal.id

    message = FakeMessage(64, bot_message=True)
    await render_proposal(message, services_for(sessions, reviews=store), proposal_id)
    text, _markup = message.edits[-1]
    assert "<a " not in text
    # The whole title, where an answer would cut it short.
    assert f"<i>Note:</i> For 💎 Family under 🎯 {title} · " in text
    assert ", not gone." in text


def test_pr_screen_003_an_edit_strikes_what_goes_and_bolds_what_comes() -> None:
    """PR-SCREEN-003 — tests/brd/tg_agent_shell/proposals.feature"""
    # Short values read whole, and so does text that loses more than half of itself.
    assert marked("Backlog", "Doing") == "<s>Backlog</s> <b>Doing</b>"
    assert len("One two three four".split()) == SHORT_VALUE_WORDS + 1
    assert marked("One two three four", "Five six seven eight") == "<b>Five six seven eight</b>"
    # Otherwise only the part that changes is marked.
    assert (
        marked("Walk after dinner every day", "Walk 30 min after dinner every day")
        == "Walk <b>30 min</b> after dinner every day"
    )
    assert (
        marked("Call mum at nine every evening", "Call dad at nine every evening")
        == "Call <s>mum</s> <b>dad</b> at nine every evening"
    )
    # A citation is one word: a mark never cuts it apart.
    assert (
        marked(
            "Read [Deep work](card:12) before the team meeting every Monday",
            "Read [Focus](card:13) before the team meeting every Monday",
        )
        == "Read <s>[Deep work](card:12)</s> <b>[Focus](card:13)</b> before the team meeting "
        "every Monday"
    )
    assert marked("Old note here", None) == "<s>Old note here</s>"
    assert marked(None, "A new note") == "<b>A new note</b>"
    assert marked(("Home", "Work"), ("Work", "Rest")) == "<s>Home</s>, Work, <b>Rest</b>"
    assert marked("a < b", "a < c") == "<s>a &lt; b</s> <b>a &lt; c</b>"

    # A new item reads plain and without its blanks; an edit shows its anchors and changes.
    title = ShownField("Title", "Walk", "Walk", anchor=True)
    note = ShownField("Note", None, "x " * (LONG_TEXT_CHARS // 2 + 1))
    stage = ShownField("Stage", "Backlog", "Backlog")
    assert field_line(ShownField("Note"), Effect.CREATE) is None
    assert field_line(note, Effect.CREATE).startswith("<i>Note:</i>\n<blockquote expandable>x x ")
    assert field_line(title, Effect.UPDATE) == "<i>Title:</i> Walk"
    assert field_line(stage, Effect.UPDATE) is None
    assert field_line(ShownField("Stage", "Backlog", None), Effect.DELETE) == "<i>Stage:</i> Backlog"
