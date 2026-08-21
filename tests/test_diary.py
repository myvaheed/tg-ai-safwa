from __future__ import annotations

from datetime import UTC, date, datetime, time
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from safwa.ai.prepare import ChangePreparer
from safwa.bootstrap.modules import ALLOWED_VIEWS, PROPOSALS, SYSTEM_PROMPT
from safwa.constants import DIARY_TIME_DEFAULT
from safwa.domain import (
    DIARY_REMINDER_INSTRUCTION,
    delete_reminder,
    reschedule_reminder,
    sync_diary_reminder,
    update_profile,
    update_reminder_text,
)
from safwa.enums import ScheduleKind
from safwa.features.diary.agent import DIARY_PROMPT, DiaryToolInput
from safwa.features.diary.model import DiaryEntry
from safwa.features.diary.telegram import (
    FEELING_SCORE_EMOJI,
    DiaryProposalPresenter,
    diary_label,
)
from safwa.features.diary.use_cases import (
    create_diary_entry,
    delete_diary_entry,
    diary_entry_for,
    update_diary_entry,
)
from safwa.features.proposals.api import ToolPreparationError
from safwa.foundation.errors import DomainError
from safwa.models import Reminder
from safwa.reminders import resolve, schedule_of


def preparer() -> ChangePreparer:
    """A Diary change needs neither the provider nor a query runner to prepare."""
    return ChangePreparer(None, None, PROPOSALS)  # type: ignore[arg-type]


async def prepared(sessions, tool_arguments: dict[str, Any]) -> Any:
    change = PROPOSALS.change_from_tool("diary", tool_arguments)
    async with sessions() as session:
        result = await preparer().prepare(session, change)
    return change, result


def test_di_read_006_both_readers_know_the_diary_view() -> None:
    """DI-READ-006 — tests/brd/diary.feature"""
    assert "ai_diary" in ALLOWED_VIEWS
    assert "`ai_diary(id, entry_date, body, feeling_score" in DIARY_PROMPT
    assert "ai_diary(id, entry_date, body, feeling_score" in SYSTEM_PROMPT
    assert "(diary:12)" in SYSTEM_PROMPT


def test_di_write_008_write_and_delete_inputs_are_distinct() -> None:
    """DI-WRITE-008 — tests/brd/diary.feature"""
    with pytest.raises(ValueError):
        DiaryToolInput.model_validate({"mode": "update", "date": "2026-08-15"})
    with pytest.raises(ValueError):
        DiaryToolInput.model_validate({"mode": "delete", "date": "2026-08-15", "pov": "День."})
    with pytest.raises(ValueError):
        DiaryToolInput.model_validate({"mode": "update", "date": "15.08.2026", "pov": "День."})
    assert DiaryToolInput.model_validate({"mode": "delete", "date": "2026-08-15"}).pov is None


def test_di_mood_004_score_is_optional_and_bounded() -> None:
    """DI-MOOD-004 — tests/brd/diary.feature"""
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


def test_di_mood_004_zero_requires_owner_words() -> None:
    """DI-MOOD-004 — tests/brd/diary.feature"""
    assert set(FEELING_SCORE_EMOJI) == set(range(11))
    assert "Never choose 0 yourself." in DIARY_PROMPT


def test_di_link_007_heading_shows_optional_mood() -> None:
    """DI-LINK-007 — tests/brd/diary.feature"""
    assert diary_label(date(2026, 3, 4), 6) == "4 марта · 🙂6"
    assert diary_label(date(2026, 3, 4), None) == "4 марта"


def test_di_write_008_update_action_is_resolved_from_live_day() -> None:
    """DI-WRITE-008 — tests/brd/diary.feature"""
    change = PROPOSALS.change_from_tool(
        "diary",
        {"mode": "update", "date": "2026-08-15", "pov": "День.", "ai_comment": "Held."},
    )
    assert (change.entity, change.action, change.id) == ("diary", "update", None)
    assert "mode" not in change.values


async def test_di_day_001_missing_day_is_created(sessions) -> None:
    """DI-DAY-001 — tests/brd/diary.feature"""
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


async def test_di_day_002_existing_day_is_replaced(sessions) -> None:
    """DI-DAY-002 — tests/brd/diary.feature"""
    today = date.today()
    async with sessions() as session:
        entry = await create_diary_entry(session, entry_date=today, body="Уже записано.")
        await session.commit()

    change, result = await prepared(
        sessions, {"mode": "update", "date": today.isoformat(), "pov": "Переписал."}
    )
    assert (change.action, change.id) == ("update", entry.id)
    assert result.expected_version == entry.version


async def test_di_delete_005_missing_day_is_refused(sessions) -> None:
    """DI-DELETE-005 — tests/brd/diary.feature"""
    today = date.today()
    with pytest.raises(ToolPreparationError) as refused:
        await prepared(sessions, {"mode": "delete", "date": today.isoformat()})
    assert refused.value.code == "target_not_found"


async def test_di_delete_005_existing_day_is_targeted(sessions) -> None:
    """DI-DELETE-005 — tests/brd/diary.feature"""
    today = date.today()
    async with sessions() as session:
        entry = await create_diary_entry(session, entry_date=today, body="Есть что удалять.")
        await session.commit()

    change, result = await prepared(sessions, {"mode": "delete", "date": today.isoformat()})
    assert (change.action, change.id) == ("delete", entry.id)
    assert result.values == {"entry_date": today.isoformat()}


async def test_di_receipt_009_result_omits_day_text(sessions) -> None:
    """DI-RECEIPT-009 — tests/brd/diary.feature"""
    change = SimpleNamespace(
        action="create",
        values={
            "entry_date": "2026-08-16",
            "body": "День.",
            "feeling_score": 7,
        },
    )
    presenter = DiaryProposalPresenter()
    async with sessions() as session:
        details = await presenter.details(session, change, None)  # type: ignore[arg-type]
        summary = await presenter.summary(session, change, details)  # type: ignore[arg-type]
    assert details == ["Date: 2026-08-16", "Entry: 5 characters", "Feeling: 7"]
    assert summary == "New Diary entry for 2026-08-16 with feeling score 7"
    assert "День" not in repr((details, summary))


async def test_di_day_002_later_write_replaces_the_single_entry(sessions) -> None:
    """DI-DAY-002 — tests/brd/diary.feature"""
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


async def test_di_delete_005_delete_removes_the_existing_entry(sessions) -> None:
    """DI-DELETE-005 — tests/brd/diary.feature"""
    async with sessions() as session:
        entry = await create_diary_entry(
            session, entry_date=date(2026, 8, 15), body="Запись на удаление."
        )
        await session.commit()
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
