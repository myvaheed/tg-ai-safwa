"""The registry and its event adapters, independent of any Safwa condition."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace

import pytest
from hook_helpers import run_hooks

from agent_runtime import AgentDefinition, AgentSession, RunRecord, ToolOutcome
from llm_gateway import ToolCall
from tg_agent_shell.ai.contracts import CALL_HELPER_TOOL
from tg_agent_shell.ai.tools import HelperPort, ToolAdapters
from tg_agent_shell.foundation.changes import Committed
from tg_agent_shell.foundation.screens import ScreenCatalogue
from tg_agent_shell.hooks.contracts import (
    Advise,
    AfterTool,
    AfterTurn,
    HookSpec,
    HookSwitch,
    OfferTool,
    OnAfterTool,
    OnAfterTurn,
    OnCommitted,
    OnTick,
    Run,
    Tick,
)
from tg_agent_shell.hooks.registry import HookRegistry


async def candidate(event):
    return ("Use the reader helper.",)


async def operation(payload, context):
    pass


async def subject(event):
    return (event.subject_id,)


async def words(session, items):
    return f"About {', '.join(str(item) for item in items)}." if items else None


SPEC = HookSpec(
    name="reader.offer", owner="reader", on=(OnAfterTool(tool="query_data"),),
    evaluate=candidate, effect=OfferTool("reader"),
)
EVENT = AfterTool(
    run_id=1, agent="root", agent_kind="advisor", tool="query_data", call_id="q1",
    arguments_json='{"sql": "SELECT 1"}', result=[{"n": 1}], outcome="success",
)
CALL = ToolCall(id=EVENT.call_id, name=EVENT.tool, arguments_json=EVENT.arguments_json)


SWITCH = HookSwitch(title="Reader offer", description="Offers the reader after a read.")
ADVICE = HookSpec(
    name="reader.advice", owner="reader", on=(OnCommitted(kind="thing.changed"),),
    evaluate=subject, effect=Advise(words), switch=SWITCH,
)
CHANGE = Committed(kind="thing.changed", subject_id=7)


@asynccontextmanager
async def no_session():
    yield None


# What the registry opens a session with; the policies below read only the name.
NO_SESSIONS = no_session


def catalogue(*specs, policy=None):
    return HookRegistry.of(
        specs, owners=frozenset({"reader"}), helpers=frozenset({"reader", "other"}),
        tools=frozenset({"query_data", "open", "call_helper"}),
        **({"policy": policy} if policy is not None else {}),
    )


def switched(*names_off):
    async def policy(session, name):
        return name not in names_off
    return policy


@pytest.mark.parametrize("switch", [None, SWITCH])
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
        (replace(SPEC, effect=Advise(words)), "incompatible"),
        (replace(SPEC, on=(OnCommitted("thing.changed"),)), "incompatible"),
        (replace(SPEC, on=(OnCommitted(" "),), effect=Advise(words)), "incompatible"),
    ],
)
def test_invalid_wiring_is_rejected_with_or_without_a_switch(bad, reason, switch):
    """AG-HOOK-035 — tests/brd/tg_agent_shell/agents.feature"""
    with pytest.raises(RuntimeError, match=reason):
        catalogue(replace(bad, switch=switch), policy=switched(bad.name))


def test_a_switched_off_duplicate_is_still_a_duplicate():
    """AG-HOOK-035 — tests/brd/tg_agent_shell/agents.feature"""
    with pytest.raises(RuntimeError, match="Duplicate"):
        catalogue(SPEC, replace(SPEC, switch=SWITCH), policy=switched(SPEC.name))


async def test_switched_off_and_unmatched_hooks_never_check():
    """AG-HOOK-035 — tests/brd/tg_agent_shell/agents.feature"""
    seen = []

    async def check(event):
        seen.append(event)
        return ("Offer.",)

    spec = replace(SPEC, evaluate=check, switch=SWITCH)
    off = catalogue(spec, policy=switched(spec.name))
    assert not [item async for item in off.evaluate(EVENT, NO_SESSIONS)]
    assert off.switches == (spec,)
    on = catalogue(spec, policy=switched("some.other"))
    for event in (
        replace(EVENT, agent="subagent"), replace(EVENT, outcome="error"),
        replace(EVENT, tool="open"), AfterTurn(42, 42, 1, 0),
    ):
        assert not [item async for item in on.evaluate(event, NO_SESSIONS)]
    assert seen == []
    assert len([item async for item in on.evaluate(EVENT, NO_SESSIONS)]) == 1
    assert seen == [EVENT]


async def test_a_hook_without_a_switch_is_on_whatever_the_policy_says():
    """AG-HOOK-035 — tests/brd/tg_agent_shell/agents.feature"""
    async def never(session, name):
        raise AssertionError("a hook without a switch asks no policy")

    hooks = catalogue(SPEC, policy=never)
    assert hooks.switches == ()
    assert len([item async for item in hooks.evaluate(EVENT, NO_SESSIONS)]) == 1
    # An application with no policy of its own keeps a switch on too.
    assert len([item async for item in catalogue(replace(SPEC, switch=SWITCH)).evaluate(EVENT, NO_SESSIONS)]) == 1


async def test_two_subscriptions_still_check_a_hook_once():
    seen = []

    async def check(event):
        seen.append(event)
        return (event,)

    hooks = catalogue(HookSpec(
        name="service", owner="reader", evaluate=check, effect=Run(operation),
        on=(OnAfterTurn("owner"), OnAfterTurn("system")),
    ))
    event = AfterTurn(42, 42, 1, 0)
    assert len([item async for item in hooks.evaluate(event, NO_SESSIONS)]) == 1
    assert seen == [event]


async def test_check_failure_isolated_but_cancellation_propagates():
    async def broken(event):
        raise ValueError("cannot inspect")

    hooks = catalogue(replace(SPEC, name="broken", evaluate=broken), SPEC)
    results = [item async for item in hooks.evaluate(EVENT, NO_SESSIONS)]
    assert str(results[0].error) == "cannot inspect"
    assert results[1].payloads == ("Use the reader helper.",)

    async def cancelled(event):
        raise asyncio.CancelledError

    hooks = catalogue(replace(SPEC, evaluate=cancelled))
    with pytest.raises(asyncio.CancelledError):
        _ = [item async for item in hooks.evaluate(EVENT, NO_SESSIONS)]


class Trail:
    async def step(self, *args, **kwargs):
        pass


def adapters(hooks, called):
    async def helper(**kwargs):
        called.append(kwargs)
        return {"rows": [{"n": 1}]}

    return ToolAdapters(
        NO_SESSIONS, None, None, Trail(), ScreenCatalogue.of(()),
        helpers={"reader": HelperPort(helper), "other": HelperPort(helper)}, hooks=hooks,
    )


async def test_only_the_named_helper_is_granted_and_the_grant_survives_resume():
    """AG-HOOK-036 — tests/brd/tg_agent_shell/agents.feature"""
    called = []
    port = adapters(catalogue(SPEC), called)
    definition = AgentDefinition(kind="advisor", tools=(), helper_tool=CALL_HELPER_TOOL)
    agent = AgentSession(run_id=1, tools=(), helper_tool=CALL_HELPER_TOOL)
    rows = [{"n": 1}]
    await port._offer_tools(agent, CALL, ToolOutcome(rows))
    await port._offer_tools(agent, CALL, ToolOutcome([{"n": 1}]))
    assert len(agent.tools) == 1
    assert agent.offered_helpers == ("reader",)
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


@pytest.mark.parametrize("on, agent_role", [(False, "root"), (True, "subagent")])
async def test_no_offer_for_a_switched_off_hook_or_child_session(on, agent_role):
    spec = replace(SPEC, switch=SWITCH)
    port = adapters(catalogue(spec, policy=switched(*([] if on else [spec.name]))), [])
    agent = AgentSession(
        run_id=2, parent_run_id=1 if agent_role == "subagent" else None,
        tools=(), helper_tool=CALL_HELPER_TOOL,
    )
    rows = [{"n": 1}]
    await port._offer_tools(agent, CALL, ToolOutcome(rows))
    assert rows == [{"n": 1}]
    assert not agent.offered_helpers


async def test_optional_bad_offer_keeps_the_original_result():
    async def bad(event):
        event.result.clear()
        return (None,)

    port = adapters(catalogue(replace(SPEC, evaluate=bad)), [])
    agent = AgentSession(run_id=1, tools=(), helper_tool=CALL_HELPER_TOOL)
    rows = [{"n": 1}]
    await port._offer_tools(agent, CALL, ToolOutcome(rows))
    assert rows == [{"n": 1}]
    assert not agent.offered_helpers


async def test_empty_catalogue_has_no_work():
    assert not [item async for item in run_hooks().evaluate(AfterTurn(42, 42, 1, 0), NO_SESSIONS)]


async def test_a_committed_change_reaches_the_hook_of_its_kind_only():
    """AG-HOOK-037 — tests/brd/tg_agent_shell/agents.feature"""
    hooks = catalogue(ADVICE, SPEC)
    checked = [item async for item in hooks.evaluate(CHANGE, NO_SESSIONS)]
    assert [(item.spec.name, item.payloads) for item in checked] == [("reader.advice", (7,))]
    assert not [item async for item in hooks.evaluate(replace(CHANGE, kind="other"), NO_SESSIONS)]
    assert not [item async for item in catalogue(ADVICE, policy=switched(ADVICE.name)).evaluate(CHANGE, NO_SESSIONS)]


async def test_a_daily_check_is_registered_at_its_time_and_hears_that_time_only():
    """AG-HOOK-039 — tests/brd/tg_agent_shell/agents.feature"""
    daily = HookSpec(
        name="reader.daily", owner="reader", on=(OnTick(at="09:00"),),
        evaluate=subject, effect=Advise(words),
    )

    async def marker(event):
        return (event.at,)

    hooks = catalogue(replace(daily, evaluate=marker), ADVICE)
    assert hooks.tick_times == frozenset({"09:00"})
    checked = [item async for item in hooks.evaluate(Tick("09:00"), NO_SESSIONS)]
    assert [(item.spec.name, item.payloads) for item in checked] == [("reader.daily", ("09:00",))]
    assert not [item async for item in hooks.evaluate(Tick("21:00"), NO_SESSIONS)]
    with pytest.raises(RuntimeError, match="HH:MM"):
        catalogue(replace(daily, on=(OnTick(at="nine"),)))
    with pytest.raises(RuntimeError, match="incompatible"):
        catalogue(replace(daily, effect=Run(operation)))


async def test_the_words_of_a_request_come_from_the_hook_that_is_still_on():
    """AG-HOOK-038 — tests/brd/tg_agent_shell/agents.feature"""
    hooks = catalogue(ADVICE, SPEC)
    assert await hooks.prepare(NO_SESSIONS, "reader.advice", [7, 9]) == "About 7, 9."
    assert await hooks.prepare(NO_SESSIONS, "reader.advice", []) is None
    assert await hooks.prepare(NO_SESSIONS, "reader.offer", [7]) is None
    assert await hooks.prepare(NO_SESSIONS, "gone", [7]) is None
    off = catalogue(ADVICE, policy=switched(ADVICE.name))
    assert await off.prepare(NO_SESSIONS, "reader.advice", [7]) is None

    async def broken(session, items):
        raise ValueError("cannot read")

    # Failing to say is not nothing to say: the caller keeps the request.
    with pytest.raises(ValueError, match="cannot read"):
        await catalogue(replace(ADVICE, effect=Advise(broken))).prepare(NO_SESSIONS, "reader.advice", [7])
