from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

import pytest
from sqlalchemy import select

from safwa.ai.context import SYSTEM_PROMPT
from safwa.ai.contracts import DiaryReportInput, mutation_change_from_tool
from safwa.ai.diary import DIARY_PROMPT, DiarySubagent
from safwa.ai.mini import ReadToolSpec
from safwa.ai.provider import ProviderToolCall, ProviderTurn
from safwa.ai.service import MUTATION_TOOL_DESCRIPTIONS
from safwa.ai.sql import ALLOWED_VIEWS
from safwa.ai.subagents import SubagentRunner
from safwa.domain import (
    DomainError,
    create_diary_entry,
    delete_diary_entry,
    diary_entry_for,
    update_diary_entry,
)
from safwa.models import DiaryEntry, DiaryStamp


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


def test_the_diary_view_is_allowed_and_named_to_both_readers() -> None:
    """A view missing from a prompt is a view that reader can never use."""
    assert "ai_diary" in ALLOWED_VIEWS
    assert "`ai_diary(id, entry_date, body" in SYSTEM_PROMPT
    assert "`ai_diary(id, entry_date, body" in DIARY_PROMPT


def test_the_saving_tool_carries_only_a_stamp() -> None:
    assert "propose_diary_update" in MUTATION_TOOL_DESCRIPTIONS
    change = mutation_change_from_tool("propose_diary_update", {"stamp": "s1"})
    # Date, target and action are filled in from the stamp at preparation.
    assert (change.entity, change.id, change.values) == ("diary", None, {"stamp": "s1"})


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
