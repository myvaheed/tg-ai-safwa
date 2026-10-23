"""The reminder subagent: Reminders and Remind have their own part, apart from the workspace's."""

from __future__ import annotations

import pytest
from schedule_helpers import rule_for

from safwa.bootstrap.modules import AGENTS, PROPOSALS, SYSTEM_PROMPT
from safwa.features.cards.use_cases import create_card
from safwa.features.checks.use_cases import create_check
from safwa.features.reminders.agent import REMINDER_AGENT
from safwa.features.schedules.api import entity_reminder
from safwa.features.schedules.use_cases import set_remind
from safwa.features.workspace_mutator.agent import MUTATOR_AGENT
from tg_agent_shell.proposals.api import ChangeAction, ToolPreparationError
from tg_agent_shell.proposals.prepare import ChangePreparer


def test_rm_agent_041_reminders_and_remind_have_their_own_part():
    """RM-AGENT-041 — tests/brd/reminders.feature"""
    assert REMINDER_AGENT.name in [agent.name for agent in AGENTS]
    assert f'`route("reminder")` — {REMINDER_AGENT.purpose}' in SYSTEM_PROMPT
    assert REMINDER_AGENT.mutation_tools == ("reminder", "remind")
    assert not {"reminder", "remind"} & set(MUTATOR_AGENT.mutation_tools)
    assert "ai_reminders" not in MUTATOR_AGENT.views
    assert REMINDER_AGENT.views == ("ai_reminders", "ai_checks", "ai_cards", "ai_tags", "ai_values")
    assert REMINDER_AGENT.workspace_state is False
    assert not {"goal", "action", "check"} & set(REMINDER_AGENT.mutation_tools)


def test_rm_agent_041_the_reminder_tool_deletes_a_reminder_by_its_id():
    """RM-AGENT-041 — tests/brd/reminders.feature"""
    change = PROPOSALS.change_from_tool("reminder", {"mode": "delete", "id": 3})
    assert (change.entity, change.action, change.id) == ("reminder", ChangeAction.DELETE, 3)
    with pytest.raises(ValueError, match="a deleted Reminder needs an id"):
        PROPOSALS.change_from_tool("reminder", {"mode": "delete"})


@pytest.mark.parametrize(
    ("kind", "named"), [("action", "Action"), ("goal", "Goal"), ("check", "Check")]
)
@pytest.mark.parametrize("call", [{"mode": "update", "when": "every day at 20:30"}, {"mode": "delete"}])
async def test_rm_agent_041_the_reminder_of_a_remind_goes_back_to_remind(
    sessions, kind, named, call
):
    """RM-AGENT-041 — tests/brd/reminders.feature"""
    async with sessions() as session:
        schedule = "daily 21:00" if kind != "goal" else "20.10.2099 21:00"
        made = dict(title="Читать", schedule=schedule, schedule_rule=await rule_for(session, schedule))
        item = await (
            create_check(session, **made) if kind == "check" else create_card(session, kind=kind, **made)
        )
        await set_remind(session, item, 15)
        reminder = await entity_reminder(session, item)
        change = PROPOSALS.change_from_tool("reminder", {**call, "id": reminder.id})
        with pytest.raises(ToolPreparationError) as refusal:
            await ChangePreparer(None, None, PROPOSALS).prepare(session, change)
    result = refusal.value.as_tool_result()
    assert (result["code"], result["error"], result["hint"]) == (
        "remind_of_item",
        f"Reminder #{reminder.id} is the Remind of {named} #{item.id} «Читать».",
        f"Use remind on {named} #{item.id}.",
    )


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
