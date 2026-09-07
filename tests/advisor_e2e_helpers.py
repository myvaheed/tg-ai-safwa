from __future__ import annotations

import json

from llm_gateway import CompletionTurn as ProviderTurn
from llm_gateway import ToolCall as ProviderToolCall
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


def route_turn(name: str) -> ProviderTurn:
    """The Advisor handing its turn to a subagent, which a script says for itself."""
    return ProviderTurn(
        content="",
        tool_calls=(
            ProviderToolCall(
                id=f"route-{name}", name="route", arguments_json=json.dumps({"name": name})
            ),
        ),
    )


def mutation_turn(
    *calls: tuple[str, dict[str, object]], prefix: str = "mutation"
) -> ProviderTurn:
    return ProviderTurn(
        content="",
        tool_calls=tuple(
            ProviderToolCall(
                id=f"{prefix}-{index}", name=name, arguments_json=json.dumps(arguments)
            )
            for index, (name, arguments) in enumerate(calls, start=1)
        ),
    )
