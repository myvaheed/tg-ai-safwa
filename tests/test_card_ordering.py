from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from schedule_helpers import create_card

from safwa.bootstrap.modules import ALLOWED_VIEWS
from safwa.features.cards import ordering
from safwa.features.cards.api import actions_on_stages, order_cards
from safwa.features.cards.model import CardStage
from safwa.features.cards.use_cases import (
    create_card as domain_card,
)
from safwa.features.cards.use_cases import (
    finish_action,
    toggle_card_parent,
    update_card_fields,
)
from safwa.features.home.dashboard import dashboard_text
from safwa.features.planning.model import Sprint, SprintCommitment
from safwa.features.saved_requests.api import request_items
from safwa.features.values.use_cases import create_value, update_value_fields
from safwa.features.workspace_mutator import state
from safwa.foundation.workspace import require_workspace

NOW = datetime(2030, 10, 15, 12, tzinfo=ZoneInfo("Europe/Istanbul"))


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(ordering, "utcnow", lambda: NOW)
    monkeypatch.setattr(state, "utcnow", lambda: NOW)


async def _action(session, title, *, day=None, hour=12, **fields):
    appointment = NOW.replace(hour=hour) + timedelta(days=day) if day is not None else None
    return await create_card(
        session, kind="action", title=title,
        schedule=appointment.strftime("%d.%m.%Y %H:%M") if appointment else None,
        **fields,
    )


async def _goal(session, title, *, day=None, **fields):
    deadline = NOW.date() + timedelta(days=day) if day is not None else None
    return await domain_card(
        session, kind="goal", title=title,
        schedule=deadline.isoformat() if deadline else None,
        schedule_rule={"kind": "deadline", "date": deadline.isoformat(), "time": None}
        if deadline else None,
        **fields,
    )


async def _sprint(session, *, length, elapsed=0):
    start = NOW.date() - timedelta(days=elapsed)
    sprint = Sprint(
        number="30.10-01", planned_start_date=start,
        planned_end_date=start + timedelta(days=length - 1), actual_started_at=NOW,
    )
    session.add(sprint)
    await session.flush()
    (await require_workspace(session)).active_sprint_id = sprint.id
    return sprint


async def _values(session, count=4):
    return [await create_value(session, f"Value {i}", "What matters") for i in range(count)]


@pytest.mark.parametrize("day,dated_first", [
    (-1, True), (0, True), (1, True), (2, True), (7, True),
    (8, False), (None, False),
])
async def test_action_date_windows_have_one_common_order(sessions, day, dated_first):
    """CD-ORDER-060 — tests/brd/cards.feature"""
    async with sessions() as session:
        critical = await _action(session, "Critical", priority="critical")
        dated = await _action(session, "Dated", priority="low", day=day)
        ordered = await order_cards(session, [critical, dated])
    assert ordered == ([dated, critical] if dated_first else [critical, dated])


@pytest.mark.parametrize("day,earlier_first", [(0, True), (1, True), (2, False)])
async def test_action_time_and_priority_precedence_changes_by_group(sessions, day, earlier_first):
    """CD-ORDER-060 — tests/brd/cards.feature"""
    async with sessions() as session:
        later = await _action(session, "Critical later", day=day, hour=18, priority="critical")
        earlier = await _action(session, "Low earlier", day=day, hour=9, priority="low")
        ordered = await order_cards(session, [later, earlier])
    assert ordered == ([earlier, later] if earlier_first else [later, earlier])


@pytest.mark.parametrize("day", [None, 8])
async def test_last_action_group_compares_values_before_appointments(sessions, day):
    """CD-ORDER-060 — tests/brd/cards.feature"""
    async with sessions() as session:
        [value] = await _values(session, 1)
        earlier = await _action(session, "Earlier appointment", day=8, hour=9)
        stronger = await _action(session, "Value", day=day, hour=18, value_ids={value.id})
        assert await order_cards(session, [earlier, stronger]) == [stronger, earlier]


@pytest.mark.parametrize("day", [0, 2])
async def test_first_two_action_groups_compare_time_before_values(sessions, day):
    """CD-ORDER-060 — tests/brd/cards.feature"""
    async with sessions() as session:
        [value] = await _values(session, 1)
        later = await _action(session, "Value later", day=day, hour=18, value_ids={value.id})
        earlier = await _action(session, "Earlier", day=day, hour=9)
        assert await order_cards(session, [later, earlier]) == [earlier, later]


async def test_equal_cards_use_creation_time_then_id(sessions):
    """CD-ORDER-060 — tests/brd/cards.feature"""
    async with sessions() as session:
        first = await _action(session, "First")
        second = await _action(session, "Second")
        older = await _action(session, "Older")
        first.created_at = second.created_at = NOW
        older.created_at = NOW - timedelta(days=1)
        assert await order_cards(session, [second, first, older]) == [older, first, second]


