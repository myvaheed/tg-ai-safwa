"""What a proposal may not reach.

Every test here is evidence for one scenario in `tests/brd/proposals.feature`.
"""

from __future__ import annotations

import pytest

from safwa.ai.prepare import ChangePreparer
from safwa.bootstrap.modules import PROPOSALS
from safwa.domain import (
    archive_check,
    archive_subtree,
    create_card,
    create_check,
    finish_action,
    resolve_check,
)
from safwa.features.cards.model import CardStage
from safwa.features.checks.model import CheckOutcome
from safwa.features.proposals.api import ToolPreparationError


async def _refused(session, tool: str, arguments: dict) -> ToolPreparationError:
    change = PROPOSALS.change_from_tool(tool, arguments)
    with pytest.raises(ToolPreparationError) as refused:
        await ChangePreparer(None, None, PROPOSALS).prepare(session, change)  # type: ignore[arg-type]
    return refused.value


async def test_pr_target_001_an_archived_item_is_not_changed_automatically(sessions):
    """PR-TARGET-001 — tests/brd/proposals.feature"""
    async with sessions() as session:
        card = await create_card(
            session, kind="action", title="Walk", effort_points=2, stage="today"
        )
        check = await create_check(session, title="Sat straight?")
        await resolve_check(session, check.id, CheckOutcome.PASSED)
        await archive_check(session, check.id)
        await finish_action(session, card.id, CardStage.DONE)
        await archive_subtree(session, card.id)
        await session.commit()

        for tool, arguments in (
            ("card", {"mode": "update", "id": card.id, "title": "Walk more"}),
            ("check", {"mode": "update", "id": check.id, "title": "Sat straighter?"}),
        ):
            error = await _refused(session, tool, arguments)
            assert error.code == "target_archived"
            assert "is archived" in str(error)
            # The id was right, so the model is not sent looking for it again: it names
            # the item to the owner, who opens it and changes it by hand.
            assert "not changed automatically" in error.hint
            assert "query_safwa" not in error.hint
        assert f"[title](card:{card.id})" in (
            await _refused(session, "card", {"mode": "update", "id": card.id, "title": "x"})
        ).hint


async def test_pr_target_001_an_id_that_matches_nothing_is_a_different_refusal(sessions):
    """PR-TARGET-001 — tests/brd/proposals.feature"""
    async with sessions() as session:
        error = await _refused(session, "card", {"mode": "update", "id": 999, "title": "Ghost"})

        assert error.code == "target_not_found"
        assert str(error) == "Card #999 does not exist."
        assert "Find the current numeric ID with query_safwa" in error.hint
