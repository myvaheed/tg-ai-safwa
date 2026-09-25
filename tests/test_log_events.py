"""The log of changes: what each of the six items writes, and how the Advisor reads it."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from safwa.bootstrap.modules import ALLOWED_VIEWS, PROPOSALS, SYSTEM_PROMPT
from safwa.enums import ActorType
from safwa.features.advisor.agent import ADVISOR_ROW_LIMITS, ADVISOR_VIEWS
from safwa.features.cards.model import CardStage
from safwa.features.cards.use_cases import (
    create_card,
    delete_subtree,
    finish_action,
    move_card,
    update_card_fields,
)
from safwa.features.checks.use_cases import (
    create_check,
    delete_check,
    resolve_check,
    update_check_fields,
)
from safwa.features.planning.use_cases import start_sprint
from safwa.features.reminders.schedule import resolve, schedule_payload
from safwa.features.reminders.use_cases import (
    create_reminder,
    delete_reminder,
    update_reminder_text,
)
from safwa.features.saved_requests.use_cases import (
    create_saved_request,
    delete_saved_request,
    update_saved_request,
)
from safwa.features.tags.use_cases import create_tag, delete_tag, update_tag_fields
from safwa.features.values.use_cases import create_value, delete_value, update_value_fields
from safwa.foundation.log_events import LOG_EVENTS_SHOWN, LogEvent
from tg_agent_shell.proposals.api import ApplyContext
from tg_agent_shell.proposals.model import ChangeAction, ProposalChange

TZ = ZoneInfo("Europe/Istanbul")
QUERY = "SELECT id FROM ai_cards"

# What a new item is made with, and the one field its rename changes, per type.
CREATED = {
    "card": {"kind": "action", "title": "Walk", "effort_points": 1},
    "check": {"title": "Walk"},
    "value": {"name": "Walk"},
    "tag": {"name": "Walk"},
    "request": {"name": "Walk", "query_sql": QUERY},
    "reminder": {"instruction": "Walk"},
}
RENAMED = {
    "card": {"title": "Walk far"},
    "check": {"title": "Walk far"},
    "value": {"name": "Walk far"},
    "tag": {"name": "Walk far"},
    "request": {"name": "Walk far"},
    "reminder": {"instruction": "Walk far"},
}


def _schedule():
    return resolve(interval_minutes=120, now=datetime.now(UTC), tz=TZ)


async def _by_hand(session) -> dict[str, int]:
    """Each of the six made, renamed and deleted through the calls its screens make."""
    card = await create_card(session, kind="action", title="Walk", effort_points=1)
    await update_card_fields(session, card.id, {"title": "Walk far"})
    await delete_subtree(session, card.id)
    check = await create_check(session, title="Walk")
    await update_check_fields(session, check.id, {"title": "Walk far"})
    await delete_check(session, check.id)
    value = await create_value(session, "Walk")
    await update_value_fields(session, value.id, name="Walk far")
    await delete_value(session, value.id)
    tag = await create_tag(session, "Walk")
    await update_tag_fields(session, tag.id, name="Walk far")
    await delete_tag(session, tag.id)
    request = await create_saved_request(session, "Walk", QUERY, views=ALLOWED_VIEWS)
    await update_saved_request(session, request.id, name="Walk far", views=ALLOWED_VIEWS)
    await delete_saved_request(session, request.id)
    reminder = await create_reminder(session, instruction="Walk", schedule=_schedule(), tz=TZ)
    await update_reminder_text(session, reminder.id, "Walk far")
    await delete_reminder(session, reminder.id)
    return {
        "card": card.id,
        "check": check.id,
        "value": value.id,
        "tag": tag.id,
        "request": request.id,
        "reminder": reminder.id,
    }


async def _by_proposal(session) -> dict[str, int]:
    """Each of the six made, renamed and deleted by saved proposals."""
    context = ApplyContext(session, frozenset(ALLOWED_VIEWS), allow_destructive=True)
    ids: dict[str, int] = {}
    for entity, values in CREATED.items():
        if entity == "reminder":
            values = {**values, "schedule": schedule_payload(_schedule())}
        handler = PROPOSALS.handler(entity)
        [item_id] = await handler.apply(
            context, ProposalChange(entity=entity, action=ChangeAction.CREATE, values=values)
        )
        ids[entity] = item_id
        for action, change in ((ChangeAction.UPDATE, RENAMED[entity]), (ChangeAction.DELETE, {})):
            version = await session.scalar(
                select(LogEvent.after).where(LogEvent.item_type == entity, LogEvent.item_id == item_id)
                .order_by(LogEvent.id.desc())
            )
            await handler.apply(
                context,
                ProposalChange(
                    entity=entity,
                    action=action,
                    entity_id=item_id,
                    expected_version=version["version"],
                    values=change,
                ),
            )
    return ids


async def _written(session, ids: dict[str, int], since: int) -> dict[str, list[tuple]]:
    """The events of each item written after event `since`: a deleted item's id may be
    another's by now."""
    return {
        item_type: [
            (event.title, event.operation, event.actor, event.before is None, event.after is None)
            for event in await session.scalars(
                select(LogEvent)
                .where(
                    LogEvent.item_type == item_type, LogEvent.item_id == item_id, LogEvent.id > since
                )
                .order_by(LogEvent.id)
            )
        ]
        for item_type, item_id in ids.items()
    }


