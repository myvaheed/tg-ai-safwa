from __future__ import annotations

import asyncio
import json
from datetime import date, datetime
from typing import Any

import pytest
from sqlalchemy import select

from safwa.ai.context import SYSTEM_PROMPT
from safwa.ai.diary import DiarySubagent
from safwa.ai.mini import MiniSessionError, ReadToolSpec
from safwa.ai.provider import ProviderToolCall, ProviderTurn
from safwa.ai.subagents import SubagentRunner
from safwa.models import AgentRun, AgentStep, DiaryStamp

TODAY = date.today().isoformat()


class ScriptedProvider:
    """One turn per provider call, and a record of the tools each call was offered."""

    def __init__(self, *turns: ProviderTurn) -> None:
        self.turns = list(turns)
        self.offered: list[list[str]] = []

    async def complete_turn(self, _messages, *, tools=None, **_kwargs) -> ProviderTurn:
        self.offered.append(
            [tool["function"]["name"] for tool in tools or []]
        )
        if not self.turns:
            raise AssertionError("The subagent made an unexpected provider call")
        return self.turns.pop(0)


class StallingProvider:
    async def complete_turn(self, _messages, **_kwargs) -> ProviderTurn:
        await asyncio.sleep(30)
        raise AssertionError("unreachable")


class ExplodingProvider:
    async def complete_turn(self, _messages, **_kwargs) -> ProviderTurn:
        raise RuntimeError("the provider is down")


class StubDayReader:
    def __init__(self, transcript: str) -> None:
        self.transcript = transcript
        self.reads: list[dict[str, Any]] = []

    async def day_transcript(self, chat_id: int, *, start, end, token_budget) -> str:
        self.reads.append({"chat_id": chat_id, "start": start, "end": end})
        return self.transcript


def calls(*named: tuple[str, dict[str, Any]]) -> ProviderTurn:
    return ProviderTurn(
        content="",
        tool_calls=tuple(
            ProviderToolCall(id=f"call-{index}", name=name, arguments=json.dumps(arguments))
            for index, (name, arguments) in enumerate(named)
        ),
    )


def stub_query_tool(rows: list[dict[str, Any]]) -> ReadToolSpec:
    async def read(_call: ProviderToolCall) -> list[dict[str, Any]]:
        return rows

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


def diary_subagent(
    sessions,
    provider,
    *,
    transcript: str = "[09:12] [User]: I finished the market run.",
    rows: list[dict[str, Any]] | None = None,
) -> tuple[DiarySubagent, StubDayReader]:
    history = StubDayReader(transcript)
    subagent = DiarySubagent(
        sessions,
        provider,  # type: ignore[arg-type]
        history,
        stub_query_tool(rows if rows is not None else [{"card_id": 3, "operation": "complete"}]),
        chat_id=42,
        timezone="Europe/Istanbul",
    )
    return subagent, history


def runner(sessions, *subagents) -> SubagentRunner:
    return SubagentRunner(
        sessions,
        subagents,
        provider_name="test",
        model_name="test-model",
    )


def test_the_roster_names_every_subagent_that_can_be_called() -> None:
    """The roster is prose in the prompt, so a subagent it omits is never called."""
    roster = SYSTEM_PROMPT.split("# Subagents", 1)[1].split("\n# ", 1)[0]
    assert f"`{DiarySubagent.name}`" in roster


async def test_the_diary_reads_both_sources_and_reports_a_stamped_draft(sessions) -> None:
    provider = ScriptedProvider(
        calls(("read_day", {}), ("query_safwa", {"sql": "SELECT 1 FROM ai_card_events"})),
        calls(("diary_report", {"date": TODAY, "entry": "Сходил на рынок.", "remark": "A steady day."})),
    )
    subagent, history = diary_subagent(sessions, provider)

    result = await runner(sessions, subagent).run("diary", "Write today's entry.")

    assert result.result["shape"] == "draft"
    assert history.reads[0]["chat_id"] == 42
    async with sessions() as session:
        stamp = await session.get(DiaryStamp, result.result["stamp"])
    assert stamp is not None
    # The body stays host-side under the stamp; the advisor is told its size, not its text.
    assert stamp.body == "Сходил на рынок."
    assert stamp.remark == "A steady day."
    assert result.result["characters"] == len("Сходил на рынок.")
    assert "Сходил" not in json.dumps(result.result, ensure_ascii=False)


async def test_a_stamp_expires_at_the_end_of_its_own_local_day(sessions) -> None:
    provider = ScriptedProvider(calls(("diary_report", {"date": TODAY, "entry": "A quiet day.", "remark": "—"})))
    subagent, _ = diary_subagent(sessions, provider)

    result = await runner(sessions, subagent).run("diary", "Write today's entry.")

    async with sessions() as session:
        stamp = await session.get(DiaryStamp, result.result["stamp"])
    assert stamp is not None
    local_end = stamp.expires_at.astimezone(subagent.tz)
    assert (local_end.hour, local_end.minute) == (0, 0)
    assert (local_end.date() - stamp.entry_date).days == 1


async def test_nothing_to_write_carries_a_question_and_issues_no_stamp(sessions) -> None:
    provider = ScriptedProvider(
        calls(("diary_report", {"question": "What did today actually turn on?"}))
    )
    subagent, _ = diary_subagent(sessions, provider, transcript="")

    result = await runner(sessions, subagent).run("diary", "Write today's entry.")

    assert result.result["shape"] == "question"
    assert result.result["question"] == "What did today actually turn on?"
    assert "stamp" not in result.result
    async with sessions() as session:
        assert list(await session.scalars(select(DiaryStamp))) == []


