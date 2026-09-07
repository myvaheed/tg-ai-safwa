"""What a proposal may not reach.

Every test here is evidence for one scenario in `tests/brd/proposals.feature`.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from safwa.bootstrap.modules import PROPOSALS
from safwa.bootstrap.recovery import recover_startup
from safwa.features.cards.model import Card, CardStage
from safwa.features.cards.use_cases import archive_subtree, create_card, finish_action
from safwa.features.checks.model import CheckOutcome
from safwa.features.checks.use_cases import archive_check, create_check, resolve_check
from safwa.features.planning.use_cases import expire_due_sprint, start_sprint
from tg_agent_shell.ai.autoapproval import AutoApprovalCandidate, AutoApprovalVerdict
from tg_agent_shell.ai.outcome import AIOutcome, AIOutcomeKind
from tg_agent_shell.ai.runs import AgentRun
from tg_agent_shell.foundation.errors import StaleStateError
from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.history import TelegramMessage
from tg_agent_shell.proposals.api import ToolPreparationError
from tg_agent_shell.proposals.materialize import ProposalMaterializer
from tg_agent_shell.proposals.model import ChangeProposal, QueueItem
from tg_agent_shell.proposals.prepare import ChangePreparer
from tg_agent_shell.proposals.render import ProposalRenderer
from tg_agent_shell.proposals.store import ProposalStore
from tg_agent_shell.proposals.use_cases import (
    approve_proposal,
    decide_batch_item,
    open_batch,
    prepare_proposal,
)
from tg_agent_shell.telegram.model import CallbackToken


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
    """PR-TARGET-001 — tests/brd/tg_agent_shell/proposals.feature"""
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
            assert "query_data" not in error.hint
        assert f"[title](card:{card.id})" in (
            await _refused(session, "card", {"mode": "update", "id": card.id, "title": "x"})
        ).hint


async def test_pr_target_001_an_id_that_matches_nothing_is_a_different_refusal(sessions, reviews):
    """PR-TARGET-001 — tests/brd/tg_agent_shell/proposals.feature"""
    async with sessions() as session:
        error = await _refused(session, "card", {"mode": "update", "id": 999, "title": "Ghost"})

        assert error.code == "target_not_found"
        assert str(error) == "Card #999 does not exist."
        assert "Find the current numeric ID with query_data" in error.hint


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
    """PR-STALE-012 — tests/brd/tg_agent_shell/proposals.feature"""
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
    """PR-STALE-013 — tests/brd/tg_agent_shell/proposals.feature"""
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
    """PR-STALE-013 — tests/brd/tg_agent_shell/proposals.feature

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


async def test_pr_fail_014_a_commit_that_fails_leaves_a_review_the_owner_can_still_answer(
    sessions, reviews, monkeypatch
):
    """PR-FAIL-014 — tests/brd/tg_agent_shell/proposals.feature"""
    async with sessions() as session:
        card = await create_card(
            session, kind="action", title="Walk", effort_points=2, stage="today"
        )
        card_id = card.id
        proposal = await _proposal_for(
            session, reviews, "card", {"mode": "update", "id": card_id, "title": "Walk more"}
        )
        await session.commit()

        async def refuse_to_commit() -> None:
            raise RuntimeError("disk I/O error")

        monkeypatch.setattr(session, "commit", refuse_to_commit)
        with pytest.raises(RuntimeError):
            await approve_proposal(session, reviews, PROPOSALS, proposal.id)

    # The write went back, so the review has to still be there: a change that was neither
    # written nor refused would leave the owner a screen they could no longer answer.
    assert reviews.proposal(proposal.id) is not None
    async with sessions() as session:
        assert (await session.get(Card, card_id)).title == "Walk"


class _ApprovesEverything:
    """An autoapproval reviewer that always says yes, so the Save itself is under test."""

    async def review(self, candidate: AutoApprovalCandidate) -> AutoApprovalVerdict:
        return AutoApprovalVerdict(approved=True, reason="Exactly what was asked for.")


def _autoapproving(sessions, reviews: ProposalStore) -> ProposalMaterializer:
    """The real materializer, with the suspended session's resume stood in for.

    Resolving a decision is the store and the use case; what a resumed model then says is
    the runtime's, and this test is about neither of those.
    """

    async def resolve(proposal_id, *, decision, result, apply_proposal=False):
        async with sessions() as session:
            decided = await decide_batch_item(
                session,
                reviews,
                PROPOSALS,
                proposal_id,
                decision=decision,
                apply_change=apply_proposal,
                render=lambda tool, affected: {"status": decision.value},
            )
            if decided is None:
                return None
            await session.commit()
        return None

    return ProposalMaterializer(
        sessions,
        reviews,
        PROPOSALS,
        ProposalRenderer(reviews, PROPOSALS),
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        resolve=resolve,
        autoapproval=_ApprovesEverything(),
    )


async def test_pr_auto_026_a_refused_automatic_save_never_offers_a_review_that_is_gone(
    sessions, reviews
):
    """PR-AUTO-026 — tests/brd/tg_agent_shell/proposals.feature"""
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
    reviews.open_batch(
        open_batch(
            run_id=1,
            items=[QueueItem(proposal_id=proposal.id, call_ids=("call-1",))],
            tool_calls=[{"id": "call-1", "name": "card", "result": None}],
            repair_exhausted=False,
        )
    )

    outcome = await _autoapproving(sessions, reviews).advance_autoapprovals(
        AIOutcome(AIOutcomeKind.PROPOSAL, "Review this.", proposal_id=proposal.id)
    )

    # The refusal ends the review deliberately, so the fallback is words rather than a
    # screen id nothing can draw, and the queue behind it does not stay open on it.
    assert outcome.kind is AIOutcomeKind.ANSWER
    assert reviews.proposal(proposal.id) is None
    assert reviews.batch_for_proposal(proposal.id) is None
    async with sessions() as session:
        assert (await session.get(Card, card.id)).title == "Walk"
