from __future__ import annotations

import json

import pytest
from sqlalchemy import func, select

from safwa.ai.context import DialogueMessage
from safwa.ai.provider import ProviderToolCall, ProviderTurn
from safwa.ai.service import ProposalService
from safwa.domain import create_card
from safwa.enums import ProposalStatus
from safwa.models import AgentStep, Card, CardTag, ChangeProposal, Tag, Value

pytestmark = pytest.mark.e2e


def mutation_turn(*calls: tuple[str, dict[str, object]]) -> ProviderTurn:
    return ProviderTurn(
        content="",
        tool_calls=tuple(
            ProviderToolCall(
                id=f"mutation-{index}", name=name, arguments=json.dumps(arguments)
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
                arguments=json.dumps({"reason": reason}),
            ),
        ),
    )


async def test_exact_allowlisted_creation_is_autoapproved(e2e_harness):
    request = "Create an Action named Buy milk with effort 1"
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
            ),
            review_turn("autoapprove", "The operation and every non-default value are explicit."),
            "Created it.",
        ],
        autoapprove=True,
    )

    outcome = await advisor.handle(request)

    assert outcome.kind == "answer"
    assert outcome.proposal_id is None
    assert "⚡ Auto-saved" in outcome.message
    assert len(provider.calls) == 3

    reviewer_messages = provider.calls[1]
    assert [message["role"] for message in reviewer_messages] == ["system", "user"]
    assert "One proposal may implement only one part" in str(reviewer_messages[0]["content"])
    reviewer_context = json.loads(str(reviewer_messages[1]["content"]))
    assert reviewer_context["owner_request"] == request
    assert reviewer_context["operation"] == {
        "entity": "card",
        "action": "create",
        "entity_id": None,
    }
    assert reviewer_context["normalized_values"]["title"] == "Buy milk"

    resolved = json.loads(
        str(next(message for message in provider.calls[2] if message["role"] == "tool")["content"])
    )
    assert resolved["status"] == "approved"
    assert resolved["approval_source"] == "auto"
    assert resolved["autoapproval_reason"].startswith("The operation")
    async with e2e_harness.sessions() as session:
        card = await session.scalar(select(Card))
        proposal = await session.scalar(select(ChangeProposal))
        assert card is not None and card.title == "Buy milk"
        assert proposal is not None and proposal.status == ProposalStatus.APPROVED.value


async def test_reviewer_doubt_leaves_the_original_proposal_pending(e2e_harness):
    advisor, provider = e2e_harness.advisor(
        [
            mutation_turn(("tag", {"mode": "create", "name": "Maybe work"})),
            review_turn("require_review", "The requested name is not exact enough."),
        ],
        autoapprove=True,
    )

    outcome = await advisor.handle("Create a suitable work tag")

    assert outcome.kind == "proposal"
    assert outcome.proposal_id is not None
    assert len(provider.calls) == 2
    async with e2e_harness.sessions() as session:
        assert await session.scalar(select(func.count(Tag.id))) == 0
        proposal = await session.get(ChangeProposal, outcome.proposal_id)
        assert proposal is not None and proposal.status == ProposalStatus.PENDING.value


async def test_batch_is_reviewed_head_first_without_a_bulk_block(e2e_harness):
    advisor, provider = e2e_harness.advisor(
        [
            mutation_turn(
                ("tag", {"mode": "create", "name": "Work"}),
                ("value", {"mode": "create", "name": "Freedom"}),
            ),
            review_turn("autoapprove", "The requested Tag is exact."),
            review_turn("require_review", "The requested Value needs manual review."),
        ],
        autoapprove=True,
    )

    outcome = await advisor.handle("Create the Work tag and the Freedom value")

    assert outcome.kind == "proposal"
    assert outcome.proposal_id is not None
    assert len(provider.calls) == 3
    async with e2e_harness.sessions() as session:
        assert await session.scalar(select(func.count(Tag.id))) == 1
        assert await session.scalar(select(func.count(Value.id))) == 0
        batch = await session.scalar(select(AgentStep).where(AgentStep.kind == "approval_batch"))
        assert batch is not None
        assert [item["status"] for item in batch.metadata_json["queue"]] == [
            "approved",
            "pending",
        ]
        assert batch.metadata_json["tool_calls"][0]["result"]["approval_source"] == "auto"