@pytest.mark.parametrize("kind", ["action", "goal"])
async def test_the_strongest_value_on_a_parent_outweighs_the_weakest_on_the_card(sessions, kind):
    """CD-ORDER-062 — tests/brd/cards.feature"""
    async with sessions() as session:
        values = await _values(session)
        parent = await _goal(session, "Parent", value_ids={values[0].id})
        create = _action if kind == "action" else _goal
        near = await create(session, "Near", value_ids={values[-1].id})
        far = await create(session, "Far", parent_ids={parent.id})
        assert await order_cards(session, [near, far]) == [far, near]


@pytest.mark.parametrize("kind", ["action", "goal"])
async def test_the_same_value_weighs_more_on_the_card_than_on_its_parent(sessions, kind):
    """CD-ORDER-062 — tests/brd/cards.feature"""
    async with sessions() as session:
        values = await _values(session)
        parent = await _goal(session, "Parent", value_ids={values[1].id})
        create = _action if kind == "action" else _goal
        far = await create(session, "Far", parent_ids={parent.id})
        near = await create(session, "Near", value_ids={values[1].id})
        assert await order_cards(session, [far, near]) == [near, far]


async def test_two_branches_with_different_values_can_outweigh_one_stronger_branch(sessions):
    """CD-ORDER-062 — tests/brd/cards.feature"""
    async with sessions() as session:
        values = await _values(session)
        strongest = await _goal(session, "Strongest", value_ids={values[0].id})
        branches = [await _goal(session, f"Branch {i}", value_ids={values[i].id}) for i in (1, 2)]
        one = await _action(session, "One branch", parent_ids={strongest.id})
        two = await _action(session, "Two branches", parent_ids={goal.id for goal in branches})
        assert await order_cards(session, [one, two]) == [two, one]


async def test_a_value_reached_through_two_parents_counts_once(sessions):
    """CD-ORDER-062 — tests/brd/cards.feature"""
    async with sessions() as session:
        values = await _values(session)
        single = await _goal(session, "Single", value_ids={values[0].id})
        parents = [await _goal(session, f"Parent {i}", value_ids={values[0].id}) for i in range(2)]
        control = await _action(session, "One route", parent_ids={single.id})
        shared = await _action(session, "Two routes", parent_ids={goal.id for goal in parents})
        control.created_at = shared.created_at = NOW
        assert await order_cards(session, [shared, control]) == [control, shared]


async def test_the_nearest_occurrence_counts_once_even_with_a_longer_path(sessions):
    """CD-ORDER-062 — tests/brd/cards.feature"""
    async with sessions() as session:
        values = await _values(session)
        parent = await _goal(session, "Parent", value_ids={values[0].id})
        middle = await _goal(session, "Middle", parent_ids={parent.id})
        control = await _action(session, "Same nearest distance", parent_ids={parent.id})
        shared = await _action(session, "Two routes", parent_ids={middle.id, parent.id})
        control.created_at = shared.created_at = NOW
        assert await order_cards(session, [shared, control]) == [control, shared]
        await toggle_card_parent(session, shared.id, parent.id)
        assert await order_cards(session, [shared, control]) == [control, shared]


async def test_value_reordering_changes_the_order_without_rewriting_cards(sessions):
    """CD-ORDER-062 — tests/brd/cards.feature"""
    async with sessions() as session:
        first, second = await _values(session, 2)
        weaker = await _action(session, "Second", value_ids={second.id})
        stronger = await _action(session, "First", value_ids={first.id})
        assert await order_cards(session, [weaker, stronger]) == [stronger, weaker]
        await update_value_fields(session, second.id, order=1)
        assert await order_cards(session, [stronger, weaker]) == [weaker, stronger]


@pytest.mark.parametrize("length,elapsed,day,urgent", [
    (None, 0, -1, True), (None, 0, 29, True), (None, 0, 30, False),
    (14, 0, 13, True), (14, 0, 14, False),
    (7, 0, 13, True), (7, 0, 14, False),
    (14, 10, 13, True), (14, 10, 14, False), (21, 0, 20, True),
])
async def test_goal_urgency_uses_the_sprint_or_planning_window(
    sessions, length, elapsed, day, urgent,
):
    """CD-ORDER-061 — tests/brd/cards.feature"""
    async with sessions() as session:
        if length is not None:
            await _sprint(session, length=length, elapsed=elapsed)
        critical = await _goal(session, "Critical", priority="critical", day=60)
        deadline = await _goal(session, "Deadline", priority="low", day=day)
        ordered = await order_cards(session, [critical, deadline])
    assert ordered == ([deadline, critical] if urgent else [critical, deadline])


