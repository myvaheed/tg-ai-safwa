from __future__ import annotations

import pytest
from pydantic import ValidationError

from safwa.ai.contracts import CardToolInput
from safwa.bootstrap.modules import PROPOSALS
from safwa.features.cards.model import Card, CardStage, Priority
from safwa.features.cards.use_cases import (
    create_card,
    finish_action,
    toggle_card_check,
    update_card_fields,
)
from safwa.features.checks.model import Check, CheckOutcome
from safwa.features.checks.use_cases import create_check
from safwa.features.planning.model import Sprint
from safwa.features.proposals.api import ToolPreparationError
from safwa.features.proposals.prepare import ChangePreparer
from safwa.features.saved_requests.model import SavedRequest
from safwa.features.tags.model import Tag
from safwa.features.values.model import Value
from safwa.foundation.marks import live_repeat_instance_id
from safwa.foundation.models import UtcDateTime


@pytest.mark.parametrize("stage", ["done", "cancelled"])
def test_card_move_tool_rejects_a_terminal_stage(stage):
    # move applies through move_card, which does not own completion timestamps,
    # feedback, Sprint results or repeat successors.  complete/cancel do.
    with pytest.raises(ValidationError, match="complete or cancel"):
        CardToolInput(mode="move", id=1, stage=stage)

    assert CardToolInput(mode="move", id=1, stage="today").stage == "today"


@pytest.mark.parametrize("model", [Card, Tag, Value, SavedRequest, Sprint])
def test_user_item_timestamp_columns_are_timezone_aware(model):
    # `UtcDateTime` is what makes a column read back aware; plain `DateTime(timezone=True)`
    # hands a naive value back from SQLite and every reader has to remember to stamp it.
    for field in ("created_at", "updated_at"):
        assert isinstance(model.__table__.columns[field].type, UtcDateTime)


async def test_card_field_updates_reject_an_unknown_priority(sessions):
    async with sessions() as session:
        card = await create_card(session, kind="action", title="Validated", effort_points=1)
        with pytest.raises(ValueError):
            await update_card_fields(session, card.id, {"priority": "urgent"})
        await update_card_fields(session, card.id, {"priority": Priority.CRITICAL})
        assert card.priority == Priority.CRITICAL.value


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
