"""The registry and its event adapters, independent of any Safwa condition."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
from hook_helpers import run_hooks

from agent_runtime import AgentDefinition, AgentSession, RunRecord, ToolOutcome
from llm_gateway import ToolCall
from tg_agent_shell.ai.contracts import CALL_HELPER_TOOL
from tg_agent_shell.ai.tools import HelperPort, ToolAdapters
from tg_agent_shell.foundation.screens import ScreenCatalogue
from tg_agent_shell.hooks.contracts import (
    AfterTool,
    AfterTurn,
    HookRegistration,
    HookSpec,
    OfferTool,
    OnAfterTool,
    OnAfterTurn,
    Run,
)
from tg_agent_shell.hooks.registry import HookRegistry


async def candidate(event):
    return ("Use the reader helper.",)


async def operation(payload, context):
    pass


SPEC = HookSpec(
    name="reader.offer", owner="reader", on=(OnAfterTool(tool="query_data"),),
    evaluate=candidate, effect=OfferTool("reader"),
)
EVENT = AfterTool(
    run_id=1, agent="root", agent_kind="advisor", tool="query_data", call_id="q1",
    arguments_json='{"sql": "SELECT 1"}', result=[{"n": 1}], outcome="success",
)
CALL = ToolCall(id=EVENT.call_id, name=EVENT.tool, arguments_json=EVENT.arguments_json)


def catalogue(*registrations):
    return HookRegistry.of(
        registrations, owners=frozenset({"reader"}), helpers=frozenset({"reader", "other"}),
        tools=frozenset({"query_data", "open", "call_helper", "route"}),
    )


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize(
    "bad, reason",
    [
        (replace(SPEC, owner="missing"), "unknown owner"),
        (replace(SPEC, effect=OfferTool("missing")), "unknown helper"),
        (replace(SPEC, on=()), "subscriptions"),
        (replace(SPEC, on=(OnAfterTurn(),)), "incompatible"),
        (replace(SPEC, on=(OnAfterTool("query_data", agent="subagent"),)), "incompatible"),
        (replace(SPEC, on=(OnAfterTool("query_data", outcome="error"),)), "incompatible"),
        (replace(SPEC, effect=Run(operation)), "incompatible"),
        (replace(SPEC, on=(OnAfterTool("absent"),)), "unavailable tool boundary"),
        (replace(SPEC, on=(OnAfterTool("route"),)), "unavailable tool boundary"),
    ],
)
def test_invalid_wiring_is_rejected_even_when_disabled(bad, reason, enabled):
    """AG-HOOK-035 — tests/brd/tg_agent_shell/agents.feature"""
    with pytest.raises(RuntimeError, match=reason):
        catalogue(HookRegistration(bad, enabled))


def test_a_disabled_duplicate_is_still_a_duplicate():
    """AG-HOOK-035 — tests/brd/tg_agent_shell/agents.feature"""
    with pytest.raises(RuntimeError, match="Duplicate"):
        catalogue(HookRegistration(SPEC), HookRegistration(SPEC, False))


async def test_disabled_and_unmatched_hooks_never_check():
    """AG-HOOK-035 — tests/brd/tg_agent_shell/agents.feature"""
    seen = []

    async def check(event):
        seen.append(event)
        return ("Offer.",)

    spec = replace(SPEC, evaluate=check)
    disabled = catalogue(HookRegistration(spec, False))
    assert not [item async for item in disabled.evaluate(EVENT)]
    assert disabled.registrations[0].enabled is False
    enabled = catalogue(HookRegistration(spec))
    for event in (
        replace(EVENT, agent="subagent"), replace(EVENT, outcome="error"),
        replace(EVENT, tool="open"), AfterTurn(42, 42, 1, 0),
    ):
        assert not [item async for item in enabled.evaluate(event)]
    assert seen == []
    assert len([item async for item in enabled.evaluate(EVENT)]) == 1
    assert seen == [EVENT]


async def test_two_subscriptions_still_check_a_hook_once():
    seen = []

    async def check(event):
        seen.append(event)
        return (event,)

    hooks = catalogue(HookRegistration(HookSpec(
        name="service", owner="reader", evaluate=check, effect=Run(operation),
        on=(OnAfterTurn("owner"), OnAfterTurn("system")),
    )))
    event = AfterTurn(42, 42, 1, 0)
    assert len([item async for item in hooks.evaluate(event)]) == 1
    assert seen == [event]


async def test_check_failure_isolated_but_cancellation_propagates():
    async def broken(event):
        raise ValueError("cannot inspect")

    hooks = catalogue(
        HookRegistration(replace(SPEC, name="broken", evaluate=broken)), HookRegistration(SPEC),
    )
    results = [item async for item in hooks.evaluate(EVENT)]
    assert str(results[0].error) == "cannot inspect"
    assert results[1].payloads == ("Use the reader helper.",)

    async def cancelled(event):
        raise asyncio.CancelledError

    hooks = catalogue(HookRegistration(replace(SPEC, evaluate=cancelled)))
    with pytest.raises(asyncio.CancelledError):
        _ = [item async for item in hooks.evaluate(EVENT)]


class Trail:
    async def step(self, *args, **kwargs):
        pass


def adapters(hooks, called):
    async def helper(**kwargs):
        called.append(kwargs)
        return {"rows": [{"n": 1}]}

    return ToolAdapters(
        None, None, None, Trail(), ScreenCatalogue.of(()),
        helpers={"reader": HelperPort(helper), "other": HelperPort(helper)}, hooks=hooks,
    )


async def test_only_the_named_helper_is_granted_and_the_grant_survives_resume():
    """AG-HOOK-036 — tests/brd/tg_agent_shell/agents.feature"""
    called = []
    port = adapters(catalogue(HookRegistration(SPEC)), called)
    definition = AgentDefinition(kind="advisor", tools=(), helper_tool=CALL_HELPER_TOOL)
    agent = AgentSession(run_id=1, tools=(), helper_tool=CALL_HELPER_TOOL)
    rows = [{"n": 1}]
    await port._offer_tools(agent, CALL, ToolOutcome(rows))
    await port._offer_tools(agent, CALL, ToolOutcome([{"n": 1}]))
    assert len(agent.tools) == 1
    assert agent.host_state["offered_helpers"] == ["reader"]
    assert rows == [{"n": 1}, {"notice": "Use the reader helper."}]

    resumed, _ = AgentSession.restore(RunRecord(1, "advisor", state=agent.state()), definition)
    for name, expected in (("other", "helper_not_offered"), ("reader", None)):
        result = await port.call_helper(resumed, ToolCall(
            id=name, name="call_helper", arguments_json=f'{{"name":"{name}","request":"Count."}}',
        ))
        assert result.get("code") == expected
    assert len(called) == 1
    # A new session starts with no offer or helper tool, regardless of the previous one.
    fresh = AgentSession(run_id=2, tools=(), helper_tool=CALL_HELPER_TOOL)
    assert "call_helper" not in fresh.tool_names
    result = await port.call_helper(fresh, ToolCall(
        id="new", name="call_helper", arguments_json='{"name":"reader","request":"Count."}',
    ))
    assert result["code"] == "helper_not_offered"
    assert len(called) == 1


@pytest.mark.parametrize("enabled, agent_role", [(False, "root"), (True, "subagent")])
async def test_no_offer_for_disabled_hook_or_child_session(enabled, agent_role):
    port = adapters(catalogue(HookRegistration(SPEC, enabled)), [])
    agent = AgentSession(
        run_id=2, parent_run_id=1 if agent_role == "subagent" else None,
        tools=(), helper_tool=CALL_HELPER_TOOL,
    )
    rows = [{"n": 1}]
    await port._offer_tools(agent, CALL, ToolOutcome(rows))
    assert rows == [{"n": 1}]
    assert not agent.helper_offered


async def test_optional_bad_offer_keeps_the_original_result():
    async def bad(event):
        event.result.clear()
        return (None,)

    port = adapters(catalogue(HookRegistration(replace(SPEC, evaluate=bad))), [])
    agent = AgentSession(run_id=1, tools=(), helper_tool=CALL_HELPER_TOOL)
    rows = [{"n": 1}]
    await port._offer_tools(agent, CALL, ToolOutcome(rows))
    assert rows == [{"n": 1}]
    assert not agent.helper_offered


async def test_empty_catalogue_has_no_work():
    assert not [item async for item in run_hooks().evaluate(AfterTurn(42, 42, 1, 0))]
