from __future__ import annotations

import pytest
from sqlalchemy import DateTime, func, select

from safwa.domain import DomainError, create_card, update_card_fields
from safwa.enums import CardKind, CardStage
from safwa.models import Card, CardCategory, CardEnergyType, SavedRequest, Sprint, Tag, Value


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