@pytest.mark.parametrize("day,deadline_first", [(90, True), (91, False), (None, False)])
async def test_goal_quarter_boundary_and_no_deadline(sessions, day, deadline_first):
    """CD-ORDER-061 — tests/brd/cards.feature"""
    async with sessions() as session:
        critical = await _goal(session, "Critical", priority="critical")
        deadline = await _goal(session, "Deadline", priority="low", day=day)
        ordered = await order_cards(session, [critical, deadline])
    assert ordered == ([deadline, critical] if deadline_first else [critical, deadline])


async def test_goal_group_zero_compares_deadline_then_height_then_priority(sessions):
    """CD-ORDER-061 — tests/brd/cards.feature"""
    async with sessions() as session:
        critical = await _goal(session, "Critical", priority="critical", day=1)
        high = await _goal(session, "High", priority="low", day=1)
        await _goal(session, "Child", parent_ids={high.id})
        earlier = await _goal(session, "Earlier", priority="low", day=0)
        assert await order_cards(session, [critical, high, earlier]) == [earlier, high, critical]


@pytest.mark.parametrize("day", [60, 100, None])
async def test_other_goal_groups_compare_priority_before_height(sessions, day):
    """CD-ORDER-061 — tests/brd/cards.feature"""
    async with sessions() as session:
        high = await _goal(session, "High low-priority", priority="low", day=day)
        await _goal(session, "Child", parent_ids={high.id})
        critical = await _goal(session, "Critical", priority="critical", day=day)
        assert await order_cards(session, [high, critical]) == [critical, high]


@pytest.mark.parametrize("day", [60, 100])
async def test_goal_height_precedes_deadline_and_values_in_later_groups(sessions, day):
    """CD-ORDER-061 — tests/brd/cards.feature"""
    async with sessions() as session:
        [value] = await _values(session, 1)
        lower = await _goal(session, "Lower earlier with Value", day=day, value_ids={value.id})
        higher = await _goal(session, "Higher later", day=day + 1)
        await _goal(session, "Child", parent_ids={higher.id})
        assert await order_cards(session, [lower, higher]) == [higher, lower]


@pytest.mark.parametrize("day,later_first", [(60, False), (100, True)])
async def test_goal_score_and_deadline_precedence_changes_by_group(sessions, day, later_first):
    """CD-ORDER-061 — tests/brd/cards.feature"""
    async with sessions() as session:
        [value] = await _values(session, 1)
        earlier = await _goal(session, "Earlier", day=day)
        later = await _goal(session, "Later with Value", day=day + 1, value_ids={value.id})
        ordered = await order_cards(session, [earlier, later])
    assert ordered == ([later, earlier] if later_first else [earlier, later])


async def _key(session, sprint, action):
    session.add(SprintCommitment(sprint_id=sprint.id, card_id=action.id, key_action=True))
    await session.flush()


@pytest.mark.parametrize("day", [0, 2, None])
async def test_a_key_action_leads_its_equals_but_not_a_higher_priority(sessions, day):
    """CD-ORDER-060 — tests/brd/cards.feature"""
    async with sessions() as session:
        sprint = await _sprint(session, length=14)
        [value] = await _values(session, 1)
        plain = await _action(session, "Plain with Value", day=day, value_ids={value.id})
        key = await _action(session, "Key", day=day)
        critical = await _action(session, "Critical", day=day, priority="critical")
        await _key(session, sprint, key)
        assert await order_cards(session, [plain, key, critical]) == [critical, key, plain]


async def test_key_marks_count_only_while_their_sprint_runs(sessions):
    """CD-ORDER-060 — tests/brd/cards.feature"""
    async with sessions() as session:
        sprint = await _sprint(session, length=14)
        [value] = await _values(session, 1)
        plain = await _action(session, "Plain with Value", value_ids={value.id})
        key = await _action(session, "Key")
        await _key(session, sprint, key)
        assert await order_cards(session, [plain, key]) == [key, plain]
        (await require_workspace(session)).active_sprint_id = None
        assert await order_cards(session, [plain, key]) == [plain, key]


async def test_action_windows_use_local_dates_instead_of_utc_dates(sessions):
    """CD-ORDER-060 — tests/brd/cards.feature"""
    async with sessions() as session:
        earlier = await _action(session, "Earlier low", day=2, hour=9, priority="low")
        later = await _action(session, "Later critical", day=2, hour=18, priority="critical")
        # It is already the 16th in Istanbul, so both appointments on the 17th are group 0.
        now = datetime.fromisoformat("2030-10-15T22:30:00+00:00")
        assert await order_cards(session, [later, earlier], now=now) == [earlier, later]


