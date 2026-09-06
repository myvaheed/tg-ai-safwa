"""The workspace mutator: what the roster promises about the one session that changes the workspace.

The workspace is not an entity — it is the set the owner keeps, Cards, Checks, Values, Tags,
Requests and Reminders — so what can be asserted here is the contract of the session that
proposes every change to it.
"""

from __future__ import annotations

import re
from typing import get_args

import pytest

from safwa.bootstrap.modules import (
    AGENTS,
    ALLOWED_VIEWS,
    HELPERS,
    PROPOSALS,
    SYSTEM_PROMPT,
)
from safwa.features.workspace_mutator.agent import MUTATOR_AGENT
from safwa.features.workspace_mutator.remove import ARCHIVABLE, RemoveToolInput


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
