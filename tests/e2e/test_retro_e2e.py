"""The retro subagent in a real Advisor turn: sums and means over ended Sprints, and a retro
put on the screen by a subagent.

The real registry, the retro subagent bound from its own declaration and the real database;
only the provider is scripted.
"""

from __future__ import annotations

import json

import pytest
from advisor_e2e_helpers import route_turn
from agent_turns import forward_turn

from llm_gateway import CompletionTurn as ProviderTurn
from llm_gateway import ToolCall as ProviderToolCall
from safwa.features.cards.use_cases import create_card, finish_action
from safwa.features.planning.closing import RetroStatistics
from safwa.features.planning.model import Sprint
from safwa.features.planning.use_cases import finish_sprint, start_sprint
from safwa.features.profile.model import ProfileField
from safwa.features.profile.use_cases import set_profile_field
from safwa.foundation.workspace import Workspace
from tg_agent_shell.ai.outcome import AIOutcomeKind

pytestmark = pytest.mark.e2e


def read_turn(name: str, arguments: dict, call_id: str = "read") -> ProviderTurn:
    return ProviderTurn(
        content="",
        tool_calls=(
            ProviderToolCall(id=call_id, name=name, arguments_json=json.dumps(arguments)),
        ),
    )


def _tool_result(call: list[dict], name: str) -> dict:
    [result] = [
        message for message in call if message.get("role") == "tool" and message.get("name") == name
    ]
    return json.loads(str(result["content"]))


async def _three_ended(harness) -> list[Sprint]:
    """Three Sprints that ended, started with capacity 10, 20 and off, each finishing one
    Action; then a fourth that is still running."""
    ended = []
    async with harness.sessions() as session:
        for capacity in (10, 20, None):
            (await session.get(Workspace, 1)).sprint_capacity_effort_points = capacity
            card = await create_card(
                session, kind="action", title="Ship", stage="sprint", effort_points=2
            )
            sprint = await start_sprint(session, success_criteria="Ship")
            await finish_action(session, card.id)
            await finish_sprint(session)
            ended.append(sprint)
        await create_card(session, kind="action", title="Next", stage="sprint", effort_points=1)
        running = await start_sprint(session, success_criteria="Next")
        await session.commit()
    return [*ended, running]


async def test_rt_ask_013_totals_and_averages_come_from_the_code(e2e_harness):
    """RT-ASK-013 — tests/brd/retro.feature"""
    async with e2e_harness.sessions() as session:
        await set_profile_field(session, ProfileField.EFFORT_TRACKING, True)
        await session.commit()
    *ended, running = await _three_ended(e2e_harness)
    numbers = [sprint.number for sprint in ended]
    words = (
        "They started with 15 EP of capacity on average, over 2 of the 3 Sprints, and "
        "finished 3 Actions in all."
    )
    advisor, provider = e2e_harness.advisor(
        [
            route_turn("retro"),
            read_turn("get_aggregate", {"numbers": numbers, "op": "mean"}, "mean"),
            read_turn("get_aggregate", {"numbers": [*numbers, running.number], "op": "sum"}, "sum"),
            words,
            forward_turn("retro"),
        ],
        subagents=(e2e_harness.subagent("retro"),),
    )

    outcome = await advisor.handle("Average capacity and total Actions over the last 3 Sprints?")

    assert outcome.kind is AIOutcomeKind.ANSWER
    assert outcome.message == words
    # It reads no view: its own three reads, and `open` over retros alone.
    offered = [tool["function"]["name"] for tool in provider.options[1]["tools"]]
    assert offered == ["open", "get_retro_number", "get_retro_data", "get_aggregate"]
    # The newest ended Sprints come after the conversation, each with its retro link.
    block = str(provider.calls[1][-1]["content"])
    for sprint in ended:
        assert f"[Sprint {sprint.number} retro](retro:{sprint.id})" in block
    assert f"Running now: Sprint {running.number}. It has no retro until it ends." in block

    mean = [
        json.loads(str(message["content"]))
        for message in provider.calls[3]
        if message.get("tool_call_id") == "mean"
    ][0]
    assert mean["values"]["capacity"] == 15
    assert mean["counted_over"]["capacity"] == 2
    assert mean["sprint_count"] == 3
    total = [
        json.loads(str(message["content"]))
        for message in provider.calls[3]
        if message.get("tool_call_id") == "sum"
    ][0]
    assert total["values"]["actions_finished"] == 3
    assert total["sprint_count"] == 3
    assert "still running" in total["errors"][running.number]


