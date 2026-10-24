"""The reminder subagent: Reminders and Remind have their own part, apart from the workspace's."""

from __future__ import annotations

import pytest
from schedule_helpers import rule_for

from safwa.bootstrap.modules import AGENTS, PROPOSALS, SYSTEM_PROMPT
from safwa.features.cards.use_cases import archive_subtree, create_card, finish_action
from safwa.features.checks.model import CheckOutcome
from safwa.features.checks.use_cases import create_check, resolve_check, toggle_check_value
from safwa.features.reminders import items
from safwa.features.reminders.agent import REMINDER_AGENT
from safwa.features.reminders.items import NO_OPEN_ITEMS, OPEN_ITEMS, open_items, reminder_items
from safwa.features.schedules.api import entity_reminder
from safwa.features.schedules.use_cases import set_remind
from safwa.features.tags.use_cases import create_tag
from safwa.features.values.use_cases import create_value
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
    assert REMINDER_AGENT.current is reminder_items
    assert not {"goal", "action", "check"} & set(REMINDER_AGENT.mutation_tools)


async def test_rm_agent_041_the_reminder_part_is_handed_its_open_items_with_a_schedule(sessions):
    """RM-AGENT-041 — tests/brd/reminders.feature"""
    async with sessions() as session:
        health = await create_value(session, "Health", "Strong enough to carry the day.")
        sport = await create_tag(session, "Sport")
        daily = dict(schedule="daily 21:00", schedule_rule=await rule_for(session, "daily 21:00"))
        one_day = dict(
            schedule="20.10.2099 21:00", schedule_rule=await rule_for(session, "20.10.2099 21:00")
        )
        meditated = await create_check(session, title="Помедитировал?", **daily)
        await toggle_check_value(session, meditated.id, health.id)
        # The answered copy is not listed: the series is its open copy.
        _, meditated = await resolve_check(session, meditated.id, CheckOutcome.PASSED)
        await create_check(session, title="Купил молоко?")
        run = await create_card(
            session,
            kind="action",
            title="Пробежка",
            tag_ids={sport.id},
            value_ids={health.id},
            **daily,
        )
        await create_card(session, kind="action", title="Разобрать почту")
        called = await create_card(session, kind="action", title="Позвонить маме", **one_day)
        await finish_action(session, called.id)
        await archive_subtree(session, called.id)
        marathon = await create_card(session, kind="goal", title="Марафон", **one_day)
        await session.commit()

        assert await open_items(session) == "\n".join(
            [
                OPEN_ITEMS,
                f"- Check #{meditated.id} «Помедитировал?»; schedule: daily 21:00; values: Health",
                f"- Action #{run.id} «Пробежка»; schedule: daily 21:00; tags: Sport; values: Health",
                f"- Goal #{marathon.id} «Марафон»; deadline: 20.10.2099 21:00",
            ]
        )


async def test_rm_agent_041_open_items_past_the_most_are_left_to_search(sessions, monkeypatch):
    """RM-AGENT-041 — tests/brd/reminders.feature"""
    async with sessions() as session:
        assert await open_items(session) == NO_OPEN_ITEMS
        daily = dict(schedule="daily 21:00", schedule_rule=await rule_for(session, "daily 21:00"))
        checks = [await create_check(session, title=f"Трекер {n}?", **daily) for n in range(3)]
        await session.commit()
        monkeypatch.setattr(items, "OPEN_ITEMS_MAX", 1)

        assert await open_items(session) == "\n".join(
            [
                OPEN_ITEMS,
                f"- Check #{checks[0].id} «Трекер 0?»; schedule: daily 21:00",
                "And 2 more: find them with `search`.",
            ]
        )


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
