from __future__ import annotations

from agent_turns import PLAN, mutation_turn, route_turn, schedule_turn
from schedule_helpers import create_card

from safwa.features.cards.model import Card
from tg_agent_shell.proposals.model import BatchDecision


async def create_manual_card(session, **overrides) -> Card:
    payload = {
        "title": "Action",
        "kind": "action",
        "stage": "backlog",
        "effort_points": 3,
    }
    payload.update(overrides)
    payload.pop("root_confirmed", None)
    payload.pop("expected_parent_version", None)
    return await create_card(session, **payload)


async def save(advisor, proposal_id, **result):
    """Press Save on a screen a suspended request waits on, the way its button does.

    Returns what the request does next, and the ids this proposal's Save wrote: none while
    screens of its queue still wait, since the queue's Saves are written once it ends.
    """
    batch = advisor.reviews.batch_for_proposal(proposal_id)
    outcome = await advisor.resolve_approval(
        proposal_id, decision=BatchDecision.APPROVED, result=result
    )
    item = batch.state.item_for(proposal_id) if batch is not None else None
    affected = [
        affected_id
        for tool in (batch.tool_calls if item is not None else ())
        if str(tool.get("id")) in item.call_ids
        for affected_id in (tool.get("result") or {}).get("affected_ids", ())
    ]
    return outcome, affected


__all__ = ["PLAN", "create_manual_card", "mutation_turn", "route_turn", "save", "schedule_turn"]
