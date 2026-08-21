from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy import DateTime, func, select

from safwa.ai.contracts import CardToolInput
from safwa.ai.prepare import ChangePreparer
from safwa.bootstrap.modules import PROPOSALS
from safwa.domain import (
    DomainError,
    create_card,
    create_check,
    finish_action,
    live_repeat_instance_id,
    toggle_card_check,
    update_card_fields,
)
from safwa.enums import CardKind, CardStage, CheckOutcome, Priority
from safwa.features.proposals.api import ToolPreparationError
from safwa.models import (
    Card,
    CardCategory,
    CardEnergyType,
    Check,
    SavedRequest,
    Sprint,
    Tag,
    Value,
)


@pytest.mark.parametrize("stage", ["done", "cancelled"])
def test_card_move_tool_rejects_a_terminal_stage(stage):
    # move applies through move_card, which does not own completion timestamps,
    # feedback, Sprint results or repeat successors.  complete/cancel do.
    with pytest.raises(ValidationError, match="complete or cancel"):
        CardToolInput(mode="move", id=1, stage=stage)

    assert CardToolInput(mode="move", id=1, stage="today").stage == "today"


@pytest.mark.parametrize("model", [Card, Tag, Value, SavedRequest, Sprint])
def test_user_item_timestamp_columns_are_timezone_aware(model):
    for field in ("created_at", "updated_at"):
        column_type = model.__table__.columns[field].type
        assert isinstance(column_type, DateTime)
        assert column_type.timezone is True


async def test_reviewed_card_creation_is_one_atomic_domain_write(sessions):
    async with sessions() as session:
        assert await session.scalar(select(func.count(Card.id))) == 0
        card = await create_card(
            session,
            kind=CardKind.ACTION,
            title="Push ups 30 times",
            stage=CardStage.BACKLOG,
            effort_points=2,
            categories={"self"},
            energy_types={"physical"},
        )
        await session.commit()

    async with sessions() as session:
        stored = await session.get(Card, card.id)
        assert stored is not None
        assert stored.title == "Push ups 30 times"
        assert await session.get(
            CardCategory, {"card_id": card.id, "category": "self"}
        ) is not None
        assert await session.get(
            CardEnergyType, {"card_id": card.id, "energy_type": "physical"}
        ) is not None


async def test_blocked_card_requires_description(sessions):
    async with sessions() as session:
        with pytest.raises(DomainError, match="blocked description"):
            await create_card(
                session,
                kind="action",
                title="Waiting",
                effort_points=1,
                blocked=True,
            )

        card = await create_card(
            session,
            kind="action",
            title="Waiting",
            effort_points=1,
            blocked=True,
            blocked_description="Need account access",
        )
        await update_card_fields(session, card.id, {"blocked": False})
        assert card.blocked is False
        assert card.blocked_description == ""


async def test_goal_creation_programmatically_removes_action_only_fields(sessions):
    async with sessions() as session:
        goal = await create_card(
            session,
            kind="goal",
            title="Release VrWalk",
            effort_points=5,
            repeatable=True,
            categories={"work"},
            energy_types={"cognitive"},
        )
        await session.flush()

        assert goal.effort_points is None
        assert goal.repeatable is False
        assert not list(
            await session.scalars(
                select(CardCategory).where(CardCategory.card_id == goal.id)
            )
        )
        assert not list(
            await session.scalars(
                select(CardEnergyType).where(CardEnergyType.card_id == goal.id)
            )
        )


async def test_card_field_updates_reject_an_unknown_priority(sessions):
    async with sessions() as session:
        card = await create_card(session, kind="action", title="Validated", effort_points=1)
        with pytest.raises(ValueError):
            await update_card_fields(session, card.id, {"priority": "urgent"})
        await update_card_fields(session, card.id, {"priority": Priority.CRITICAL})
        assert card.priority == Priority.CRITICAL.value


async def test_user_items_have_typed_timestamps(sessions):
    async with sessions() as session:
        card = await create_card(
            session,
            kind="action",
            title="Timestamped",
            effort_points=1,
        )
        await session.commit()
        await session.refresh(card)

        assert card.created_at is not None
        assert card.updated_at is not None


async def test_no_proposal_may_touch_a_closed_repeat(sessions):
    """The proposal path is the only one that guesses which instance it meant."""
    async with sessions() as session:
        card = await create_card(
            session, kind="action", title="Run", stage="today", effort_points=3, repeatable=True
        )
        check = await create_check(session, title="Posture straight?", repeatable=True)
        await toggle_card_check(session, card.id, check.id)
        result = await finish_action(
            session, card.id, CardStage.DONE, check_outcomes={check.id: CheckOutcome.PASSED}
        )
        await session.commit()
        live_card_id = result.successor_ids[0]
        live_check_id = await live_repeat_instance_id(session, check)
        assert live_check_id is not None

    refusals = [
        ("card", {"mode": "update", "id": card.id, "title": "Run far"}, live_card_id),
        ("card", {"mode": "move", "id": card.id, "stage": "today"}, live_card_id),
        ("remove", {"mode": "archive", "entity": "card", "id": card.id}, live_card_id),
        ("check", {"mode": "update", "id": check.id, "title": "Posture?"}, live_check_id),
        # Targeting the live Card does not excuse linking the dead Check onto it.
        ("card", {"mode": "link", "id": live_card_id, "check_ids": [check.id]}, live_check_id),
    ]
    for tool, arguments, live_id in refusals:
        change = PROPOSALS.change_from_tool(tool, arguments)
        async with sessions() as session:
            with pytest.raises(ToolPreparationError) as refused:
                await ChangePreparer(None, None, PROPOSALS).prepare(  # type: ignore[arg-type]
                    session, change
                )
        assert refused.value.code == "closed_repeat", arguments
        assert f"#{live_id}" in refused.value.hint, arguments

    async with sessions() as session:
        change = PROPOSALS.change_from_tool(
            "card", {"mode": "update", "id": live_card_id, "check_ids": [live_check_id]}
        )
        prepared = await ChangePreparer(None, None, PROPOSALS).prepare(  # type: ignore[arg-type]
            session, change
        )
        assert prepared.values["check_ids"] == [live_check_id]
        assert await session.get(Check, live_check_id) is not None