async def test_rt_open_015_a_retro_named_by_a_date_or_a_number_is_put_on_screen(e2e_harness):
    """RT-OPEN-015 — tests/brd/retro.feature"""
    [sprint, *_] = await _three_ended(e2e_harness)
    async with e2e_harness.sessions() as session:
        first_day = RetroStatistics.from_record((await session.get(Sprint, sprint.id)).retro).first_day
    by_date = [
        read_turn("get_retro_number", {"date": first_day.isoformat()}, "find"),
        read_turn("open", {"item_type": "retro", "id": sprint.id}, "open"),
    ]
    by_number = [read_turn("open", {"item_type": "retro", "id": sprint.id}, "open")]
    for script in (by_date, by_number):
        advisor, provider = e2e_harness.advisor(
            [route_turn("retro"), *script, "Here is its retro.", forward_turn("retro")],
            subagents=(e2e_harness.subagent("retro"),),
        )

        outcome = await advisor.handle("Show me that Sprint's retro")

        # The screen the subagent opened follows Safwa's message, as a link opens it.
        assert outcome.open_item == f"retro-{sprint.id}"
        assert outcome.message.startswith("Here is its retro.")
        assert _tool_result(provider.calls[-2], "open")["opened"] == {
            "item_type": "retro",
            "id": sprint.id,
        }


async def test_ag_open_052_a_subagent_opens_only_the_kinds_it_declared(e2e_harness):
    """AG-OPEN-052 — tests/brd/tg_agent_shell/agents.feature"""
    async with e2e_harness.sessions() as session:
        card = await create_card(session, kind="action", title="Ship", effort_points=1)
        await session.commit()
    advisor, provider = e2e_harness.advisor(
        [
            route_turn("retro"),
            read_turn("open", {"item_type": "card", "id": card.id}, "open"),
            "I can open a retro only.",
            "Anything else?",
        ],
        subagents=(e2e_harness.subagent("retro"), e2e_harness.subagent("sprint")),
    )

    outcome = await advisor.handle("Open the Ship Card")

    refused = _tool_result(provider.calls[2], "open")
    assert refused["status"] == "error"
    assert refused["hint"] == "Use one of: retro."
    assert outcome.open_item is None

    def open_enum(kind: str) -> list[list[str]]:
        return [
            tool["function"]["parameters"]["properties"]["item_type"]["enum"]
            for tool in advisor.adapters.definition(kind).tools
            if tool["function"]["name"] == "open"
        ]

    assert open_enum("retro") == [["retro"]]
    assert open_enum("sprint") == []


async def test_ag_open_054_an_item_not_found_is_left_to_the_subagent_that_opens_its_kind(
    e2e_harness,
):
    """AG-OPEN-054 — tests/brd/tg_agent_shell/agents.feature"""
    missing = {"item_type": "retro", "id": 999}
    advisor, provider = e2e_harness.advisor(
        [read_turn("open", missing, "open"), "I could not find it."],
        subagents=(e2e_harness.subagent("retro"),),
    )

    await advisor.handle("Open the retro of my last Sprint")

    refused = _tool_result(provider.calls[1], "open")
    assert refused["code"] == "not_found"
    assert refused["hint"] == 'route("retro") finds a retro by what the user said and opens it.'

    advisor, provider = e2e_harness.advisor(
        [
            route_turn("retro"),
            read_turn("open", missing, "open"),
            "No Sprint has that retro.",
            forward_turn("retro"),
        ],
        subagents=(e2e_harness.subagent("retro"),),
    )

    await advisor.handle("Open the retro of my last Sprint")

    refused = _tool_result(provider.calls[2], "open")
    assert refused["hint"] == "Find the id with your read tools, then call open again."
