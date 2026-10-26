"""Blueprint as Safwa declares it: who opens it, what it reads and how its plan is written."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from agent_runtime import AgentSession
from llm_gateway import CompletionTurn, ToolCall
from safwa.bootstrap.modules import AGENTS, FEATURE_COMMANDS, HOOKS, SYSTEM_PROMPT, routed_prompt
from safwa.features.advisor.agent import ITEMS, PERSONA, TALK_PERSONA
from safwa.features.blueprint.agent import (
    BLUEPRINT_AGENT,
    BLUEPRINT_PROMPT,
    EFFORT,
    OPEN_GOALS,
    blueprint_now,
)
from safwa.features.blueprint.hooks import (
    ASKED_PROMPT,
    BLUEPRINT_ASKED_HOOK,
    NOT_ASKED,
    opening_blueprint,
    review_asked,
)
from safwa.features.cards.model import CATEGORY_MEANINGS, EFFORT_RUNGS, ENERGY_MEANINGS
from safwa.features.compass.agent import TOPICS
from safwa.foundation.marks import MARKS_EXPLAINED
from safwa.foundation.method import explanation
from tg_agent_shell.ai.contracts import FINISH_TOOL
from tg_agent_shell.ai.subagents import RoutedSubagent
from tg_agent_shell.ai.tools import WATCHED_TOOLS, ToolAdapters
from tg_agent_shell.foundation.screens import ScreenCatalogue
from tg_agent_shell.hooks.contracts import BeforeTool, OnBeforeTool, ReviewTool
from tg_agent_shell.hooks.registry import HookRegistry
from tg_agent_shell.telegram.manifest import AgentContext

BLUEPRINT = next(agent for agent in AGENTS if agent.name == "blueprint")


def test_the_advisor_opens_blueprint_only_when_the_owner_asks_to_plan():
    """BP-ENTER-001 — tests/brd/blueprint.feature"""
    assert (
        "Open a conversation of its own with `sub(name)` only when the user explicitly asks "
        "for it, or says yes to your offer of it:" in SYSTEM_PROMPT
    )
    assert f'- `sub("blueprint")` — {BLUEPRINT_AGENT.purpose}' in SYSTEM_PROMPT
    assert BLUEPRINT_AGENT.purpose.startswith("the user explicitly asks to plan a new Value")
    assert '`route("blueprint")`' not in SYSTEM_PROMPT
    # An unclear Goal is asked about, or offered a plan: the purpose line is the one way in.
    assert (
        "- A Goal or Subgoal with an unclear result or completion criterion: ask about it, or "
        "offer to plan it together." in SYSTEM_PROMPT
    )
    assert SYSTEM_PROMPT.count('sub("blueprint")') == 1
    assert "- `sub(name)` alone." in SYSTEM_PROMPT
    assert BLUEPRINT_AGENT.title == "📐 Blueprint"
    assert "exit" in {command.command for command in FEATURE_COMMANDS}


def test_blueprint_asks_one_thing_at_a_time_from_the_owners_words_and_changes_nothing():
    """BP-STEP-002 — tests/brd/blueprint.feature"""
    for line in (
        "- One question per message, in one or two sentences.",
        "- Build every item from the user's words. Never invent a target, a number, a date or a time.",
        "2. Why: which Value it serves. Offer the user's Values from the workspace state by name. "
        "None fits: work out a new Value.",
        "5. Ask once whether the new Actions should also stand under an existing Goal or "
        f"Subgoal. Yes: read the open ones with `{OPEN_GOALS}`, and offer for each Action the "
        "ones it moves forward.",
        "6. For each Action, offer its Categories and Energy, and ask whether they fit.",
        "Only an Action carries them, and each may hold several.",
    ):
        assert line in BLUEPRINT_PROMPT
    # Every label by the words its selector gives it.
    for meanings in (CATEGORY_MEANINGS, ENERGY_MEANINGS):
        for kind, meaning in meanings.items():
            assert f"{kind.title()} ({meaning})" in BLUEPRINT_PROMPT
    # Effort is told only while Effort Points are on, so the prompt says nothing of it.
    assert "Effort" not in BLUEPRINT_PROMPT
    assert BLUEPRINT_AGENT.current is blueprint_now
    assert BLUEPRINT_AGENT.mutation_tools == ()
    assert BLUEPRINT_AGENT.workspace_state
    assert BLUEPRINT_AGENT.views == ("ai_cards", "ai_values", "ai_tags")
    # A title it reads may carry a repeat's or an archive's mark, told in the Advisor's words.
    assert MARKS_EXPLAINED in BLUEPRINT_PROMPT and MARKS_EXPLAINED in ITEMS
    # Its reads are filled in once, so the prompt names every view it may read.
    assert "{views}" not in BLUEPRINT.instructions and "ai_cards" in BLUEPRINT.instructions


def test_blueprint_holds_the_whole_of_each_method_and_speaks_in_safwas_voice():
    """BP-METHOD-003 — tests/brd/blueprint.feature"""
    # The one wording each feature gives its items, whole, in the prompt rather than a read.
    for method in TOPICS.values():
        assert explanation(method) in BLUEPRINT_PROMPT
    assert BLUEPRINT_AGENT.read_tools is None
    assert "get_more_about" not in BLUEPRINT_PROMPT
    for line in (
        "- Hold every item to its method. When a point does not hold, say which and ask about it.",
        "8. A Tag when the user wants one. Its method is advice: suggest it, and keep the Tag the user wants.",
    ):
        assert line in BLUEPRINT_PROMPT
    assert routed_prompt(BLUEPRINT).startswith(TALK_PERSONA)
    assert "You answer the user yourself." in TALK_PERSONA
    # Every other subagent keeps the persona it had, word for word.
    for agent in AGENTS:
        if agent.title is None:
            assert routed_prompt(agent).startswith(PERSONA)
    assert TALK_PERSONA.replace(
        "The Advisor handed this conversation to you. You answer the user yourself.",
        "The Advisor routed this request to you. Your answer goes back to it.",
    ) == PERSONA


def test_the_plan_is_written_one_line_per_item_ending_with_whether_to_create_it():
    """BP-PLAN-004 — tests/brd/blueprint.feature"""
    for line in (
        "- One line per item, each parent before its children.",
        "- Marks: 💎 Value, 🎯 Goal or Subgoal, ⭐️ Action, ☑️ Check, 🏷 Tag.",
        "- Fields: Note, Deadline, Schedule, Categories, Energy, Values, Tags.",
        "- A Card under Goals says under and each of their titles. A Check on a Card says on and "
        "that Card's title.",
        "- The last line asks, in the user's language, whether to create it.",
        "10. The user agrees: call `finish` alone with the plan as agreed.",
        "11. The user wants to stop: call `finish` alone with what was agreed so far.",
        "· Categories: Rest · Energy: Physical",
    ):
        assert line in BLUEPRINT_PROMPT
    assert BLUEPRINT_PROMPT.count("Create it?") == 1
    assert "write it in the plan as Effort." in EFFORT
    # The plan is longer than a few sentences, so `finish` asks for no length of its own.
    result = FINISH_TOOL["function"]["parameters"]["properties"]["result"]["description"]
    assert result == "What the conversation came to, in the user's language."


def _context(sessions) -> AgentContext:
    return AgentContext(
        owner_id=42,
        timezone="Europe/Istanbul",
        query_runner=None,  # type: ignore[arg-type]
        history=None,  # type: ignore[arg-type]
        sessions=sessions,
    )


async def test_blueprint_is_told_effort_only_while_effort_points_are_on(sessions):
    """BP-STEP-002 — tests/brd/blueprint.feature"""
    assert await blueprint_now(_context(sessions)) == ""


async def test_blueprint_offers_effort_by_its_rungs_while_effort_points_are_on(
    sessions, effort_on
):
    """BP-STEP-002 — tests/brd/blueprint.feature"""
    told = await blueprint_now(_context(sessions))
    assert told == EFFORT
    assert told.startswith("# Effort\nEffort Points are on: for each Action, also offer its Effort")
    for points, meaning in EFFORT_RUNGS.items():
        assert f"- {points:g}: {meaning}." in told


def _opening(arguments_json: str) -> BeforeTool:
    return BeforeTool(
        run_id=1, agent="root", agent_kind="advisor", tool="sub", call_id="s1",
        arguments_json=arguments_json, conversation="[User]: Let's plan a goal about running.",
    )


class _Verdict:
    """The model's one line, and the request it was asked with."""

    def __init__(self, content: str) -> None:
        self.content = content
        self.requests = []

    async def complete(self, request):
        self.requests.append(request)
        return CompletionTurn(content=self.content)


