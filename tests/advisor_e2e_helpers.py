from __future__ import annotations

from agent_turns import PLAN, mutation_turn, route_turn

from safwa.features.cards.model import Card
from safwa.features.cards.use_cases import create_card


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


__all__ = ["PLAN", "create_manual_card", "mutation_turn", "route_turn"]
