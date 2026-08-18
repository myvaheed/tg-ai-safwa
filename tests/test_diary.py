from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from safwa.ai.context import SYSTEM_PROMPT
from safwa.ai.contracts import DiaryToolInput, mutation_change_from_tool
from safwa.ai.diary import DIARY_PROMPT
from safwa.ai.prepare import ChangePreparer, ToolPreparationError
from safwa.ai.service import _approval_results_summary
from safwa.ai.sql import ALLOWED_VIEWS
from safwa.constants import DIARY_TIME_DEFAULT, FEELING_SCORE_EMOJI
from safwa.domain import (
    DIARY_REMINDER_INSTRUCTION,
    DomainError,
    create_diary_entry,
    delete_diary_entry,
    delete_reminder,
    diary_entry_for,
    reschedule_reminder,
    sync_diary_reminder,
    update_diary_entry,
    update_profile,
    update_reminder_text,
)
from safwa.enums import ScheduleKind
from safwa.models import DiaryEntry, Reminder
from safwa.reminders import resolve, schedule_of
from safwa.telegram._presentation import diary_label


def preparer() -> ChangePreparer:
    """A Diary change needs neither the provider nor a query runner to prepare."""
    return ChangePreparer(None, None)  # type: ignore[arg-type]


async def prepared(sessions, tool_arguments: dict[str, Any]) -> Any:
    change = mutation_change_from_tool("diary", tool_arguments)
    async with sessions() as session:
        result = await preparer().prepare(session, change)
    return change, result


def test_both_readers_are_told_about_the_diary_view() -> None:
    """A view missing from a prompt is a view that reader can never use."""
    assert "ai_diary" in ALLOWED_VIEWS
    assert "`ai_diary(id, entry_date, body, feeling_score" in DIARY_PROMPT
    # The advisor reads days for itself; only writing one goes through the subagent.
    assert "ai_diary(id, entry_date, body, feeling_score" in SYSTEM_PROMPT
    assert "(diary:12)" in SYSTEM_PROMPT


def test_a_written_day_carries_its_text_and_a_removal_carries_none() -> None:
    with pytest.raises(ValueError):
        DiaryToolInput.model_validate({"mode": "update", "date": "2026-08-15"})
    with pytest.raises(ValueError):
        DiaryToolInput.model_validate({"mode": "delete", "date": "2026-08-15", "pov": "День."})
    with pytest.raises(ValueError):
        DiaryToolInput.model_validate({"mode": "update", "date": "15.08.2026", "pov": "День."})
    assert DiaryToolInput.model_validate({"mode": "delete", "date": "2026-08-15"}).pov is None


def test_a_feeling_score_runs_from_zero_to_ten() -> None:
    written = DiaryToolInput.model_validate(
        {"mode": "update", "date": "2026-08-15", "pov": "День.", "feeling_score": 0}
    )
    assert written.feeling_score == 0
    for out_of_scale in (-1, 11):
        with pytest.raises(ValueError):
            DiaryToolInput.model_validate(
                {
                    "mode": "update",
                    "date": "2026-08-15",
                    "pov": "День.",
                    "feeling_score": out_of_scale,
                }
            )
    with pytest.raises(ValueError):
        DiaryToolInput.model_validate({"mode": "delete", "date": "2026-08-15", "feeling_score": 7})


def test_the_scale_is_stated_once_and_the_model_never_reaches_for_zero() -> None:
    assert set(FEELING_SCORE_EMOJI) == set(range(11))
    assert "Never choose 0 yourself." in DIARY_PROMPT
    assert diary_label(date(2026, 3, 4), 6) == "4 марта · 🙂6"
    # A day that said nothing about how it felt is named by its date alone.
    assert diary_label(date(2026, 3, 4), None) == "4 марта"


def test_the_call_says_what_was_asked_for_and_nothing_about_the_data() -> None:
    change = mutation_change_from_tool(
        "diary",
        {"mode": "update", "date": "2026-08-15", "pov": "День.", "ai_comment": "Held."},
    )
    # Whether that day exists is not the model's to know; preparation settles it.
    assert (change.entity, change.action, change.id) == ("diary", "update", None)
    assert "mode" not in change.values


async def test_preparation_settles_a_written_day_on_create_or_update(sessions) -> None:
    today = date.today()
    change, result = await prepared(
        sessions,
        {"mode": "update", "date": today.isoformat(), "pov": "Первый день.", "feeling_score": 8},
    )
    assert (change.action, change.id) == ("create", None)
    assert result.values == {
        "entry_date": today.isoformat(),
        "body": "Первый день.",
        "feeling_score": 8,
        "ai_comment": "",
    }

    async with sessions() as session:
        entry = await create_diary_entry(session, entry_date=today, body="Уже записано.")
        await session.commit()

    change, result = await prepared(
        sessions, {"mode": "update", "date": today.isoformat(), "pov": "Переписал."}
    )
    assert (change.action, change.id) == ("update", entry.id)
    assert result.expected_version == entry.version


