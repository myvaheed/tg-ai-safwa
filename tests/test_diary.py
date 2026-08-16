from __future__ import annotations

import json
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from safwa.ai.context import SYSTEM_PROMPT
from safwa.ai.contracts import DiaryReportInput, mutation_change_from_tool
from safwa.ai.diary import DIARY_PROMPT, DiarySubagent
from safwa.ai.mini import ReadToolSpec
from safwa.ai.provider import ProviderToolCall, ProviderTurn
from safwa.ai.service import MUTATION_TOOL_DESCRIPTIONS, _approval_results_summary
from safwa.ai.sql import ALLOWED_VIEWS
from safwa.ai.subagents import SubagentRunner
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
from safwa.models import DiaryEntry, DiaryStamp, Reminder
from safwa.reminders import resolve, schedule_of
from safwa.telegram._presentation import diary_label


class RecordingProvider:
    """One turn per call, keeping the context each call was given."""

    def __init__(self, *turns: ProviderTurn) -> None:
        self.turns = list(turns)
        self.seen: list[list[dict[str, Any]]] = []
        self.offered: list[list[str]] = []

    async def complete_turn(self, messages, *, tools=None, **_kwargs) -> ProviderTurn:
        self.seen.append([dict(message) for message in messages])
        self.offered.append([tool["function"]["name"] for tool in tools or []])
        if not self.turns:
            raise AssertionError("The subagent made an unexpected provider call")
        return self.turns.pop(0)


class StubDayReader:
    def __init__(self) -> None:
        self.days: list[tuple[Any, Any]] = []

    async def day_transcript(self, _chat_id: int, *, start, end, token_budget) -> str:  # noqa: ARG002
        self.days.append((start, end))
        return "[09:12] [User]: Утро прошло спокойно."


def turn(name: str, **arguments: Any) -> ProviderTurn:
    return ProviderTurn(
        content="",
        tool_calls=(
            ProviderToolCall(id="call-1", name=name, arguments=json.dumps(arguments)),
        ),
    )


