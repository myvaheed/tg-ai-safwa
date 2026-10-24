"""Blueprint as Safwa declares it: who opens it, what it reads and how its plan is written."""

from __future__ import annotations

from safwa.bootstrap.modules import AGENTS, FEATURE_COMMANDS, SYSTEM_PROMPT, routed_prompt
from safwa.features.advisor.agent import ITEMS, PERSONA, TALK_PERSONA
from safwa.features.blueprint.agent import BLUEPRINT_AGENT, BLUEPRINT_PROMPT
from safwa.features.compass.agent import MORE_ABOUT, TOPICS
from tg_agent_shell.ai.contracts import FINISH_TOOL

BLUEPRINT = next(agent for agent in AGENTS if agent.name == "blueprint")


def test_the_advisor_opens_blueprint_with_sub_and_routes_a_clear_item_to_the_workspace():
    """BP-ENTER-001 — tests/brd/blueprint.feature"""
    assert f'- `sub("blueprint")` — {BLUEPRINT_AGENT.purpose}' in SYSTEM_PROMPT
    assert '`route("blueprint")`' not in SYSTEM_PROMPT
    assert '- A clear item to create is still `route("workspace_mutator")`.' in SYSTEM_PROMPT
    assert (
        '- A Goal or Subgoal with an unclear result or completion criterion: `sub("blueprint")`.'
        in SYSTEM_PROMPT
    )
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
    ):
        assert line in BLUEPRINT_PROMPT
    assert BLUEPRINT_AGENT.mutation_tools == ()
    assert BLUEPRINT_AGENT.workspace_state
    assert BLUEPRINT_AGENT.views == ("ai_cards", "ai_values", "ai_tags")
    # Its reads are filled in once, so the prompt names every view it may read.
    assert "{views}" not in BLUEPRINT.instructions and "ai_cards" in BLUEPRINT.instructions


def test_blueprint_reads_each_items_method_in_the_one_wording_and_speaks_in_safwas_voice():
    """BP-METHOD-003 — tests/brd/blueprint.feature"""
    assert BLUEPRINT_AGENT.read_tools(None) == (MORE_ABOUT,)
    assert {"values", "goals", "actions", "checks", "tags"} <= set(TOPICS)
    for line in (
        "- Before you shape a Value, Goal, Action, Check or Tag, read its method once with `get_more_about`.",
        "- Hold every item to its method. When a point does not hold, say which and ask, as its examples do.",
        "6. A Tag when the user wants one. Its method is advice: suggest it, and keep the Tag the user wants.",
    ):
        assert line in BLUEPRINT_PROMPT
    prompt = routed_prompt(BLUEPRINT)
    assert prompt.startswith(TALK_PERSONA) and ITEMS in prompt
    assert "You answer the user yourself." in TALK_PERSONA
    # Every other subagent keeps the persona it had, word for word.
    for agent in AGENTS:
        if agent.title is None:
            assert routed_prompt(agent).startswith(PERSONA)
    assert TALK_PERSONA.replace(
        "The Advisor handed this conversation to you. You answer the user yourself.",
        "The Advisor routed this request to you. Your answer goes back to it.",
    ) == PERSONA


def test_the_plan_is_written_one_line_per_item_ending_with_how_to_create_it():
    """BP-PLAN-004 — tests/brd/blueprint.feature"""
    for line in (
        "- One line per item, each parent before its children.",
        "- Marks: 💎 Value, 🎯 Goal or Subgoal, ⭐️ Action, ☑️ Check, 🏷 Tag.",
        "- Fields: Note, Deadline, Schedule, Values, Tags.",
        "- A Card under a Goal says under and that Goal's title. A Check on a Card says on and "
        "that Card's title.",
        '- The last line: Say "Create" to create it.',
        "8. The user agrees: call `finish` alone with the plan as agreed.",
        "9. The user wants to stop: call `finish` alone with what was agreed so far.",
    ):
        assert line in BLUEPRINT_PROMPT
    # The plan is longer than a few sentences, so `finish` asks for no length of its own.
    result = FINISH_TOOL["function"]["parameters"]["properties"]["result"]["description"]
    assert result == "What the conversation came to, in the user's language."
