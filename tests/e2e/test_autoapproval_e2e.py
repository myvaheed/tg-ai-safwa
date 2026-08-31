from __future__ import annotations

import json

import pytest
from sqlalchemy import func, select

from llm_gateway import CompletionTurn as ProviderTurn
from llm_gateway import ToolCall as ProviderToolCall
from safwa.ai.autoapproval import AutoApprovalReviewer
from safwa.ai.outcome import AIOutcomeKind
from safwa.bootstrap.modules import PROPOSALS
from safwa.features.cards.use_cases import create_card
from safwa.features.proposals.model import (
    BatchDecision,
)
from safwa.features.proposals.use_cases import approve_proposal
from safwa.features.tags.use_cases import create_tag
from safwa.features.values.use_cases import create_value
from safwa.models import Card, CardTag, Tag, Value

pytestmark = pytest.mark.e2e


def mutation_turn(*calls: tuple[str, dict[str, object]]) -> ProviderTurn:
    return ProviderTurn(
        content="",
        tool_calls=tuple(
            ProviderToolCall(
                id=f"mutation-{index}", name=name, arguments_json=json.dumps(arguments)
            )
            for index, (name, arguments) in enumerate(calls, start=1)
        ),
    )


def review_turn(name: str, reason: str) -> ProviderTurn:
    return ProviderTurn(
        content="",
        tool_calls=(
            ProviderToolCall(
                id=f"review-{name}",
                name=name,
                arguments_json=json.dumps({"reason": reason}),
            ),
        ),
    )


async def action(e2e_harness, title: str = "Buy milk") -> Card:
    async with e2e_harness.sessions() as session:
        card = await create_card(
            session, kind="action", title=title, stage="backlog", effort_points=1
        )
        await session.commit()
        return card


async def test_an_exact_allowlisted_edit_is_autoapproved(e2e_harness):
    """PR-AUTO-024 — tests/brd/proposals.feature"""
    card = await action(e2e_harness)
    request = "Rename Buy milk to Buy oat milk"
    advisor, provider = e2e_harness.advisor(
        [
            mutation_turn(("card", {"mode": "update", "id": card.id, "title": "Buy oat milk"})),
            review_turn("autoapprove", "The operation and every non-default value are explicit."),
            "Renamed it.",
        ],
        autoapprove=True,
    )

    outcome = await advisor.handle(request)

    assert outcome.kind is AIOutcomeKind.ANSWER
    assert outcome.proposal_id is None
    assert "⚡ Auto-saved" in outcome.message
    assert len(provider.calls) == 3

    reviewer_messages = provider.calls[1]
    assert [message["role"] for message in reviewer_messages] == ["system", "user"]
    assert "may be one part of a longer request" in str(reviewer_messages[0]["content"])
    reviewer_context = json.loads(str(reviewer_messages[1]["content"]))
    assert reviewer_context["user_request"] == request
    assert reviewer_context["operation"] == {
        "entity": "card",
        "action": "update",
        "entity_id": card.id,
    }
    assert reviewer_context["normalized_values"]["title"] == "Buy oat milk"

    resolved = json.loads(
        str(next(message for message in provider.calls[2] if message["role"] == "tool")["content"])
    )
    assert resolved["status"] == "approved"
    assert resolved["approval_source"] == "auto"
    # The user pressed nothing, so the model must not report this as their decision.
    assert resolved["next"].startswith("Safwa saved this one itself")
    async with e2e_harness.sessions() as session:
        stored = await session.get(Card, card.id)
        proposal = next(iter(e2e_harness.reviews.open_proposals), None)
        assert stored is not None and stored.title == "Buy oat milk"
        assert proposal is None


async def test_creation_is_never_autoapproved(e2e_harness):
    """PR-AUTO-025 — tests/brd/proposals.feature"""
    advisor, provider = e2e_harness.advisor(
        [
            mutation_turn(
                (
                    "card",
                    {
                        "mode": "create",
                        "kind": "action",
                        "title": "Buy milk",
                        "effort_points": 1,
                    },
                )
            )
        ],
        autoapprove=True,
    )

    outcome = await advisor.handle("Create an Action named Buy milk with effort 1")

    assert outcome.kind is AIOutcomeKind.PROPOSAL
    # The reviewer is not even consulted: creation is not on the allowlist.
    assert len(provider.calls) == 1
    async with e2e_harness.sessions() as session:
        assert await session.scalar(select(func.count(Card.id))) == 0


