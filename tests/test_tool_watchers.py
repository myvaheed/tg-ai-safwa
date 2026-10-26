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
from tg_agent_shell.ai.subagents import RoutedSubagent
from tg_agent_shell.ai.tools import (
    WATCHED_TOOLS,
    AgentSession,
    HelperPort,
    ToolAdapters,
    WatcherFailed,
)
from tg_agent_shell.foundation.screens import ScreenCatalogue
from tg_agent_shell.hooks.contracts import (
    BeforeTool,
    HookSpec,
    OfferTool,
    OnAfterTool,
    OnBeforeTool,
    RefuseTool,
    ReviewTool,
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


def _refusing(evaluate, *, policy=None, **adapters: Any) -> ToolAdapters:
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
        **adapters,
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


async def test_a_refusal_written_for_the_root_never_reaches_a_subsession_s_agent() -> None:
    """AG-SUB-072 — tests/brd/tg_agent_shell/agents.feature"""
    # It starts a session of its own, as the root does, and has no helper to be pointed at.
    ran: list[str] = []

    async def too_hard(event):
        return ("Too hard here. call_helper(\"any\", ...) instead.",)

    session = _session(ran)
    session.kind = "coach"
    coach = RoutedSubagent(name="coach", prompt="", title="💬 Budget talk")
    outcome = await _refusing(too_hard, subagents={"coach": coach}).run(session, READ)

    assert ran == ["read_thing"] and outcome.succeeded


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


# A provider is only handed on to the review here; nothing calls it.
PROVIDER = object()


async def _every_call(event):
    return (event,)


def _review_spec(review, evaluate=_every_call, *, tool: str = "read_thing") -> HookSpec:
    return HookSpec(
        name="review", owner="test", on=(OnBeforeTool(tool=tool),),
        evaluate=evaluate, effect=ReviewTool(review),
        title="Review", description="Reviews a call before it runs.",
    )


def _reviewing(review, evaluate=_every_call, *, policy=None) -> ToolAdapters:
    hooks = HookRegistry.of(
        (_review_spec(review, evaluate),),
        owners=frozenset({"test"}), tools=frozenset({"read_thing"}),
        **({"policy": policy} if policy is not None else {}),
    )
    return _adapters(hooks=hooks, provider=PROVIDER)


async def test_ag_hook_063_a_review_that_answers_refuses_the_call_with_its_words() -> None:
    """AG-HOOK-071 — tests/brd/tg_agent_shell/agents.feature"""
    ran: list[str] = []
    read = []

    async def review(event, provider):
        read.append((event, provider))
        return "Not this one. Answer in words."

    session = _helped_session(ran)
    session.dialogue = [{"role": "user", "content": "Plan it with me."}]
    outcome = await _reviewing(review).run(session, READ)

    assert ran == [] and not outcome.succeeded
    assert outcome.result == {
        "status": ToolResultStatus.ERROR.value,
        "code": "refused",
        "notice": "Not this one. Answer in words.",
    }
    ((event, provider),) = read
    assert provider is PROVIDER
    assert (event.tool, event.arguments_json) == ("read_thing", "{}")
    assert "Plan it with me." in event.conversation
    # A refusal on review grants no helper.
    assert not session.offered_helpers


async def test_ag_hook_063_a_review_with_nothing_to_say_lets_the_call_run() -> None:
    """AG-HOOK-071 — tests/brd/tg_agent_shell/agents.feature"""
    ran: list[str] = []

    async def review(event, provider):
        return None

    outcome = await _reviewing(review).run(_session(ran), READ)

    assert ran == ["read_thing"] and outcome.succeeded


async def test_ag_hook_063_a_check_or_review_that_fails_lets_the_call_run_and_is_logged(
    caplog,
) -> None:
    """AG-HOOK-071 — tests/brd/tg_agent_shell/agents.feature"""

    async def broken_review(event, provider):
        raise RuntimeError("model down")

    async def broken_check(event):
        raise RuntimeError("cannot tell")

    async def never(event, provider):
        raise AssertionError("a check that failed is never reviewed")

    for adapters, reason in (
        (_reviewing(broken_review), "model down"),
        (_reviewing(never, broken_check), "cannot tell"),
    ):
        ran: list[str] = []
        caplog.clear()
        outcome = await adapters.run(_session(ran), READ)

        assert ran == ["read_thing"] and outcome.succeeded
        assert "The hook review failed; the read_thing call runs" in caplog.text
        assert reason in caplog.text


async def test_ag_hook_063_sub_may_be_reviewed_and_route_may_not_and_no_switch_stops_it() -> None:
    """AG-HOOK-071 — tests/brd/tg_agent_shell/agents.feature"""

    async def refuse(event, provider):
        return "Not asked."

    hooks = HookRegistry.of(
        (_review_spec(refuse, tool="sub"),), owners=frozenset({"test"}), tools=WATCHED_TOOLS
    )
    assert hooks.listens(BeforeTool)
    with pytest.raises(RuntimeError, match="unavailable tool boundary: route"):
        HookRegistry.of(
            (_review_spec(refuse, tool="route"),), owners=frozenset({"test"}), tools=WATCHED_TOOLS
        )

    # The owner's switches do not reach it: it checks the model's own work.
    async def everything_off(session, name):
        return False

    ran: list[str] = []
    assert not _review_spec(refuse).agent_related
    outcome = await _reviewing(refuse, policy=everything_off).run(_session(ran), READ)
    assert ran == [] and outcome.result["notice"] == "Not asked."