async def test_removing_a_day_that_was_never_written_is_refused(sessions) -> None:
    today = date.today()
    with pytest.raises(ToolPreparationError) as refused:
        await prepared(sessions, {"mode": "delete", "date": today.isoformat()})
    assert refused.value.code == "target_not_found"

    async with sessions() as session:
        entry = await create_diary_entry(session, entry_date=today, body="Есть что удалять.")
        await session.commit()

    change, result = await prepared(sessions, {"mode": "delete", "date": today.isoformat()})
    assert (change.action, change.id) == ("delete", entry.id)
    assert result.values == {"entry_date": today.isoformat()}


def test_a_diary_receipt_names_the_day_and_never_repeats_it() -> None:
    tool: dict[str, Any] = {
        "change": {"entity": "diary", "action": "create", "values": {}},
        "display": "New Diary entry for 2026-08-16",
        "details": ["Date: 2026-08-16", "Entry: 5 characters"],
        "target": {"type": "proposal", "id": 1},
    }
    discarded = _approval_results_summary(
        [{**tool, "result": {"status": "discarded"}}], for_display=True
    )
    saved = _approval_results_summary(
        [{**tool, "result": {"status": "approved"}}], for_display=True
    )

    # A refused day is still held by its own session, so nothing has to be handed back.
    assert discarded == "🗑 Discarded — New Diary entry for 2026-08-16"
    assert saved == "✅ Saved — New Diary entry for 2026-08-16"


async def test_a_day_holds_one_entry_and_a_later_write_replaces_it(sessions) -> None:
    async with sessions() as session:
        entry = await create_diary_entry(
            session, entry_date=date(2026, 8, 15), body="Долгий день."
        )
        await session.commit()

        with pytest.raises(DomainError):
            await create_diary_entry(
                session, entry_date=date(2026, 8, 15), body="Ещё раз про тот же день."
            )

        await update_diary_entry(session, entry.id, "Долгий день, но закончился хорошо.")
        await session.commit()

        rows = list(await session.scalars(select(DiaryEntry)))
        same_day = await diary_entry_for(session, date(2026, 8, 15))
    assert [row.id for row in rows] == [entry.id]
    assert same_day is not None
    assert same_day.body == "Долгий день, но закончился хорошо."
    assert same_day.version == 2

    async with sessions() as session:
        await delete_diary_entry(session, entry.id)
        await session.commit()
        assert await diary_entry_for(session, date(2026, 8, 15)) is None


# --- the system Reminder behind the Diary ---------------------------------


async def system_reminder(sessions) -> Reminder | None:
    async with sessions() as session:
        return await session.scalar(select(Reminder).where(Reminder.system.is_(True)))


async def test_settings_is_the_only_source_of_the_diary_reminder(sessions) -> None:
    async with sessions() as session:
        await sync_diary_reminder(session)
        await session.commit()
    reminder = await system_reminder(sessions)
    assert reminder is not None
    assert reminder.at_time == time.fromisoformat(DIARY_TIME_DEFAULT)
    assert schedule_of(reminder).kind is ScheduleKind.DAILY
    first_fire = reminder.next_fire_at

    # A restart re-runs the projection; an unchanged clock must not push the fire away.
    async with sessions() as session:
        await sync_diary_reminder(session)
        await session.commit()
    assert (await system_reminder(sessions)).next_fire_at == first_fire

    async with sessions() as session:
        await update_profile(session, diary_time=time(7, 30))
        await session.commit()
    moved = await system_reminder(sessions)
    assert moved.at_time == time(7, 30)
    assert moved.next_fire_at != first_fire

    async with sessions() as session:
        await update_profile(session, diary_time=None)
        await session.commit()
    assert await system_reminder(sessions) is None


async def test_the_extra_instruction_reaches_the_reminder_text(sessions) -> None:
    async with sessions() as session:
        await update_profile(session, diary_instructions="Спроси про сон.")
        await session.commit()
    reminder = await system_reminder(sessions)
    assert reminder.instruction.startswith(DIARY_REMINDER_INSTRUCTION)
    assert reminder.instruction.endswith("Спроси про сон.")

    async with sessions() as session:
        await update_profile(session, diary_instructions="")
        await session.commit()
    assert (await system_reminder(sessions)).instruction == DIARY_REMINDER_INSTRUCTION


async def test_the_diary_reminder_is_not_the_owners_to_edit(sessions) -> None:
    async with sessions() as session:
        await sync_diary_reminder(session)
        await session.commit()
    reminder_id = (await system_reminder(sessions)).id
    schedule = resolve(clock="09:00", days=["Mon"], now=datetime.now(UTC), tz=ZoneInfo("UTC"))

    async with sessions() as session:
        with pytest.raises(DomainError):
            await update_reminder_text(session, reminder_id, "Mine now.")
        with pytest.raises(DomainError):
            await reschedule_reminder(session, reminder_id, schedule=schedule, tz=ZoneInfo("UTC"))
        with pytest.raises(DomainError):
            await delete_reminder(session, reminder_id)
