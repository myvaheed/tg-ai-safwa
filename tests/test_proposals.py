"""What a proposal may not reach.

Every test here is evidence for one scenario in `tests/brd/proposals.feature`.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from safwa.ai.prepare import ChangePreparer
from safwa.bootstrap.modules import PROPOSALS
from safwa.domain import (
    StaleStateError,
    archive_check,
    archive_subtree,
    create_card,
    create_check,
    expire_due_sprint,
    finish_action,
    resolve_check,
    start_sprint,
)
from safwa.enums import MessageKind
from safwa.features.cards.model import CardStage
from safwa.features.checks.model import CheckOutcome
from safwa.features.proposals.api import ToolPreparationError
from safwa.features.proposals.model import ChangeProposal
from safwa.features.proposals.store import ProposalStore
from safwa.features.proposals.use_cases import approve_proposal, prepare_proposal
from safwa.models import AgentRun, CallbackToken, Card, TelegramMessage
from safwa.recovery import recover_startup


@pytest.fixture
def reviews() -> ProposalStore:
    """The reviews one running process has open. A fresh store is a fresh process."""
    return ProposalStore()


async def _refused(session, tool: str, arguments: dict) -> ToolPreparationError:
    change = PROPOSALS.change_from_tool(tool, arguments)
    with pytest.raises(ToolPreparationError) as refused:
        await ChangePreparer(None, None, PROPOSALS).prepare(session, change)  # type: ignore[arg-type]
    return refused.value


async def test_pr_target_001_an_archived_item_is_not_changed_automatically(sessions, reviews):
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


async def test_pr_target_001_an_id_that_matches_nothing_is_a_different_refusal(sessions, reviews):
    """PR-TARGET-001 — tests/brd/proposals.feature"""
    async with sessions() as session:
        error = await _refused(session, "card", {"mode": "update", "id": 999, "title": "Ghost"})

        assert error.code == "target_not_found"
        assert str(error) == "Card #999 does not exist."
        assert "Find the current numeric ID with query_safwa" in error.hint


async def _proposal_for(
    session, reviews: ProposalStore, tool: str, arguments: dict
) -> ChangeProposal:
    """One prepared change, opened the way a mutation tool call opens it."""
    return await prepare_proposal(
        session,
        reviews,
        ChangePreparer(None, None, PROPOSALS),  # type: ignore[arg-type]
        message="Safwa proposed this",
        change=PROPOSALS.change_from_tool(tool, arguments),
    )


async def test_a_sprint_closing_itself_overnight_refuses_the_waiting_proposal(sessions, reviews):
    """PR-STALE-012 — tests/brd/proposals.feature"""
    async with sessions() as session:
        card = await create_card(
            session, kind="action", title="Walk", effort_points=2, stage="today"
        )
        await start_sprint(
            session,
            success_criteria="Walk every day",
            start_date=date.today() - timedelta(days=14),
            length_days=7,
        )
        await session.commit()

        proposal = await _proposal_for(
            session, reviews, "card", {"mode": "update", "id": card.id, "title": "Walk more"}
        )
        await session.commit()

        assert await expire_due_sprint(session) is not None
        await session.commit()

        with pytest.raises(StaleStateError):
            await approve_proposal(session, reviews, PROPOSALS, proposal.id)
        await session.commit()

    async with sessions() as session:
        assert reviews.proposal(proposal.id) is None
        assert (await session.get(Card, card.id)).title == "Walk"


async def test_a_proposal_does_not_expire_while_its_process_is_running(sessions, reviews):
    """PR-STALE-013 — tests/brd/proposals.feature"""
    async with sessions() as session:
        card = await create_card(
            session, kind="action", title="Walk", effort_points=2, stage="today"
        )
        proposal = await _proposal_for(
            session, reviews, "card", {"mode": "update", "id": card.id, "title": "Walk more"}
        )
        await session.commit()

        # A review carries no age at all: the row is the pending state, nothing more.
        assert not hasattr(proposal, "created_at")
        await approve_proposal(session, reviews, PROPOSALS, proposal.id)
        await session.commit()

    async with sessions() as session:
        assert reviews.proposal(proposal.id) is None
        assert (await session.get(Card, card.id)).title == "Walk more"


async def test_startup_clears_what_an_unanswered_review_left_behind(sessions, reviews):
    """PR-STALE-013 — tests/brd/proposals.feature

    A restart ends the reviews themselves, because they only ever lived in the process
    that opened them.  What outlives them is what pointed at one: the button that would
    still claim, and the screen whose `related_id` would name a review made later.
    """
    async with sessions() as session:
        run = AgentRun(provider="test", model="test", status="awaiting_approval")
        session.add(run)
        session.add(
            CallbackToken(
                token="tok-approve",
                owner_id=42,
                action="proposal_approve",
                payload={"id": 1},
            )
        )
        session.add(
            TelegramMessage(
                chat_id=700,
                message_id=10,
                direction="out",
                kind=MessageKind.APPROVAL.value,
                related_id=1,
            )
        )
        await session.commit()

        await recover_startup(session)
        await session.commit()

    async with sessions() as session:
        screen = await session.scalar(select(TelegramMessage))
        assert await session.get(CallbackToken, "tok-approve") is None
        assert screen.related_id is None
        assert (await session.get(AgentRun, run.id)).status == "abandoned"
