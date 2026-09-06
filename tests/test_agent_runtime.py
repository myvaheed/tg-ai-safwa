"""The runtime is a package, and this is what makes that claim checkable.

Rule F already says no module under `src/agent_runtime/` imports Safwa. That is necessary
and not sufficient: a package can be free of an import and still be unusable without the
application it was cut out of. So the border is tested twice — by what the public names may
say, and by an example that runs the whole loop with no Safwa in the process at all.
"""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

from agent_runtime import (
    AgentDefinition,
    AgentManager,
    AgentSession,
    InMemorySessionStore,
    InteractionRef,
    Resumption,
    RunStatus,
    ToolBudgetExceeded,
    ToolOutcome,
    TurnOutcome,
)
from llm_gateway import CompletionTurn, ToolCall

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "agent_runtime"
EXAMPLE = ROOT / "examples" / "note_keeper" / "bot.py"

# What a public name in the runtime may never be about. Two are Safwa's vocabulary and two
# are the delivery it must not know: a package that names any of them is not reusable.
FOREIGN = ("proposal", "aiogram", "sqlalchemy", "safwa", "telegram", "pydantic")


def _public_names(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            names.append(node.name)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            names.append(node.name)
            names.extend(argument.arg for argument in node.args.args)
            names.extend(argument.arg for argument in node.args.kwonlyargs)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.append(node.target.id)
    return [name for name in names if not name.startswith("_")]


@pytest.mark.parametrize("module", ["model.py", "ports.py"])
def test_the_vocabulary_of_the_package_belongs_to_no_application(module: str) -> None:
    foreign = [
        name
        for name in _public_names(PACKAGE / module)
        if any(word in name.lower() for word in FOREIGN)
    ]
    assert not foreign, f"agent_runtime/{module} names {foreign}"


def _load_example():
    specification = importlib.util.spec_from_file_location("note_keeper", EXAMPLE)
    assert specification is not None and specification.loader is not None
    bot = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(bot)
    return bot


def _note_keeper(bot, provider):
    notebook = bot.Notebook()
    notebook.notes.append("Bread, milk, coffee")
    store = bot.InMemorySessionStore()
    runtime = bot.AgentManager(
        store,
        provider,
        bot.NoteTools(notebook),
        bot.OneSystemPrompt("You keep notes."),
        bot.NoteReview(notebook),
        max_tool_calls=8,
        max_repair_rounds=2,
        child_deadline_seconds=30.0,
    )
    return notebook, store, runtime


async def test_a_bot_that_is_not_safwa_runs_a_turn_and_resumes_a_suspended_one() -> None:
    """The example is the proof: one search inside the turn, one change that waits, one resume."""
    bot = _load_example()
    provider = bot.ScriptedProvider(
        [
            bot._call("search_notes", word="coffee"),
            bot.CompletionTurn(content="You have one already."),
            bot._call("write_note", text="Ask about the roast"),
            bot.CompletionTurn(content="Kept it."),
        ]
    )
    notebook, store, runtime = _note_keeper(bot, provider)

    answered = await runtime.handle([{"role": "user", "content": "Any note on coffee?"}])
    assert answered.message == "You have one already."
    assert not answered.waiting and answered.ref is None

    proposed = await runtime.handle([{"role": "user", "content": "Note: ask about the roast"}])
    assert proposed.waiting
    assert notebook.waiting == "Ask about the roast"
    assert notebook.notes == ["Bread, milk, coffee"]
    # The turn is over and stored: the person is not being waited on by a running session.
    reference = proposed.ref
    assert reference is not None
    assert store.status(reference.run_id) is RunStatus.AWAITING_APPROVAL

    call, note = notebook.approve()
    resumed = await runtime.resume(
        reference, Resumption(results={call: {"status": "kept", "note": note}})
    )
    assert resumed is not None
    assert resumed.message == "Kept it."
    assert not resumed.waiting
    assert notebook.notes == ["Bread, milk, coffee", "Ask about the roast"]
    assert store.status(reference.run_id) is RunStatus.COMPLETED
    # The resumed session carried on from its own steps, and the decision reached it as the
    # result of the call it made — not as a retelling.
    continued = provider.requests[-1].messages
    assert [message["role"] for message in continued] == [
        "system",
        "user",
        "assistant",
        "tool",
    ]
    assert json.loads(str(continued[-1]["content"])) == {
        "status": "kept",
        "note": "Ask about the roast",
    }
    # One decision, once: the same reference names a suspension the session has left.
    assert await runtime.resume(reference, Resumption(results={call: {}})) is None


async def test_a_session_no_one_routed_to_is_ended_when_the_person_writes_instead() -> None:
    """Nothing can route back into a session with no caller, so it is ended, not left open."""
    bot = _load_example()
    provider = bot.ScriptedProvider(
        [
            bot._call("write_note", text="Ask about the roast"),
            bot.CompletionTurn(content="Fine, forgetting that one."),
        ]
    )
    notebook, store, runtime = _note_keeper(bot, provider)

    proposed = await runtime.handle([{"role": "user", "content": "Note: ask about the roast"}])
    reference = proposed.ref
    assert reference is not None

    prior = await runtime.interrupt(
        reference,
        {notebook.waiting_call: {"status": "discarded"}},
        summary="🗑 Discarded — Ask about the roast",
    )
    assert prior == []
    assert store.status(reference.run_id) is RunStatus.ABANDONED
    # What it stopped on is answered in its own record rather than left half-said.
    stored = await store.get(reference.run_id)
    assert stored is not None
    assert json.loads(str(stored.state["transcript"][-2]["content"])) == {"status": "discarded"}
    # The person's words are a new request, and the ended session is not picked up by it.
    answered = await runtime.handle([{"role": "user", "content": "Forget it."}])
    assert answered.message == "Fine, forgetting that one."
    assert answered.ref is None
    assert store.status(reference.run_id) is RunStatus.ABANDONED
    assert store.status(reference.run_id + 1) is RunStatus.COMPLETED
    assert notebook.notes == ["Bread, milk, coffee"]


async def test_a_reference_no_one_minted_resumes_nothing() -> None:
    """A decision the session never stopped on changes it in no way at all."""
    bot = _load_example()
    provider = bot.ScriptedProvider([bot._call("write_note", text="Ask about the roast")])
    notebook, store, runtime = _note_keeper(bot, provider)

    proposed = await runtime.handle([{"role": "user", "content": "Note: ask about the roast"}])
    assert proposed.ref is not None

    stale = InteractionRef(proposed.ref.run_id, "not-the-token")
    assert await runtime.resume(stale, Resumption()) is None
    # Refused before anything is taken: the session is still waiting for its real answer.
    assert store.status(proposed.ref.run_id) is RunStatus.AWAITING_APPROVAL
    assert notebook.notes == ["Bread, milk, coffee"]


async def test_the_in_memory_store_closes_a_branch_the_way_the_real_one_does() -> None:
    """One port, one contract: everything unfinished below a session ends with it."""
    store = InMemorySessionStore()
    root = await store.create(kind="advisor")
    child = await store.create(kind="workspace_mutator", parent_run_id=root.id)
    grandchild = await store.create(kind="diary", parent_run_id=child.id)
    for run in (child, grandchild):
        await store.leave_interrupted(run.id, {}, "left unfinished")

    assert await store.close_unfinished_children(root.id) == 2

    assert store.status(child.id) is RunStatus.ABANDONED
    assert store.status(grandchild.id) is RunStatus.ABANDONED


# --------------------------------------------- what the loop refuses, counts and stops

READ = {"type": "function", "function": {"name": "read", "parameters": {}}}
ROUTE = {"type": "function", "function": {"name": "route", "parameters": {}}}
WRITE = {"type": "function", "function": {"name": "write", "parameters": {}}}


def _turn(*names: str, content: str = "") -> CompletionTurn:
    return CompletionTurn(
        content=content,
        tool_calls=tuple(
            ToolCall(id=f"call-{index}", name=name, arguments_json="{}")
            for index, name in enumerate(names)
        ),
    )


class _Tools:
    """A root that reads and routes, and one subagent that writes. Nothing else exists."""

    def __init__(self) -> None:
        self.ran: list[str] = []

    def definition(self, kind: str) -> AgentDefinition:
        return AgentDefinition(
            kind=kind, tools=(READ, WRITE) if kind == "writer" else (READ, ROUTE)
        )

    def is_immediate(self, agent: AgentSession, name: str) -> bool:
        return name in {"read", "route"}

    async def run(self, agent: AgentSession, call: ToolCall) -> ToolOutcome:
        self.ran.append(call.name)
        if call.name == "read":
            return ToolOutcome(result={"rows": []})
        return ToolOutcome(result={"status": "prepared"}, change={"tool": call.name})

    def route_target(self, call: ToolCall) -> tuple[str | None, dict[str, Any] | None]:
        return "writer", None

    def refuse_mixed(self) -> dict[str, Any]:
        return {"status": "error", "code": "mixed_read_and_mutation_tools"}

    def prepared_message(self) -> str:
        return "Prepared."

    def repair_exhausted_message(self) -> str:
        return "Gave up."


class _Prompt:
    async def messages_for(self, kind, dialogue, prior_receipts=None) -> list[dict[str, Any]]:
        return [{"role": "system", "content": kind}]


class _Review:
    """A prepared change is what the person decides on; words end the session."""

    async def materialize(self, agent: AgentSession, result) -> TurnOutcome:
        if any(tool.change for tool in result.pending_tools):
            return TurnOutcome(message="Waiting on you.", waiting=True)
        return TurnOutcome(message=result.message)


class _Provider:
    """Scripted turns, each answered after `delay` seconds of this session's own time."""

    def __init__(self, turns: list[CompletionTurn], delay: float = 0.0) -> None:
        self.turns = list(turns)
        self.delay = delay
        self.requests: list[Any] = []

    async def complete(self, request) -> CompletionTurn:
        await asyncio.sleep(self.delay)
        self.requests.append(request)
        if not self.turns:
            raise AssertionError("The application made an unexpected LLM request")
        return self.turns.pop(0)

    async def aclose(self) -> None:
        return None


def _runtime(
    provider: _Provider,
    *,
    tools: _Tools | None = None,
    max_tool_calls: int = 8,
    child_deadline_seconds: float = 30.0,
) -> tuple[AgentManager, _Tools, InMemorySessionStore]:
    store = InMemorySessionStore()
    adapters = tools or _Tools()
    return (
        AgentManager(
            store,
            provider,
            adapters,
            _Prompt(),
            _Review(),
            routed_kinds=frozenset({"writer"}),
            max_tool_calls=max_tool_calls,
            max_repair_rounds=2,
            child_deadline_seconds=child_deadline_seconds,
        ),
        adapters,
        store,
    )


def _results(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [json.loads(str(item["content"])) for item in messages if item.get("role") == "tool"]


async def test_ag_route_001_a_session_cannot_call_a_tool_it_was_never_handed() -> None:
    """AG-ROUTE-001 — tests/brd/tg_agent_shell/agents.feature"""
    provider = _Provider([_turn("write"), _turn(content="Handing it over then.")])
    runtime, tools, _ = _runtime(provider)

    answered = await runtime.handle([{"role": "user", "content": "Change it."}])

    assert answered.message == "Handing it over then."
    assert not answered.waiting
    # Nothing ran and nothing was prepared: the mutation belongs to the subagent that owns it.
    assert tools.ran == []


async def test_ag_route_004_a_subagent_has_no_way_to_hand_the_work_on() -> None:
    """AG-ROUTE-004 — tests/brd/tg_agent_shell/agents.feature"""
    provider = _Provider(
        [
            _turn("route"),
            _turn("route"),
            _turn("write"),
            _turn(content="Prepared it myself."),
            _turn(content="It is ready for you."),
        ]
    )
    runtime, tools, _ = _runtime(provider)

    outcome = await runtime.handle([{"role": "user", "content": "Change it."}])

    assert outcome.waiting
    assert tools.ran == ["write"]
    # The subagent's own route was refused rather than run, so the chain stayed two deep.
    refused = _results(list(provider.requests[-1].messages))
    assert [item["code"] for item in refused] == ["tool_not_available"]


async def test_ag_budget_011_every_refused_response_spends_the_budget() -> None:
    """AG-BUDGET-011 — tests/brd/tg_agent_shell/agents.feature"""
    # A response that mixes route with anything else runs nothing at all. Repeated, it is a
    # session that never settles, and only the budget can end it.
    provider = _Provider([_turn("route", "read") for _ in range(20)])
    runtime, tools, _ = _runtime(provider, max_tool_calls=4)

    with pytest.raises(ToolBudgetExceeded):
        await runtime.handle([{"role": "user", "content": "Change it."}])

    assert tools.ran == []


async def test_ag_budget_012_a_subagent_resumed_after_a_screen_is_still_bound_by_the_clock() -> None:
    """AG-BUDGET-012 — tests/brd/tg_agent_shell/agents.feature"""
    provider = _Provider([_turn("route"), _turn("write"), _turn(content="It is saved.")])
    runtime, _, store = _runtime(provider, child_deadline_seconds=0.05)

    waiting = await runtime.handle([{"role": "user", "content": "Change it."}])
    reference = waiting.ref
    assert reference is not None

    # However long the person takes to decide, none of it is the subagent's own time.
    await asyncio.sleep(0.12)
    provider.delay = 0.2
    resumed = await runtime.resume(reference, Resumption(results={"call-0": {"status": "saved"}}))

    assert resumed is not None
    assert resumed.message == "It is saved."
    assert store.status(reference.run_id) is RunStatus.FAILED
    # The root keeps its turn and is told what did not finish, rather than losing the answer.
    receipt = _results(list(provider.requests[-1].messages))[-1]
    assert "did not finish within" in receipt["error"]
