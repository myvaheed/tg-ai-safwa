"""The reminder subagent: Reminders and Remind have their own part, apart from the workspace's."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from schedule_helpers import rule_for

from safwa.bootstrap.modules import AGENTS, PROPOSALS, SYSTEM_PROMPT
from safwa.constants import WEEKDAY_NAMES
from safwa.features.cards.use_cases import archive_subtree, create_card, finish_action
from safwa.features.checks.model import CheckOutcome
from safwa.features.checks.use_cases import create_check, resolve_check, toggle_check_value
from safwa.features.reminders import items
from safwa.features.reminders.agent import REMINDER_AGENT
from safwa.features.reminders.api import resolve
from safwa.features.reminders.items import (
    NO_OPEN_ITEMS,
    OPEN_ITEMS,
    RANKED_BY_TOKENS,
    REMINDERS,
    SCHEDULED,
    UNSCHEDULED,
    open_items,
    reminder_items,
)
from safwa.features.reminders.use_cases import create_reminder
from safwa.features.schedules.api import entity_reminder
from safwa.features.schedules.use_cases import set_remind
from safwa.features.tags.use_cases import create_tag
from safwa.features.values.use_cases import create_value
from safwa.features.workspace_mutator.agent import MUTATOR_AGENT
from safwa.foundation.tokens import estimate_tokens
from tg_agent_shell.proposals.api import ChangeAction, ToolPreparationError
from tg_agent_shell.proposals.prepare import ChangePreparer

TZ = ZoneInfo("Europe/Istanbul")


def test_rm_agent_041_reminders_and_remind_have_their_own_part():
    """RM-AGENT-041 — tests/brd/reminders.feature"""
    assert REMINDER_AGENT.name in [agent.name for agent in AGENTS]
    assert f'`route("reminder")` — {REMINDER_AGENT.purpose}' in SYSTEM_PROMPT
    assert REMINDER_AGENT.mutation_tools == ("reminder", "remind")
    assert not {"reminder", "remind"} & set(MUTATOR_AGENT.mutation_tools)
    assert "ai_reminders" not in MUTATOR_AGENT.views
    assert REMINDER_AGENT.views == ()
    assert REMINDER_AGENT.workspace_state is False
    assert REMINDER_AGENT.current is reminder_items
    assert not {"goal", "action", "check"} & set(REMINDER_AGENT.mutation_tools)


class Closeness:
    """A search index that finds the items it is given, and keeps what it was asked."""

    def __init__(self, scores: dict[tuple[str, int], float]) -> None:
        self.scores = scores
        self.asked: list[tuple[str, tuple[str, ...]]] = []

    async def relevance(self, search, views):
        self.asked.append((search, tuple(views)))
        return self.scores


def context_of(sessions, ranking=None):
    return SimpleNamespace(
        sessions=sessions, timezone=TZ.key, query_runner=SimpleNamespace(search=ranking)
    )


async def test_rm_agent_041_the_reminder_part_is_handed_its_items_and_reminders(sessions):
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
        milk = await create_check(session, title="Купил молоко?")
        run = await create_card(
            session,
            kind="action",
            title="Пробежка",
            tag_ids={sport.id},
            value_ids={health.id},
            **daily,
        )
        mail = await create_card(session, kind="action", title="Разобрать почту", tag_ids={sport.id})
        called = await create_card(session, kind="action", title="Позвонить маме", **one_day)
        await finish_action(session, called.id)
        await archive_subtree(session, called.id)
        marathon = await create_card(session, kind="goal", title="Марафон", **one_day)
        water = await create_reminder(
            session,
            remind_text="Remind the user\nto drink water.",
            schedule=resolve(days=list(WEEKDAY_NAMES), clock="07:00", now=datetime.now(UTC), tz=TZ),
            tz=TZ,
        )
        # The Reminder Remind made is the item's, changed with `remind`.
        await set_remind(session, run, 15)
        await session.commit()

        scheduled = [
            f"- Check #{meditated.id} «Помедитировал?»; schedule: daily 21:00; values: Health",
            f"- Action #{run.id} «Пробежка»; schedule: daily 21:00; tags: Sport; values: Health",
            f"- Goal #{marathon.id} «Марафон»; deadline: 20.10.2099 21:00",
        ]
        # The condition's own session has the ones with a Schedule alone, and no Remind.
        assert await open_items(session, None, "") == "\n".join([OPEN_ITEMS, *scheduled])
    scheduled[1] = (
        f"- Action #{run.id} «Пробежка»; schedule: daily 21:00; remind: 15 minutes before; "
        "tags: Sport; values: Health"
    )
    assert await reminder_items(context_of(sessions), "") == "\n\n".join(
        [
            "\n".join([SCHEDULED, *scheduled]),
            "\n".join(
                [
                    UNSCHEDULED,
                    f"- Check #{milk.id} «Купил молоко?»",
                    f"- Action #{mail.id} «Разобрать почту»; tags: Sport",
                ]
            ),
            "\n".join(
                [REMINDERS, f"- Reminder #{water.id} «Remind the user to drink water.»; every day at 07:00"]
            ),
        ]
    )


async def test_rm_agent_041_the_closest_to_the_conversation_come_first(sessions, monkeypatch):
    """RM-AGENT-041 — tests/brd/reminders.feature"""
    async with sessions() as session:
        assert await open_items(session, None, "") == NO_OPEN_ITEMS
        daily = dict(schedule="daily 21:00", schedule_rule=await rule_for(session, "daily 21:00"))
        checks = [await create_check(session, title=f"Трекер {n}?", **daily) for n in range(4)]
        await session.commit()
    monkeypatch.setattr(items, "SUBAGENT_ITEMS_MAX", 2)
    monkeypatch.setattr(items, "OPEN_ITEMS_MAX", 3)
    closeness = Closeness({("check", checks[3].id): 0.9, ("check", checks[2].id): 0.4})
    said = "<User>" + "старые слова " * 200 + "</User>\n<User>напомни про трекер 3</User>"

    blocks = await reminder_items(context_of(sessions, closeness), said)

    assert blocks.split("\n\n")[0] == "\n".join(
        [
            SCHEDULED,
            f"- Check #{checks[3].id} «Трекер 3?»; schedule: daily 21:00",
            f"- Check #{checks[2].id} «Трекер 2?»; schedule: daily 21:00",
        ]
    )
    async with sessions() as session:
        assert await open_items(session, closeness, said) == "\n".join(
            [
                OPEN_ITEMS,
                f"- Check #{checks[3].id} «Трекер 3?»; schedule: daily 21:00",
                f"- Check #{checks[2].id} «Трекер 2?»; schedule: daily 21:00",
                f"- Check #{checks[0].id} «Трекер 0?»; schedule: daily 21:00",
            ]
        )
    # The end of what was said, as words: its newest RANKED_BY_TOKENS tokens, without who said it.
    (words, views), _ = closeness.asked
    assert words.endswith("напомни про трекер 3") and "<User>" not in words
    assert estimate_tokens(words) <= RANKED_BY_TOKENS < estimate_tokens(said)
    assert views == ("ai_checks", "ai_cards", "ai_reminders")


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
