"""How a queue's kept Saves are written: together, once the queue ends, all or none."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from safwa.bootstrap.modules import PROPOSALS
from safwa.features.cards.model import Card
from safwa.features.cards.use_cases import CARD_CREATED, create_card
from safwa.features.tags.model import Tag
from tg_agent_shell.cues.initiatives import CommittedSink, bind_committed
from tg_agent_shell.foundation.changes import Committed
from tg_agent_shell.hooks.registry import HookRegistry
from tg_agent_shell.proposals.model import BatchDecision, ChangeProposal, QueueItem
from tg_agent_shell.proposals.prepare import ChangePreparer
from tg_agent_shell.proposals.store import ProposalStore
from tg_agent_shell.proposals.use_cases import (
    NOT_WRITTEN,
    decide_batch_item,
    open_batch,
    prepare_change,
)


async def _proposal(session, reviews: ProposalStore, tool: str, arguments: dict) -> ChangeProposal:
    preparer = ChangePreparer(None, None, PROPOSALS)  # type: ignore[arg-type]
    return reviews.open_proposal(
        message="Safwa proposed this",
        workspace_revision=(await preparer.world(session)).revision,
        changes=[
            await prepare_change(session, preparer, PROPOSALS.change_from_tool(tool, arguments))
        ],
    )


def _queue(reviews: ProposalStore, *proposals: ChangeProposal):
    batch = open_batch(
        run_id=1,
        items=[
            QueueItem(proposal_id=proposal.id, call_ids=(f"call-{proposal.id}",))
            for proposal in proposals
        ],
        tool_calls=[{"id": f"call-{proposal.id}", "result": None} for proposal in proposals],
        repair_exhausted=False,
    )
    reviews.open_batch(batch)
    return batch


async def _save(sessions, reviews: ProposalStore, proposal_id: int) -> None:
    async with sessions() as session:
        await decide_batch_item(
            session, reviews, PROPOSALS, proposal_id,
            decision=BatchDecision.APPROVED, result={},
            render=lambda tool, decided, payload: {"status": decided.value, **payload},
        )


@pytest.fixture
async def commits(sessions, monkeypatch):
    """What each commit hands the hooks."""
    seen: list[list[Committed]] = []
    monkeypatch.setattr(CommittedSink, "publish", lambda _sink, changes: seen.append(list(changes)))
    sink = bind_committed(sessions, HookRegistry.of(()))
    yield seen
    await sink.close()


async def test_kept_saves_are_written_together_once_the_queue_ends(sessions, commits):
    """PR-SAVE-031 — tests/brd/tg_agent_shell/proposals.feature"""
    reviews = ProposalStore()
    async with sessions() as session:
        made = [
            await _proposal(session, reviews, "action", {"mode": "create", "title": title})
            for title in ("One", "Two", "Three")
        ]
    batch = _queue(reviews, *made)

    await _save(sessions, reviews, made[0].id)
    await _save(sessions, reviews, made[1].id)
    async with sessions() as session:
        assert list(await session.scalars(select(Card.title))) == []
    assert commits == []

    await _save(sessions, reviews, made[2].id)

    async with sessions() as session:
        cards = {card.title: card.id for card in await session.scalars(select(Card))}
    results = [tool["result"] for tool in batch.tool_calls]
    assert [result["status"] for result in results] == ["approved"] * 3
    assert [result["affected_ids"] for result in results] == [
        [cards["One"]], [cards["Two"]], [cards["Three"]],
    ]
    # One commit, and the hooks hear of every Card at once.
    [facts] = commits
    created = [fact.subject_id for fact in facts if fact.kind == CARD_CREATED]
    assert created == [cards["One"], cards["Two"], cards["Three"]]
    assert reviews.open_proposals == () and reviews.open_batches == ()


async def test_one_save_that_fails_leaves_the_whole_queue_unwritten(sessions, commits):
    """PR-FAIL-014 — tests/brd/tg_agent_shell/proposals.feature"""
    reviews = ProposalStore()
    async with sessions() as session:
        first = await _proposal(session, reviews, "action", {"mode": "create", "title": "One"})
        broken = await _proposal(session, reviews, "action", {"mode": "create", "title": "Two"})
        last = await _proposal(session, reviews, "action", {"mode": "create", "title": "Three"})
    # Prepared, then made impossible: the rung does not exist, so its write fails.
    broken.changes[0].values["effort_points"] = 4
    batch = _queue(reviews, first, broken, last)

    for proposal in (first, broken, last):
        await _save(sessions, reviews, proposal.id)

    async with sessions() as session:
        assert list(await session.scalars(select(Card.title))) == []
    assert commits == []
    results = [tool["result"] for tool in batch.tool_calls]
    assert [result["status"] for result in results] == ["failed"] * 3
    assert "error" in results[1]
    assert results[0]["reason"] == results[2]["reason"] == NOT_WRITTEN
    assert [item.decision for item in batch.state.items] == [BatchDecision.FAILED] * 3
    assert reviews.open_proposals == () and reviews.open_batches == ()


async def test_a_save_kept_before_the_workspace_moved_leaves_the_queue_unwritten(sessions):
    """PR-SAVE-031 — tests/brd/tg_agent_shell/proposals.feature"""
    reviews = ProposalStore()
    async with sessions() as session:
        first = await _proposal(session, reviews, "tag", {"mode": "create", "name": "First"})
        second = await _proposal(session, reviews, "tag", {"mode": "create", "name": "Second"})
    batch = _queue(reviews, first, second)
    # Made by hand while the first screen stood: the second is shown after it.
    async with sessions() as session:
        await create_card(session, kind="action", title="By hand")
        await session.commit()

    await _save(sessions, reviews, first.id)
    await _save(sessions, reviews, second.id)

    results = [tool["result"] for tool in batch.tool_calls]
    assert [result["status"] for result in results] == ["failed", "failed"]
    assert "workspace moved on" in results[0]["error"]
    assert results[1]["reason"] == NOT_WRITTEN
    async with sessions() as session:
        assert list(await session.scalars(select(Tag.name))) == []
