"""The reminder subagent: Reminders and Remind have their own part, apart from the workspace's."""

from __future__ import annotations

import pytest

from safwa.bootstrap.modules import AGENTS, PROPOSALS, SYSTEM_PROMPT
from safwa.features.reminders.agent import REMINDER_AGENT
from safwa.features.workspace_mutator.agent import MUTATOR_AGENT
from tg_agent_shell.proposals.api import ChangeAction


def test_rm_agent_041_reminders_and_remind_have_their_own_part():
    """RM-AGENT-041 — tests/brd/reminders.feature"""
    assert REMINDER_AGENT.name in [agent.name for agent in AGENTS]
    assert f'`route("reminder")` — {REMINDER_AGENT.purpose}' in SYSTEM_PROMPT
    assert REMINDER_AGENT.mutation_tools == ("reminder", "remind", "remove")
    assert not {"reminder", "remind"} & set(MUTATOR_AGENT.mutation_tools)
    assert "ai_reminders" not in MUTATOR_AGENT.views
    assert REMINDER_AGENT.views == ("ai_reminders", "ai_checks", "ai_cards", "ai_tags", "ai_values")
    assert REMINDER_AGENT.workspace_state is False
    assert not {"goal", "action", "check"} & set(REMINDER_AGENT.mutation_tools)


@pytest.mark.parametrize(("item", "entity"), [("action", "card"), ("goal", "card"), ("check", "check")])
def test_remind_is_a_change_of_the_item_it_names(item, entity):
    """SCH-REMIND-029 — tests/brd/schedules.feature"""
    for lead in (45, 0, None):
        change = PROPOSALS.change_from_tool("remind", {"item": item, "id": 7, "minutes_before": lead})
        assert (change.entity, change.action, change.id, change.values) == (
            entity,
            ChangeAction.UPDATE,
            7,
            {"remind": lead},
        )