async def test_a_report_carrying_both_shapes_is_repaired_rather_than_accepted(sessions) -> None:
    provider = ScriptedProvider(
        calls(("diary_report", {"date": TODAY, "entry": "A day.", "question": "Which day?"})),
        calls(("diary_report", {"date": TODAY, "entry": "A day.", "remark": "Short."})),
    )
    subagent, _ = diary_subagent(sessions, provider)

    result = await runner(sessions, subagent).run("diary", "Write today's entry.")

    assert result.result["shape"] == "draft"
    assert provider.turns == []


async def test_a_subagent_is_never_offered_a_mutation_or_another_subagent(sessions) -> None:
    provider = ScriptedProvider(calls(("diary_report", {"date": TODAY, "entry": "A day.", "remark": "Short."})))
    subagent, _ = diary_subagent(sessions, provider)

    await runner(sessions, subagent).run("diary", "Write today's entry.")

    assert provider.offered[0] == ["read_day", "query_safwa", "diary_report"]


async def test_the_run_is_traced_as_its_own_agent_run(sessions) -> None:
    provider = ScriptedProvider(
        calls(("read_day", {})),
        calls(("diary_report", {"date": TODAY, "entry": "A day.", "remark": "Short."})),
    )
    subagent, _ = diary_subagent(sessions, provider)

    result = await runner(sessions, subagent).run("diary", "Write today's entry.")

    async with sessions() as session:
        run = await session.get(AgentRun, result.run_id)
        steps = list(
            await session.scalars(
                select(AgentStep).where(AgentStep.run_id == result.run_id).order_by(AgentStep.position)
            )
        )
    assert run is not None
    assert (run.status, run.error_code) == ("completed", None)
    assert run.duration_ms is not None
    assert [step.kind for step in steps] == ["subagent_read", "subagent_terminal"]
    assert steps[0].metadata_json["subagent"] == "diary"


async def test_a_hanging_subagent_is_cut_off_at_its_deadline(sessions) -> None:
    subagent, _ = diary_subagent(sessions, StallingProvider())
    stopwatch = SubagentRunner(
        sessions,
        (subagent,),
        provider_name="test",
        model_name="test-model",
        deadline_seconds=0.05,
    )

    result = await stopwatch.run("diary", "Write today's entry.")

    assert result.result["code"] == "subagent_timeout"
    assert result.result["retryable"] is False
    async with sessions() as session:
        run = await session.get(AgentRun, result.run_id)
    assert run is not None
    assert (run.status, run.error_code) == ("failed", "timeout")


async def test_a_subagent_that_never_reports_fails_without_raising(sessions) -> None:
    provider = ScriptedProvider(*[ProviderTurn(content="Here is the day.")] * 5)
    subagent, _ = diary_subagent(sessions, provider)

    result = await runner(sessions, subagent).run("diary", "Write today's entry.")

    assert result.result["code"] == "subagent_failed"
    async with sessions() as session:
        run = await session.get(AgentRun, result.run_id)
    assert run is not None
    assert (run.status, run.error_code) == ("failed", MiniSessionError.__name__)


async def test_an_unexpected_failure_is_reported_rather_than_ending_the_turn(sessions) -> None:
    subagent, _ = diary_subagent(sessions, ExplodingProvider())

    result = await runner(sessions, subagent).run("diary", "Write today's entry.")

    assert result.result["code"] == "subagent_failed"
    assert "the provider is down" in result.result["error"]
    async with sessions() as session:
        run = await session.get(AgentRun, result.run_id)
    assert run is not None
    assert (run.status, run.error_code) == ("failed", "RuntimeError")


async def test_an_unknown_name_is_retryable_and_starts_no_run(sessions) -> None:
    subagent, _ = diary_subagent(sessions, ScriptedProvider())

    result = await runner(sessions, subagent).run("journal", "Write today's entry.")

    assert result.result["code"] == "unknown_subagent"
    assert result.result["retryable"] is True
    assert "diary" in result.result["hint"]
    assert result.run_id is None
    async with sessions() as session:
        assert list(await session.scalars(select(AgentRun))) == []


@pytest.mark.parametrize("timezone", ["Europe/Istanbul", "Pacific/Kiritimati"])
async def test_a_day_is_read_between_its_own_local_midnights(
    sessions, timezone: str
) -> None:
    provider = ScriptedProvider(
        calls(("read_day", {})),
        calls(("diary_report", {"date": TODAY, "entry": "A day.", "remark": "Short."})),
    )
    history = StubDayReader("[10:00] [User]: Morning.")
    subagent = DiarySubagent(
        sessions,
        provider,  # type: ignore[arg-type]
        history,
        stub_query_tool([]),
        chat_id=42,
        timezone=timezone,
    )

    await runner(sessions, subagent).run("diary", "Write today's entry.")

    start: datetime = history.reads[0]["start"]
    end: datetime = history.reads[0]["end"]
    local_start = start.astimezone(subagent.tz)
    assert (local_start.hour, local_start.minute) == (0, 0)
    assert (end - start).days == 1
    # No date argument means the subagent's own local day, not the host's.
    assert local_start.date() == datetime.now(subagent.tz).date()
