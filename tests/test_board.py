"""The board subagent: what the roster promises about the one session that changes the board.

The board is not an entity — it is the set the owner keeps, Cards, Checks, Values, Tags,
Requests and Reminders — so what can be asserted here is the contract of the session that
proposes every change to it.
"""

from __future__ import annotations

import re

import pytest

from safwa.bootstrap.modules import (
    AGENTS,
    ALLOWED_VIEWS,
    HEAVY_ANALYZER_PROMPT,
    PROPOSALS,
    SYSTEM_PROMPT,
)
from safwa.features.board.agent import BOARD_AGENT


def test_every_mutation_tool_belongs_to_the_board_or_to_the_diary():
    # CLAUDE.md: the Advisor holds no mutation tool at all, and preparation runs where the
    # change was authored.  A new tool that reaches no subagent is unreachable.
    routed = {tool for agent in AGENTS for tool in agent.mutation_tools}

    assert routed == set(PROPOSALS.tools)
    assert set(PROPOSALS.tools) - set(BOARD_AGENT.mutation_tools) == {"diary"}


def test_the_board_judges_a_change_against_the_state_it_is_given():
    # Its prompt tells it to weigh every proposal against the Sprint, the Success criteria
    # and the active Values, which reach it only as the board state block.
    assert BOARD_AGENT.board_state is True


# Every prompt whose view list is composed rather than written out. The Diary writes its
# own, with columns trimmed on purpose, and it is not one of these.
CATALOGUE_PROMPTS = {
    "advisor": SYSTEM_PROMPT,
    "heavy_analyzer": HEAVY_ANALYZER_PROMPT,
    **{agent.name: agent.instructions for agent in AGENTS if agent.views},
}


@pytest.mark.parametrize("reader", sorted(CATALOGUE_PROMPTS))
def test_a_prompt_names_no_view_the_database_does_not_have(reader: str):
    # The catalogue is composed and cannot name a view that is gone. A prompt also names
    # views in its own prose, and there nothing checks the spelling.
    named = set(re.findall(r"\bai_[a-z_]+\b", CATALOGUE_PROMPTS[reader]))

    assert named <= ALLOWED_VIEWS
    assert named