async def test_next_head_is_autoapproved_after_a_manual_save(e2e_harness):
    advisor, provider = e2e_harness.advisor(
        [
            mutation_turn(
                ("tag", {"mode": "create", "name": "Work"}),
                ("value", {"mode": "create", "name": "Freedom"}),
            ),
            review_turn("require_review", "Keep the first item manual."),
            review_turn("autoapprove", "The second item is an exact request match."),
            "Both items are saved.",
        ],
        autoapprove=True,
    )
    first = await advisor.handle("Create the Work tag and the Freedom value")
    assert first.proposal_id is not None
    async with e2e_harness.sessions() as session:
        affected = await ProposalService(session).apply(first.proposal_id)
        await session.commit()

    outcome = await advisor.resolve_approval(
        "proposal",
        first.proposal_id,
        decision="approved",
        result={"affected_ids": affected},
        dialogue=[DialogueMessage(role="user", content="Create the two items")],
    )

    assert outcome is not None and outcome.kind == "answer"
    assert "⚡ Auto-saved" in outcome.message
    assert len(provider.calls) == 4
    async with e2e_harness.sessions() as session:
        assert await session.scalar(select(func.count(Tag.id))) == 1
        assert await session.scalar(select(func.count(Value.id))) == 1


async def test_multi_step_request_can_be_autoapproved_one_proposal_at_a_time(e2e_harness):
    request = "Create the Goal Enter university, create the Study tag, and link it to the Goal"
    advisor, provider = e2e_harness.advisor(
        [
            mutation_turn(
                ("card", {"mode": "create", "kind": "goal", "title": "Enter university"})
            ),
            review_turn("autoapprove", "Creating this Goal is one correct requested part."),
            mutation_turn(("tag", {"mode": "create", "name": "Study"})),
            review_turn("autoapprove", "Creating this Tag is one correct requested part."),
            mutation_turn(("card", {"mode": "link", "id": 1, "tag_id": 1})),
            review_turn("autoapprove", "The requested Tag is linked to the requested Goal."),
            "The Goal, Tag, and link are ready.",
        ],
        autoapprove=True,
    )

    outcome = await advisor.handle(request)

    assert outcome.kind == "answer"
    assert len(provider.calls) == 7
    for call_index in (1, 3, 5):
        context = json.loads(str(provider.calls[call_index][1]["content"]))
        assert context["owner_request"] == request
    async with e2e_harness.sessions() as session:
        card = await session.scalar(select(Card))
        tag = await session.scalar(select(Tag))
        assert card is not None and card.title == "Enter university"
        assert tag is not None and tag.name == "Study"
        assert await session.get(CardTag, (card.id, tag.id)) is not None


async def test_non_allowlisted_operation_does_not_call_the_reviewer(e2e_harness):
    async with e2e_harness.sessions() as session:
        card = await create_card(
            session,
            kind="action",
            title="Buy milk",
            stage="backlog",
            effort_points=1,
        )
        await session.commit()

    advisor, provider = e2e_harness.advisor(
        [mutation_turn(("card", {"mode": "move", "id": card.id, "stage": "today"}))],
        autoapprove=True,
    )

    outcome = await advisor.handle("Move Buy milk to Today")

    assert outcome.kind == "proposal"
    assert len(provider.calls) == 1
    async with e2e_harness.sessions() as session:
        stored = await session.get(Card, card.id)
        assert stored is not None and stored.effective_stage == "backlog"