def query_tool_stub() -> ReadToolSpec:
    async def read(_call: ProviderToolCall) -> list[dict[str, Any]]:
        return []

    return ReadToolSpec(
        {
            "type": "function",
            "function": {
                "name": "query_safwa",
                "description": "stub",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        read,
    )


def diary_subagent(sessions, provider: RecordingProvider) -> tuple[DiarySubagent, StubDayReader]:
    history = StubDayReader()
    subagent = DiarySubagent(
        sessions,
        provider,  # type: ignore[arg-type]
        history,
        query_tool_stub(),
        chat_id=42,
        timezone="Europe/Istanbul",
    )
    return subagent, history


async def run_diary(sessions, provider: RecordingProvider, request: str = "Write today.") -> Any:
    subagent, _ = diary_subagent(sessions, provider)
    outcome = await SubagentRunner(
        sessions, (subagent,), provider_name="test", model_name="test-model"
    ).run("diary", request)
    return outcome.result


def test_only_the_subagent_is_told_about_the_diary_view() -> None:
    """A view missing from a prompt is a view that reader can never use."""
    assert "ai_diary" in ALLOWED_VIEWS
    assert "`ai_diary(id, entry_date, body, feeling_score" in DIARY_PROMPT
    # The advisor reaches the Diary only through the subagent, so it is not named to it.
    assert "ai_diary" not in SYSTEM_PROMPT


def test_the_saving_tool_carries_only_a_stamp() -> None:
    # The runtime sends the stamp, so the model is never offered the tool.
    assert "propose_diary_update" not in MUTATION_TOOL_DESCRIPTIONS
    change = mutation_change_from_tool("propose_diary_update", {"stamp": "s1"})
    # Date, target and action are filled in from the stamp at preparation.
    assert (change.entity, change.id, change.values) == ("diary", None, {"stamp": "s1"})


def test_only_an_unsaved_diary_receipt_points_back_at_its_draft() -> None:
    tool: dict[str, Any] = {
        "change": {"entity": "diary", "action": "create", "values": {"stamp": "s1"}},
        "display": "New Diary entry for 2026-08-16",
        "details": ["Date: 2026-08-16", "Entry: 5 characters", "Draft: s1"],
        "target": {"type": "proposal", "id": 1},
    }
    discarded = _approval_results_summary(
        [{**tool, "result": {"status": "discarded"}}], for_display=True
    )
    saved = _approval_results_summary(
        [{**tool, "result": {"status": "approved"}}], for_display=True
    )

    # The draft is still readable through its stamp, so the conversation keeps the way back.
    assert discarded == "🗑 Discarded — New Diary entry for 2026-08-16 · Draft: s1"
    # Saving spends every stamp for that day; naming one would send the subagent to a dead draft.
    assert saved == "✅ Saved — New Diary entry for 2026-08-16"


def test_a_report_names_its_day_and_exactly_one_outcome() -> None:
    with pytest.raises(ValueError):
        DiaryReportInput.model_validate({"entry": "День."})
    with pytest.raises(ValueError):
        DiaryReportInput.model_validate({"date": "2026-08-15"})
    with pytest.raises(ValueError):
        DiaryReportInput.model_validate(
            {"date": "2026-08-15", "entry": "День.", "remove": True}
        )
    with pytest.raises(ValueError):
        DiaryReportInput.model_validate({"date": "15.08.2026", "entry": "День."})
    assert DiaryReportInput.model_validate({"question": "Which day?"}).date is None
    assert DiaryReportInput.model_validate({"date": "2026-08-15", "remove": True}).remove
    # Reading the Diary needs no day and settles nothing.
    assert DiaryReportInput.model_validate({"answer": "[04.03.2026](diary:3)"}).date is None


def test_a_feeling_score_belongs_to_an_entry_and_runs_from_zero_to_ten() -> None:
    assert DiaryReportInput.model_validate(
        {"date": "2026-08-15", "entry": "День.", "feeling_score": 0}
    ).feeling_score == 0
    for out_of_scale in (-1, 11):
        with pytest.raises(ValueError):
            DiaryReportInput.model_validate(
                {"date": "2026-08-15", "entry": "День.", "feeling_score": out_of_scale}
            )
    with pytest.raises(ValueError):
        DiaryReportInput.model_validate({"date": "2026-08-15", "remove": True, "feeling_score": 7})


def test_the_scale_is_stated_once_and_the_model_never_reaches_for_zero() -> None:
    assert set(FEELING_SCORE_EMOJI) == set(range(11))
    assert "Never choose 0 yourself." in DIARY_PROMPT
    assert diary_label(date(2026, 3, 4), 6) == "4 марта · 🙂6"
    # A day that said nothing about how it felt is named by its date alone.
    assert diary_label(date(2026, 3, 4), None) == "4 марта"


async def test_a_day_holds_one_entry_and_a_later_draft_replaces_it(sessions) -> None:
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


async def test_a_back_dated_report_targets_that_day_and_reads_only_it(sessions) -> None:
    yesterday = date.today() - timedelta(days=1)
    async with sessions() as session:
        saved = await create_diary_entry(
            session, entry_date=yesterday, body="Вчера было тихо."
        )
        await session.commit()
    provider = RecordingProvider(
        turn("read_day", date=yesterday.isoformat()),
        turn("diary_report", date=yesterday.isoformat(), entry="Вчера и вечер.", remark="Fuller."),
    )
    subagent, history = diary_subagent(sessions, provider)

    outcome = await SubagentRunner(
        sessions, (subagent,), provider_name="test", model_name="test-model"
    ).run("diary", "Допиши это во вчерашний день.")
    result = outcome.result

    assert (result["shape"], result["action"]) == ("draft", "update")
    assert result["entry_date"] == yesterday.isoformat()
    # The reader is closed at both ends, so today's conversation cannot leak into it.
    start, end = history.days[0]
    assert (end - start).days == 1
    assert start.astimezone(subagent.tz).date() == yesterday
    async with sessions() as session:
        stamp = await session.get(DiaryStamp, result["stamp"])
    assert stamp is not None
    assert (stamp.entry_date, stamp.entry_id, stamp.action) == (yesterday, saved.id, "update")
    # It expires with the day it was issued on, not the day it describes.
    assert stamp.expires_at.astimezone(subagent.tz).date() == date.today() + timedelta(days=1)


async def test_a_first_entry_for_a_day_is_reported_as_a_create(sessions) -> None:
    today = date.today()
    provider = RecordingProvider(
        turn("diary_report", date=today.isoformat(), entry="Первый день.", remark="A start.")
    )

    result = await run_diary(sessions, provider)

    assert result["action"] == "create"
    assert "propose_diary_update" not in provider.offered[0]
    async with sessions() as session:
        stamp = await session.get(DiaryStamp, result["stamp"])
    assert stamp is not None
    assert stamp.entry_id is None


async def test_a_removal_is_reported_only_when_that_day_has_an_entry(sessions) -> None:
    today = date.today()
    empty = RecordingProvider(turn("diary_report", date=today.isoformat(), remove=True))

    refused = await run_diary(sessions, empty, "Удали сегодняшнюю запись.")

    assert refused["shape"] == "nothing_to_remove"
    assert "stamp" not in refused
    async with sessions() as session:
        entry = await create_diary_entry(session, entry_date=today, body="Есть что удалять.")
        await session.commit()

    provider = RecordingProvider(turn("diary_report", date=today.isoformat(), remove=True))
    result = await run_diary(sessions, provider, "Удали сегодняшнюю запись.")

    assert (result["shape"], result["action"]) == ("removal", "delete")
    assert "remark" not in result
    async with sessions() as session:
        stamp = await session.get(DiaryStamp, result["stamp"])
    assert stamp is not None
    assert (stamp.entry_id, stamp.body) == (entry.id, "")


async def test_a_question_carries_no_stamp_and_no_date(sessions) -> None:
    provider = RecordingProvider(turn("diary_report", question="What did today turn on?"))

    result = await run_diary(sessions, provider)

    assert result["shape"] == "question"
    assert "stamp" not in result
    async with sessions() as session:
        assert list(await session.scalars(select(DiaryStamp))) == []


async def test_a_reading_request_answers_with_citations_and_settles_nothing(sessions) -> None:
    provider = RecordingProvider(
        turn("diary_report", answer="Тот день был тяжёлым: [04.03.2026](diary:3).")
    )

    result = await run_diary(sessions, provider, "Что я писал четвёртого марта?")

    assert result["shape"] == "answer"
    assert result["answer"] == "Тот день был тяжёлым: [04.03.2026](diary:3)."
    assert "stamp" not in result
    async with sessions() as session:
        assert list(await session.scalars(select(DiaryStamp))) == []


async def test_a_draft_carries_its_score_and_reads_back_from_its_stamp(sessions) -> None:
    today = date.today()
    provider = RecordingProvider(
        turn(
            "diary_report",
            date=today.isoformat(),
            entry="Хороший день.",
            feeling_score=8,
            remark="Something held.",
        )
    )

    result = await run_diary(sessions, provider)

    # The advisor is told the score but never the text.
    assert result["feeling_score"] == 8
    assert "Хороший день." not in json.dumps(result, ensure_ascii=False)
    async with sessions() as session:
        stamp = await session.get(DiaryStamp, result["stamp"])
    assert stamp is not None and stamp.feeling_score == 8

    subagent, _ = diary_subagent(sessions, RecordingProvider())
    observed = await subagent._observe_stamp(
        ProviderToolCall(id="c", name="observe_stamp", arguments=json.dumps({"stamp": stamp.stamp}))
    )
    assert (observed["entry"], observed["feeling_score"]) == ("Хороший день.", 8)

    # A spent stamp sends the reader to the saved day instead.
    missing = await subagent._observe_stamp(
        ProviderToolCall(id="c", name="observe_stamp", arguments=json.dumps({"stamp": "gone"}))
    )
    assert (missing["code"], missing["retryable"]) == ("stamp_not_found", True)


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