async def test_blueprint_opens_only_once_a_review_finds_the_owner_asked_to_plan():
    """BP-ASKED-006 — tests/brd/blueprint.feature"""
    assert BLUEPRINT_ASKED_HOOK in HOOKS
    assert BLUEPRINT_ASKED_HOOK.on == (OnBeforeTool(tool="sub"),)
    assert isinstance(BLUEPRINT_ASKED_HOOK.effect, ReviewTool)
    opening = _opening('{"name": "blueprint"}')
    assert await opening_blueprint(opening) == (opening,)
    for other in ('{"name": "coach"}', "not json", '{"agent": "blueprint"}', '{"name": 5}'):
        assert await opening_blueprint(_opening(other)) == ()

    for line in (
        "Answer asked when the user's newest message explicitly asks to plan, work out or think "
        "through an item together, or says yes to an offer to plan it.",
        "Answer not asked for anything else, such as asking to create, add or change an item, "
        "or a question.",
    ):
        assert line in ASKED_PROMPT
    verdict = _Verdict("not asked")
    assert await review_asked(opening, verdict) == NOT_ASKED
    (request,) = verdict.requests
    assert request.tools == () and request.reasoning_effort == "none"
    assert request.messages[0]["content"] == ASKED_PROMPT
    assert "Let's plan a goal about running." in request.messages[1]["content"]
    assert NOT_ASKED.startswith("The user did not ask to plan. Do what they asked:")

    # "Asked", or a line that is neither, opens Blueprint.
    for content, words in (
        ("Not asked.", NOT_ASKED),
        ("`not_asked`", NOT_ASKED),
        ('"not asked"', NOT_ASKED),
        ("1. **Not asked**", NOT_ASKED),
        ("Not asked: they want to create a Goal.", NOT_ASKED),
        ("asked", None),
        ("- Asked", None),
        ("Asked: they want to plan a Goal.", None),
        ("Maybe", None),
        ("", None),
    ):
        assert await review_asked(opening, _Verdict(content)) == words


