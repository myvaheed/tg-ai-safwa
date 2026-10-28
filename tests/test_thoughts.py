from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from safwa.bootstrap.modules import PROPOSALS
from safwa.features.cards.model import Card, CardStage
from safwa.features.cards.proposal import open_cards
from safwa.features.cards.use_cases import (
    archive_subtree,
    create_card,
    edit_card_schedule,
    finish_card,
    reopen_card,
    toggle_card_check,
    toggle_card_parent,
    toggle_card_tag,
    toggle_card_value,
    update_card_fields,
)
from safwa.features.checks.use_cases import create_check
from safwa.features.tags.use_cases import create_tag
from safwa.features.values.use_cases import create_value
from tg_agent_shell.foundation.errors import DomainError
from tg_agent_shell.proposals.prepare import ChangePreparer


async def test_thought_keeps_only_its_content_after_reload(sessions):
    """CD-THOUGHT-074 — tests/brd/cards.feature"""
    async with sessions() as session:
        thought = await create_card(
            session, kind="thought", title=" A thought ", note=" **Idea** ",
            blocked_description=" Need an answer ",
        )
        thought_id = thought.id
        await session.commit()
        session.expunge_all()
        thought = await session.get(Card, thought_id)
        assert (thought.title, thought.note, thought.blocked_description) == (
            "A thought", "**Idea**", "Need an answer",
        )
        assert thought.manual_stage is thought.effective_stage is thought.priority is None
        assert thought.schedule is thought.effort_points is thought.tracked_mins is None
        assert (thought_id, "A thought") in await open_cards(session)
        await update_card_fields(session, thought_id, {"blocked_description": None, "note": "New"})
        await session.commit()
        assert not thought.blocked and thought.note == "New" and thought.kind == "thought"


@pytest.mark.parametrize("field,value", [
    ("stage", "today"), ("priority", "critical"), ("schedule", "tomorrow"),
    ("effort_points", 1), ("categories", {"work"}), ("energy_types", {"cognitive"}),
])
async def test_thought_creation_refuses_work_fields(sessions, field, value):
    """CD-THOUGHT-074 — tests/brd/cards.feature"""
    async with sessions() as session:
        with pytest.raises(DomainError, match="Thought"):
            await create_card(session, kind="thought", title="Idea", **{field: value})
        assert list(await session.scalars(select(Card))) == []


async def test_thought_has_no_workflow_or_relationships(sessions):
    """CD-THOUGHT-074 — tests/brd/cards.feature"""
    async with sessions() as session:
        thought = await create_card(session, kind="thought", title="Idea")
        goal = await create_card(session, kind="goal", title="Result")
        check = await create_check(session, title="Observed?")
        tag = await create_tag(session, "Context")
        value = await create_value(session, "Health", "Being healthy")
        await session.commit()
        for operation in (
            lambda: finish_card(session, thought.id),
            lambda: reopen_card(session, thought.id),
            lambda: archive_subtree(session, thought.id),
            lambda: edit_card_schedule(session, thought.id, None, None),
            lambda: update_card_fields(session, thought.id, {"priority": "medium"}),
            lambda: toggle_card_parent(session, thought.id, goal.id),
            lambda: toggle_card_parent(session, goal.id, thought.id),
            lambda: toggle_card_check(session, thought.id, check.id),
            lambda: toggle_card_tag(session, thought.id, tag.id),
            lambda: toggle_card_value(session, thought.id, value.id),
        ):
            with pytest.raises(DomainError, match="Thought"):
                await operation()
        assert thought.effective_stage is None and goal.effective_stage == CardStage.BACKLOG


def test_thought_tool_exposes_only_content_and_create_update():
    """CD-THOUGHT-074 — tests/brd/cards.feature"""
    change = PROPOSALS.change_from_tool("thought", {"mode": "create", "title": "Idea"})
    assert change.values == {"kind": "thought", "title": "Idea"}
    for arguments in (
        {"mode": "move", "id": 1, "stage": "today"},
        {"mode": "create", "title": "Idea", "tags": [1]},
        {"mode": "create", "title": "Idea", "stage": "backlog"},
    ):
        with pytest.raises(ValidationError):
            PROPOSALS.change_from_tool("thought", arguments)


@pytest.mark.parametrize("kind,tool", [
    ("thought", "action"), ("thought", "goal"), ("action", "thought"),
    ("goal", "thought"), ("action", "goal"), ("goal", "action"),
])
async def test_each_card_kind_requires_its_own_tool(sessions, kind, tool):
    """CD-THOUGHT-074 — tests/brd/cards.feature"""
    async with sessions() as session:
        card = await create_card(session, kind=kind, title="Original")
        await session.commit()
        change = PROPOSALS.change_from_tool(tool, {"mode": "update", "id": card.id, "title": "New"})
        with pytest.raises(DomainError, match="Card"):
            await ChangePreparer(None, None, PROPOSALS).prepare(session, change)
        assert card.title == "Original" and card.kind == kind


@pytest.mark.parametrize("field", ["stage", "priority"])
async def test_thought_support_preserves_rejection_of_empty_work_field_values(sessions, field):
    async with sessions() as session:
        with pytest.raises(ValueError):
            await create_card(session, kind="action", title="Step", **{field: ""})
        assert list(await session.scalars(select(Card))) == []
