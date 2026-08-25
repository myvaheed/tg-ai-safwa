"""The board subagent: what the roster promises about the one session that changes the board.

The board is not an entity — it is the set the owner keeps, Cards, Checks, Values, Tags,
Requests and Reminders — so what can be asserted here is the contract of the session that
proposes every change to it.
"""

from __future__ import annotations

import re

from safwa.bootstrap.modules import AGENTS, ALLOWED_VIEWS, PROPOSALS
from safwa.features.board.agent import BOARD_AGENT, BOARD_PROMPT


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


def test_the_prompt_names_no_view_the_database_does_not_have():
    # The view list is prose.  A renamed view leaves the old name in the prompt, and the
    # failure is a query the model writes at runtime against a view that is gone.
    named = set(re.findall(r"\bai_[a-z_]+\b", BOARD_PROMPT))

    assert named <= ALLOWED_VIEWS
    assert named