async def test_reviewer_doubt_leaves_the_original_proposal_pending(e2e_harness):
    """PR-AUTO-026 — tests/brd/proposals.feature"""
    async with e2e_harness.sessions() as session:
        tag = await create_tag(session, name="Work")
        await session.commit()
    advisor, provider = e2e_harness.advisor(
        [
            mutation_turn(("tag", {"mode": "update", "id": tag.id, "name": "Maybe work"})),
            review_turn("require_review", "The requested name is not exact enough."),
        ],
        autoapprove=True,
    )

    outcome = await advisor.handle("Give the work tag a better name")

    assert outcome.kind is AIOutcomeKind.PROPOSAL
    assert outcome.proposal_id is not None
    assert len(provider.calls) == 2
    async with e2e_harness.sessions() as session:
        assert (await session.get(Tag, tag.id)).name == "Work"
        proposal = advisor.reviews.proposal(outcome.proposal_id)
        assert proposal is not None


async def test_batch_is_reviewed_head_first_without_a_bulk_block(e2e_harness):
    """PR-AUTO-027 — tests/brd/proposals.feature"""
    async with e2e_harness.sessions() as session:
        tag = await create_tag(session, name="Work")
        value = await create_value(session, name="Freedom")
        await session.commit()
    advisor, provider = e2e_harness.advisor(
        [
            mutation_turn(
                ("tag", {"mode": "update", "id": tag.id, "name": "Career"}),
                ("value", {"mode": "update", "id": value.id, "name": "Autonomy"}),
            ),
            review_turn("autoapprove", "The requested Tag name is exact."),
            review_turn("require_review", "The requested Value needs manual review."),
        ],
        autoapprove=True,
    )

    outcome = await advisor.handle("Rename the Work tag to Career and the Freedom value")

    assert outcome.kind is AIOutcomeKind.PROPOSAL
    assert outcome.proposal_id is not None
    assert len(provider.calls) == 3
    async with e2e_harness.sessions() as session:
        assert (await session.get(Tag, tag.id)).name == "Career"
        assert (await session.get(Value, value.id)).name == "Freedom"
        batch = next(iter(advisor.reviews.open_batches), None)
        assert batch is not None
        assert [item.decision for item in batch.state.items] == [
            "approved",
            "pending",
        ]
        assert batch.tool_calls[0]["result"]["approval_source"] == "auto"


async def test_next_head_is_autoapproved_after_a_manual_save(e2e_harness):
    """PR-AUTO-027 — tests/brd/proposals.feature"""
    async with e2e_harness.sessions() as session:
        tag = await create_tag(session, name="Work")
        value = await create_value(session, name="Freedom")
        await session.commit()
    advisor, provider = e2e_harness.advisor(
        [
            mutation_turn(
                ("tag", {"mode": "update", "id": tag.id, "name": "Career"}),
                ("value", {"mode": "update", "id": value.id, "name": "Autonomy"}),
            ),
            review_turn("require_review", "Keep the first item manual."),
            review_turn("autoapprove", "The second item is an exact request match."),
            "Both items are renamed.",
        ],
        autoapprove=True,
    )
    first = await advisor.handle("Rename the Work tag and the Freedom value")
    assert first.proposal_id is not None
    async with e2e_harness.sessions() as session:
        affected = await approve_proposal(session, advisor.reviews, PROPOSALS, first.proposal_id)
        await session.commit()

    outcome = await advisor.resolve_approval(
        first.proposal_id,
        decision=BatchDecision.APPROVED,
        result={"affected_ids": affected},
    )

    assert outcome is not None and outcome.kind is AIOutcomeKind.ANSWER
    assert "⚡ Auto-saved" in outcome.message
    assert len(provider.calls) == 4
    async with e2e_harness.sessions() as session:
        assert (await session.get(Tag, tag.id)).name == "Career"
        assert (await session.get(Value, value.id)).name == "Autonomy"


