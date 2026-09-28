"""What the engine does around a tool call: who may answer for it, and what it adds.

The adapters are built with nothing but a trail and an empty screen catalogue here: none
of these calls reaches a database, so the roster the rest of `ToolAdapters` needs is not
built either.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import pytest

from agent_runtime import ToolOutcome
from llm_gateway import ToolCall
from tg_agent_shell.ai.contracts import ToolResultStatus
from tg_agent_shell.ai.mini import ReadToolSpec
from tg_agent_shell.ai.tools import (
    AgentSession,
    HelperPort,
    ToolAdapters,
    WatcherFailed,
)
from tg_agent_shell.foundation.screens import ScreenCatalogue
from tg_agent_shell.hooks.contracts import (
    HookSpec,
    OfferTool,
    OnAfterTool,
    OnBeforeTool,
    RefuseTool,
)
from tg_agent_shell.hooks.registry import HookRegistry

READ = ToolCall(id="1", name="read_thing", arguments_json="{}")


class _Trail:
    """The one record of what a session did. Nothing here reads it back."""

    async def step(self, *arguments: Any, **keywords: Any) -> None:
        return None


def _session(ran: list[str]) -> AgentSession:
    async def run(call: ToolCall) -> dict[str, Any]:
        ran.append(call.name)
        return {"rows": "what the tool found"}

    spec = ReadToolSpec(
        schema={"type": "function", "function": {"name": "read_thing"}}, run=run
    )
    return AgentSession(run_id=1, tools=(), read_specs={"read_thing": spec})


@asynccontextmanager
async def _no_session():
    # The hook's switch is read through a session; every policy here reads the name alone.
    yield None


def _adapters(**watchers: Any) -> ToolAdapters:
    return ToolAdapters(_no_session, None, None, _Trail(), ScreenCatalogue.of(()), **watchers)


async def test_ag_tool_033_a_read_that_failed_earns_no_offer() -> None:
    """AG-TOOL-033 — tests/brd/tg_agent_shell/agents.feature"""
    checked = []

    async def offer(event):
        checked.append(event)
        return ("call the helper",)

    hooks = HookRegistry.of(
        (HookSpec(
            name="offer", owner="test", on=(OnAfterTool(tool="query_data"),),
            evaluate=offer, effect=OfferTool("any"),
            title="Offer", description="Offers any helper after a read.",
        ),),
        owners=frozenset({"test"}), helpers=frozenset({"any"}),
        tools=frozenset({"query_data"}),
    )
    adapters = _adapters(
        hooks=hooks,
        helpers={
            "any": HelperPort(
                run=None,  # type: ignore[arg-type]
            )
        }
    )
    session = AgentSession(run_id=1, tools=(), helper_tool={"function": {"name": "call_helper"}})
    call = ToolCall(id="query", name="query_data", arguments_json='{"sql": "SELECT nope FROM ai_cards"}')

    failed = [{"status": ToolResultStatus.ERROR.value, "error": "no such column: nope"}]
    await adapters._offer_tools(session, call, ToolOutcome(failed, succeeded=False))
    assert checked == []
    assert not session.offered_helpers
    rows = [{"n": 1}]
    await adapters._offer_tools(session, call, ToolOutcome(rows))
    assert len(checked) == 1
    assert rows[-1] == {"notice": "call the helper"}
    assert session.offered_helpers == ("any",)


def _refusing(evaluate, *, policy=None) -> ToolAdapters:
    hooks = HookRegistry.of(
        (HookSpec(
            name="refusal", owner="test", on=(OnBeforeTool(tool="read_thing"),),
            evaluate=evaluate, effect=RefuseTool("any"),
            title="Refusal", description="Refuses a read and offers any helper.",
        ),),
        owners=frozenset({"test"}), helpers=frozenset({"any"}),
        tools=frozenset({"read_thing"}),
        **({"policy": policy} if policy is not None else {}),
    )
    return _adapters(
        hooks=hooks,
        helpers={"any": HelperPort(run=None)},  # type: ignore[arg-type]
    )


def _helped_session(ran: list[str]) -> AgentSession:
    session = _session(ran)
    session.helper_tool = {"function": {"name": "call_helper"}}
    return session


async def test_ag_hook_036_a_refusing_hook_stops_the_call_and_grants_its_helper() -> None:
    """AG-HOOK-036 — tests/brd/tg_agent_shell/agents.feature"""
    ran: list[str] = []

    async def too_hard(event):
        return ("Too hard here. call_helper(\"any\", ...) instead.",)

    session = _helped_session(ran)
    outcome = await _refusing(too_hard).run(session, READ)

    assert ran == []
    assert not outcome.succeeded
    assert outcome.result == {
        "status": ToolResultStatus.ERROR.value,
        "code": "refused",
        "notice": "Too hard here. call_helper(\"any\", ...) instead.",
    }
    assert session.offered_helpers == ("any",)


async def test_ag_hook_036_a_refusing_hook_with_nothing_to_say_lets_the_call_run() -> None:
    """AG-HOOK-036 — tests/brd/tg_agent_shell/agents.feature"""
    ran: list[str] = []

    async def fine(event):
        return ()

    session = _helped_session(ran)
    outcome = await _refusing(fine).run(session, READ)

    assert ran == ["read_thing"] and outcome.succeeded
    assert not session.offered_helpers


async def test_ag_hook_036_a_refusing_check_that_fails_ends_the_turn_and_is_named() -> None:
    """AG-HOOK-036 — tests/brd/tg_agent_shell/agents.feature"""
    ran: list[str] = []

    async def broken(event):
        raise RuntimeError("cannot tell")

    with pytest.raises(WatcherFailed) as failure:
        await _refusing(broken).run(_helped_session(ran), READ)

    assert ran == []
    assert "refusal" in str(failure.value) and "before the read_thing" in str(failure.value)
    assert "cannot tell" in str(failure.value)


async def test_ag_hook_036_a_refusal_whose_notice_is_not_words_ends_the_turn() -> None:
    """AG-HOOK-036 — tests/brd/tg_agent_shell/agents.feature"""
    ran: list[str] = []

    async def wordless(event):
        return ("",)

    session = _helped_session(ran)
    with pytest.raises(WatcherFailed) as failure:
        await _refusing(wordless).run(session, READ)

    assert ran == []
    assert "refusal" in str(failure.value) and "not words" in str(failure.value)
    assert not session.offered_helpers


async def test_ag_hook_036_a_refusal_stands_where_its_helper_cannot_be_granted() -> None:
    """AG-HOOK-036 — tests/brd/tg_agent_shell/agents.feature"""
    ran: list[str] = []

    async def too_hard(event):
        return ("Too hard here.",)

    session = _session(ran)
    assert session.helper_tool is None
    outcome = await _refusing(too_hard).run(session, READ)

    assert ran == []
    assert not outcome.succeeded and outcome.result["code"] == "refused"
    assert not session.offered_helpers


async def test_a_switched_off_refusal_lets_the_call_run() -> None:
    """PS-HOOKS-015 — tests/brd/profile.feature"""
    ran: list[str] = []

    async def too_hard(event):
        return ("Too hard.",)

    async def everything_off(session, name):
        return False

    session = _helped_session(ran)
    outcome = await _refusing(too_hard, policy=everything_off).run(session, READ)

    assert ran == ["read_thing"] and outcome.succeeded
    assert not session.offered_helpers