async def test_ad_log_003_each_item_writes_one_row_per_change_and_keeps_them_deleted(sessions):
    """AD-LOG-003 — tests/brd/advisor.feature"""
    async with sessions() as session:
        by_hand = await _written(session, await _by_hand(session), since=0)
        last = await session.scalar(select(func.max(LogEvent.id)))
        by_proposal = await _written(session, await _by_proposal(session), since=last)
        await session.commit()

    for actor, written in ((ActorType.USER_UI, by_hand), (ActorType.AI, by_proposal)):
        for item_type, events in written.items():
            # Titled as it stood at each moment; the deletion keeps what it was before.
            assert events == [
                ("Walk", "create", actor.value, True, False),
                ("Walk far", "update", actor.value, False, False),
                ("Walk far", "delete", actor.value, False, True),
            ], (actor, item_type)


async def test_ad_log_003_an_answer_is_a_row_and_each_names_the_running_sprint(sessions):
    """AD-LOG-003 — tests/brd/advisor.feature"""
    async with sessions() as session:
        planned = await create_card(
            session, kind="action", title="Plan", stage="sprint", effort_points=1
        )
        sprint = await start_sprint(session, success_criteria="Ship")
        posture = await create_check(session, title="Posture straight?")
        milk = await create_check(session, title="Milk?")
        await resolve_check(session, posture.id, "passed", actor=ActorType.AI)
        await resolve_check(session, milk.id, "missed")
        await session.commit()

        answers = list(
            await session.execute(
                select(LogEvent.item_id, LogEvent.operation, LogEvent.actor)
                .where(LogEvent.operation.in_(("passed", "missed")))
                .order_by(LogEvent.id)
            )
        )
        created = select(LogEvent.sprint_id).where(LogEvent.operation == "create")
        before_it = await session.scalar(
            created.where(LogEvent.item_type == "card", LogEvent.item_id == planned.id)
        )
        during_it = await session.scalar(
            created.where(LogEvent.item_type == "check", LogEvent.item_id == posture.id)
        )

    assert answers == [(posture.id, "passed", "ai"), (milk.id, "missed", "user_ui")]
    assert before_it is None
    assert during_it == sprint.id


async def test_ad_log_004_a_row_reads_as_its_mode_and_its_local_day(read_views):
    """AD-LOG-004 — tests/brd/advisor.feature"""
    sessions, runner = read_views
    async with sessions() as session:
        card = await create_card(session, kind="action", title="Walk", effort_points=1)
        await move_card(session, card.id, CardStage.TODAY)
        await finish_action(session, card.id)
        await delete_subtree(session, card.id)
        await session.commit()
        stamped = await session.scalar(select(LogEvent.at).where(LogEvent.item_id == card.id))

    outcome = await runner.run(
        "SELECT title, mode, operation, date FROM ai_log_events WHERE item_type = 'card' "
        "ORDER BY id"
    )

    local_day = stamped.astimezone(TZ).date().isoformat()
    assert outcome.rows == [
        {"title": "Walk", "mode": "created", "operation": "create", "date": local_day},
        {"title": "Walk", "mode": "updated", "operation": "move", "date": local_day},
        {"title": "Walk", "mode": "updated", "operation": "done", "date": local_day},
        {"title": "Walk", "mode": "deleted", "operation": "delete", "date": local_day},
    ]


async def test_ad_log_004_a_deleted_item_has_no_id_and_one_still_there_keeps_it(read_views):
    """AD-LOG-004 — tests/brd/advisor.feature"""
    sessions, runner = read_views
    async with sessions() as session:
        gone = await create_tag(session, "Walk")
        await delete_tag(session, gone.id)
        # The next Tag takes the id the deleted one had, so a link would open the wrong Tag.
        again = await create_tag(session, "Swim")
        await update_tag_fields(session, again.id, name="Swim far")
        await session.commit()

    outcome = await runner.run("SELECT item_id, title, mode FROM ai_log_events ORDER BY id")

    assert again.id == gone.id
    assert outcome.rows == [
        {"item_id": None, "title": "Walk", "mode": "created"},
        {"item_id": None, "title": "Walk", "mode": "deleted"},
        {"item_id": again.id, "title": "Swim", "mode": "created"},
        {"item_id": again.id, "title": "Swim far", "mode": "updated"},
    ]


async def test_ad_log_004_the_advisor_reads_the_newest_rows_and_is_told_to_count_the_rest(
    read_views,
):
    """AD-LOG-004 — tests/brd/advisor.feature"""
    sessions, runner = read_views
    advisor = runner.scoped(ADVISOR_VIEWS, row_limits=ADVISOR_ROW_LIMITS)
    async with sessions() as session:
        for number in range(LOG_EVENTS_SHOWN + 5):
            await create_card(session, kind="action", title=f"Walk {number}", effort_points=1)
        await session.commit()

    log = await advisor.run("SELECT title FROM ai_log_events ORDER BY id DESC")
    cards = await advisor.run("SELECT title FROM ai_cards")

    assert len(log.rows) == LOG_EVENTS_SHOWN
    assert log.rows[0] == {"title": f"Walk {LOG_EVENTS_SHOWN + 4}"}
    assert log.notice and "more rows match" in log.notice
    # Only the log is cut short: every other view reads as far as it did.
    assert len(cards.rows) == LOG_EVENTS_SHOWN + 5 and cards.notice is None
    assert "`ORDER BY id DESC`" in SYSTEM_PROMPT
    assert "count instead with `GROUP BY mode, item_type`" in SYSTEM_PROMPT
    assert "ask which to list" in SYSTEM_PROMPT
    assert "name it by its `title`, with no link" in SYSTEM_PROMPT