async def test_multi_step_request_can_be_autoapproved_one_proposal_at_a_time(e2e_harness):
    """PR-AUTO-027 — tests/brd/proposals.feature"""
    card = await action(e2e_harness, "Enter university")
    async with e2e_harness.sessions() as session:
        tag = await create_tag(session, name="Study")
        await session.commit()
    request = "Rename Enter university to Enrol, then link the Study tag to it"
    advisor, provider = e2e_harness.advisor(
        [
            mutation_turn(("card", {"mode": "update", "id": card.id, "title": "Enrol"})),
            review_turn("autoapprove", "The rename is one correct requested part."),
            mutation_turn(("card", {"mode": "link", "id": card.id, "tag_id": tag.id})),
            review_turn("autoapprove", "The requested Tag is linked to the requested Card."),
            "The rename and the link are ready.",
        ],
        autoapprove=True,
    )

    outcome = await advisor.handle(request)

    assert outcome.kind is AIOutcomeKind.ANSWER
    assert len(provider.calls) == 5
    for call_index in (1, 3):
        context = json.loads(str(provider.calls[call_index][1]["content"]))
        assert context["user_request"] == request
    async with e2e_harness.sessions() as session:
        assert (await session.get(Card, card.id)).title == "Enrol"
        assert await session.get(CardTag, (card.id, tag.id)) is not None


async def test_non_allowlisted_operation_does_not_call_the_reviewer(e2e_harness):
    """PR-AUTO-025 — tests/brd/proposals.feature"""
    card = await action(e2e_harness)

    advisor, provider = e2e_harness.advisor(
        [mutation_turn(("card", {"mode": "move", "id": card.id, "stage": "today"}))],
        autoapprove=True,
    )

    outcome = await advisor.handle("Move Buy milk to Today")

    assert outcome.kind is AIOutcomeKind.PROPOSAL
    assert len(provider.calls) == 1
    async with e2e_harness.sessions() as session:
        stored = await session.get(Card, card.id)
        assert stored is not None and stored.effective_stage == "backlog"


async def test_autoapproval_that_cannot_decide_leaves_the_screen_standing(e2e_harness):
    """PR-AUTO-026 — tests/brd/proposals.feature

    The branch exists so a failure here costs the owner a button press, not their data.
    """
    card = await action(e2e_harness)
    advisor, provider = e2e_harness.advisor(
        [mutation_turn(("card", {"mode": "update", "id": card.id, "title": "Buy oat milk"}))],
        autoapprove=True,
    )

    class UnreachableProvider:
        async def complete(self, _request):
            raise RuntimeError("the reviewer is unreachable")

    advisor.materializer.autoapproval = AutoApprovalReviewer(UnreachableProvider())

    outcome = await advisor.handle("Rename Buy milk to Buy oat milk")

    assert outcome.kind is AIOutcomeKind.PROPOSAL
    assert outcome.proposal_id is not None
    assert len(provider.calls) == 1
    async with e2e_harness.sessions() as session:
        assert (await session.get(Card, card.id)).title == "Buy milk"
        proposal = advisor.reviews.proposal(outcome.proposal_id)
        assert proposal is not None


async def test_the_third_in_a_queue_is_not_read_while_the_second_is_on_screen(e2e_harness):
    """PR-AUTO-027 — tests/brd/proposals.feature"""
    async with e2e_harness.sessions() as session:
        tag = await create_tag(session, name="Work")
        value = await create_value(session, name="Freedom")
        await session.commit()
    card = await action(e2e_harness)
    advisor, provider = e2e_harness.advisor(
        [
            mutation_turn(
                ("tag", {"mode": "update", "id": tag.id, "name": "Career"}),
                ("value", {"mode": "update", "id": value.id, "name": "Autonomy"}),
                ("card", {"mode": "update", "id": card.id, "title": "Buy oat milk"}),
            ),
            review_turn("autoapprove", "The requested Tag name is exact."),
            review_turn("require_review", "The requested Value needs manual review."),
        ],
        autoapprove=True,
    )

    outcome = await advisor.handle("Rename the Work tag, the Freedom value and Buy milk")

    assert outcome.kind is AIOutcomeKind.PROPOSAL
    # One mutation turn and two reviews: the third was never put in front of autoapproval.
    assert len(provider.calls) == 3
    async with e2e_harness.sessions() as session:
        assert (await session.get(Tag, tag.id)).name == "Career"
        assert (await session.get(Value, value.id)).name == "Freedom"
        assert (await session.get(Card, card.id)).title == "Buy milk"
        batch = next(iter(advisor.reviews.open_batches), None)
        assert batch is not None
        assert [item.decision for item in batch.state.items] == ["approved", "pending", "pending"]