class _Down:
    """A model that cannot be reached."""

    async def complete(self, request):
        raise RuntimeError("model down")


@asynccontextmanager
async def _no_session():
    # Only a switch is read through a session, and this check has none.
    yield None


@pytest.mark.parametrize(
    "model, opens",
    [(_Verdict("not asked"), False), (_Verdict("asked"), True), (_Verdict("Maybe"), True), (_Down(), True)],
)
async def test_sub_blueprint_runs_unless_the_review_says_not_asked(model, opens):
    """BP-ASKED-006 — tests/brd/blueprint.feature"""
    hooks = HookRegistry.of(
        (BLUEPRINT_ASKED_HOOK,), owners=frozenset({"blueprint"}), tools=WATCHED_TOOLS
    )
    blueprint = RoutedSubagent(name="blueprint", prompt="", title="📐 Blueprint")
    adapters = ToolAdapters(
        _no_session, None, None, None, ScreenCatalogue.of(()),
        subagents={"blueprint": blueprint}, hooks=hooks, provider=model,
    )
    advisor = AgentSession(
        run_id=1, tools=(), dialogue=[{"role": "user", "content": "Create a goal: run a marathon"}]
    )
    call = ToolCall(id="s1", name="sub", arguments_json='{"name": "blueprint"}')

    outcome = await adapters.run(advisor, call)

    if opens:
        assert outcome.succeeded and outcome.result == {"status": "ok", "agent": "blueprint"}
    else:
        assert not outcome.succeeded
        assert outcome.result == {"status": "error", "code": "refused", "notice": NOT_ASKED}
        assert "Create a goal: run a marathon" in model.requests[0].messages[1]["content"]