async def test_goal_priority_and_values_do_not_come_from_children(sessions):
    """CD-ORDER-063 — tests/brd/cards.feature"""
    async with sessions() as session:
        values = await _values(session)
        goal = await _goal(session, "Own priority", priority="low")
        child = await _action(session, "Critical child", priority="critical", parent_ids={goal.id},
                              value_ids={values[0].id}, stage="today")
        control = await _goal(session, "Own Value", priority="low", value_ids={values[-1].id})
        assert await order_cards(session, [goal, control]) == [control, goal]
        await update_card_fields(session, child.id, {"priority": "medium"})
        await finish_action(session, child.id)
        await session.refresh(goal)
        assert goal.priority == "low"


async def test_home_groups_today_by_goals_and_the_context_ranks_it(sessions):
    """PL-KEY-025 — tests/brd/planning.feature"""
    async with sessions() as session:
        first_goal = await _goal(session, "First Goal")
        second_goal = await _goal(session, "Second Goal")
        first = await _action(session, "Appointment", day=0, parent_ids={first_goal.id}, stage="today")
        last = await _action(session, "Ordinary", parent_ids={first_goal.id}, stage="today")
        second = await _action(session, "Critical", priority="critical", parent_ids={second_goal.id}, stage="today")
        await session.commit()
        assert await order_cards(session, await actions_on_stages(session, CardStage.TODAY)) == [first, second, last]
        home = (await dashboard_text(session, {}, now=NOW)).split("**☀️ Today", 1)[1]
        context = (await state.workspace_context(session)).state.split("Today Actions", 1)[1]
    assert home.index("[Appointment]") < home.index("[Ordinary]") < home.index("[Critical]")
    assert home.count("[First Goal]") == 1
    assert context.index("[Appointment]") < context.index("[Critical]") < context.index("[Ordinary]")
    assert context.count(f"under=[First Goal](card:{first_goal.id})") == 2


async def test_focus_puts_the_goal_with_fewer_levels_first(sessions):
    """CD-ORDER-066 — tests/brd/cards.feature"""
    async with sessions() as session:
        tall = await _goal(session, "Tall")
        await _goal(session, "Child", parent_ids={tall.id})
        short = await _goal(session, "Short")
        assert await order_cards(session, [short, tall]) == [tall, short]
        assert await order_cards(session, [tall, short], focus=True) == [short, tall]


async def test_focus_reads_today_then_the_sprint_only_while_a_sprint_runs(sessions):
    """CD-ORDER-066 — tests/brd/cards.feature"""
    async with sessions() as session:
        backlog = await _action(session, "Critical in Backlog", priority="critical")
        later = await _action(session, "Low in Sprint", stage="sprint", priority="low")
        today = await _action(session, "Low in Today", stage="today", priority="low")
        soon = await _action(session, "Sprint tomorrow", stage="sprint", day=1)
        cards = [backlog, later, today, soon]
        planning = [soon, backlog, later, today]
        assert await order_cards(session, cards) == planning
        assert await order_cards(session, cards, focus=True) == planning
        await _sprint(session, length=14)
        assert await order_cards(session, cards) == planning
        assert await order_cards(session, cards, focus=True) == [today, soon, later, backlog]


async def test_sql_reads_keep_their_own_limit_offset_and_order(read_views):
    """CD-ORDER-064 — tests/brd/cards.feature"""
    sessions, runner = read_views
    async with sessions() as session:
        old = await _action(session, "Old medium")
        critical = await _action(session, "New critical", priority="critical")
        await session.commit()
        for query, expected in (
            ("SELECT 'card' AS item_type, id FROM ai_cards LIMIT 1", old),
            ("SELECT 'card' AS item_type, id FROM ai_cards LIMIT 1 OFFSET 1", critical),
            ("SELECT 'card' AS item_type, id, 'ORDER BY id' AS note FROM ai_cards LIMIT 1", old),
            ("WITH chosen AS (SELECT 'card' AS item_type, id FROM ai_cards) SELECT item_type, id FROM chosen LIMIT 1", old),
            ("SELECT 'card' AS item_type, id FROM ai_cards ORDER BY id DESC LIMIT 1", critical),
        ):
            assert await request_items(session, query, ALLOWED_VIEWS) == [expected]
            assert (await runner.run(query)).rows[0]["id"] == expected.id
        aggregate = "SELECT kind, count(*) AS id FROM ai_cards GROUP BY kind"
        assert (await runner.run(aggregate)).rows == [{"kind": "action", "id": 2}]
