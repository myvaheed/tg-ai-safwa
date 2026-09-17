"""The workspace mutator: what the roster promises about the one session that changes the workspace.

The workspace is not an entity — it is the set the owner keeps, Cards, Checks, Values, Tags,
Requests and Reminders — so what can be asserted here is the contract of the session that
proposes every change to it.
"""

from __future__ import annotations

import json
import re
from contextlib import asynccontextmanager
from typing import get_args

import pytest

from safwa.bootstrap.modules import (
    AGENTS,
    ALLOWED_VIEWS,
    HELPERS,
    PROPOSALS,
    SCREENS,
    SYSTEM_PROMPT,
)
from safwa.features.cards.use_cases import create_card
from safwa.features.diary.agent import DIARY_AGENT
from safwa.features.planning.use_cases import start_sprint
from safwa.features.profile.model import ProfileField
from safwa.features.profile.use_cases import set_profile_field
from safwa.features.tags.use_cases import create_tag
from safwa.features.values.use_cases import create_value
from safwa.features.workspace_mutator.agent import MUTATOR_AGENT
from safwa.features.workspace_mutator.remove import ARCHIVABLE, RemoveToolInput
from safwa.features.workspace_mutator.state import workspace_context
from telegram_llm import DialogueMessage
from tg_agent_shell.ai.messages import ContextBuilder, StateBlocks
from tg_agent_shell.ai.subagents import RoutedSubagent


def test_every_mutation_tool_belongs_to_the_board_or_to_the_diary():
    """WS-SCOPE-001 — tests/brd/workspace_mutator.feature"""
    # CLAUDE.md: the Advisor holds no mutation tool at all, and preparation runs where the
    # change was authored.  A new tool that reaches no subagent is unreachable.
    routed = {tool for agent in AGENTS for tool in agent.mutation_tools}

    assert routed == set(PROPOSALS.tools)
    assert set(PROPOSALS.tools) - set(MUTATOR_AGENT.mutation_tools) == {"diary"}


def test_the_board_judges_a_change_against_the_state_it_is_given():
    """WS-JUDGE-002 — tests/brd/workspace_mutator.feature"""
    # Its prompt tells it to weigh every proposal against the Sprint, the Success criteria
    # and the active Values, which reach it only as the workspace state block.
    assert MUTATOR_AGENT.workspace_state is True


def test_ws_remove_003_one_way_to_delete_and_two_kinds_that_may_be_archived():
    """WS-REMOVE-003 — tests/brd/workspace_mutator.feature"""
    entities = set(get_args(RemoveToolInput.model_fields["entity"].annotation))

    assert entities == {"card", "check", "tag", "value", "request", "reminder"}
    assert ARCHIVABLE == {"card", "check"}
    for entity in sorted(entities - ARCHIVABLE):
        with pytest.raises(ValueError, match="never archived"):
            RemoveToolInput(mode="archive", entity=entity, id=1)


class _NoMemory:
    async def sync(self):
        return type("Facts", (), {"text": ""})()


@asynccontextmanager
async def _no_session():
    yield None


def _builder(state: str) -> ContextBuilder:
    async def workspace_state(_session) -> StateBlocks:
        return StateBlocks(state=state, clock="Current local time: 2026-09-08 10:00")

    return ContextBuilder(
        _no_session,
        _NoMemory(),
        workspace_state,
        system_prompt="You keep what the owner keeps.",
        subagents={},
        cache_breakpoints=False,
    )


async def test_ws_context_004_the_advisor_reads_it_always_and_a_subagent_only_when_it_asks():
    """WS-CONTEXT-004 — tests/brd/workspace_mutator.feature"""
    builder = _builder("Workspace mode: sprint")
    turn = [{"role": "user", "content": "Move the roof Card to Today."}]

    answering = json.dumps(
        await builder.root([DialogueMessage(role="user", content="What is open?")])
    )
    asked = json.dumps(
        await builder.routed(RoutedSubagent("mutator", "Change it.", workspace_state=True), turn)
    )
    did_not = json.dumps(
        await builder.routed(RoutedSubagent("days", "Write it.", workspace_state=False), turn)
    )

    assert "Workspace mode: sprint" in answering
    assert "Workspace mode: sprint" in asked
    assert "Workspace mode: sprint" not in did_not
    # The one that changes the workspace asks for it; the one that writes days does not.
    assert MUTATOR_AGENT.workspace_state is True
    assert DIARY_AGENT.workspace_state is False


async def test_ws_context_005_the_mode_comes_first_and_the_owner_next(sessions):
    """WS-CONTEXT-005 — tests/brd/workspace_mutator.feature"""
    async with sessions() as session:
        await set_profile_field(
            session, ProfileField.ABOUT_ME, "I run in the mornings"
        )
        await create_card(session, title="Ship it", kind="action", stage="today", effort_points=3)
        await session.commit()
        planning = (await workspace_context(session)).state.splitlines()

        await start_sprint(session, success_criteria="Ship v2")
        await session.commit()
        running = (await workspace_context(session)).state.splitlines()

    assert planning[0] == "Workspace mode: planning"
    assert running[0] == "Workspace mode: sprint"
    assert running[1] == "About me: I run in the mornings"
    assert running[2].startswith("Advisor instructions:")


async def test_ws_context_006_everything_named_in_the_state_is_a_citation(sessions):
    """WS-CONTEXT-006 — tests/brd/workspace_mutator.feature"""
    async with sessions() as session:
        value = await create_value(session, "Health", active=True)
        tag = await create_tag(session, "Family")
        card = await create_card(
            session,
            title="Fix the roof",
            kind="action",
            stage="backlog",
            priority="critical",
            effort_points=3,
        )
        await session.commit()
        state = (await workspace_context(session)).state

    # Read back with the pattern that parses Safwa's own replies: what the block hands over
    # is already a link, so pointing the owner at one of these is quoting it.
    named = {(kind, int(item_id)) for _label, kind, item_id in SCREENS.citation.findall(state)}

    assert named == {("value", value.id), ("tag", tag.id), ("card", card.id)}



async def test_ws_context_007_the_clock_is_the_owners_and_comes_last(sessions):
    """WS-CONTEXT-007 — tests/brd/workspace_mutator.feature"""
    async with sessions() as session:
        context = await workspace_context(session)

    assert context.clock.startswith("Current local time: ")
    assert context.clock.endswith("(Europe/Istanbul)")

    builder = _builder("Workspace mode: planning")
    turn = [DialogueMessage(role="user", content="What is open?")]

    messages = await builder.root(turn)

    # Last, and behind the conversation: everything before it is the same on the next turn.
    assert "Current local time" in json.dumps(messages[-1])
    assert "Current local time" not in json.dumps(messages[:-1])


# Every prompt a model reads a view list from, however that list was written.
CATALOGUE_PROMPTS = {
    "advisor": SYSTEM_PROMPT,
    **{name: helper.instructions for name, helper in HELPERS.items()},
    **{agent.name: agent.instructions for agent in AGENTS},
}


@pytest.mark.parametrize("reader", sorted(CATALOGUE_PROMPTS))
def test_a_prompt_names_no_view_the_database_does_not_have(reader: str):
    # The catalogue is composed and cannot name a view that is gone. A prompt also names
    # views in its own prose, and there nothing checks the spelling.
    named = set(re.findall(r"\bai_[a-z_]+\b", CATALOGUE_PROMPTS[reader]))

    assert named <= ALLOWED_VIEWS
    assert named
