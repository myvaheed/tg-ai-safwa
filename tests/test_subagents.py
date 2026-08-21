from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from llm_gateway import ToolCall as ProviderToolCall
from safwa.ai.context import SYSTEM_PROMPT
from safwa.ai.diary import DIARY_PROMPT, day_read_tool, diary_clock
from safwa.ai.service import MUTATION_TOOLS, SAFWA_TOOLS
from safwa.ai.subagents import PERSONA, RoutedSubagent


class StubDayReader:
    def __init__(self, transcript: str) -> None:
        self.transcript = transcript
        self.reads: list[dict[str, Any]] = []

    async def day_transcript(self, chat_id: int, *, start, end, token_budget) -> str:
        self.reads.append({"chat_id": chat_id, "start": start, "end": end})
        return self.transcript


def diary_routed(history: StubDayReader, timezone: str = "Europe/Istanbul") -> RoutedSubagent:
    return RoutedSubagent(
        name="diary",
        purpose="the Diary",
        instructions=DIARY_PROMPT,
        read_tools=(day_read_tool(history, chat_id=42, timezone=timezone),),
        mutation_tools=("diary",),
        clock=lambda: diary_clock(timezone),
    )


def test_the_routing_rules_name_every_subagent_that_can_be_routed_to() -> None:
    """The rules are prose in the prompt, so a subagent they omit is never routed to."""
    rules = SYSTEM_PROMPT.split("# Routing", 1)[1].split("\n# ", 1)[0]
    assert 'route("diary")' in rules


def test_a_routed_prompt_carries_the_one_persona_block() -> None:
    routed = diary_routed(StubDayReader(""))
    assert routed.prompt.startswith(PERSONA)
    assert "You keep the user's Diary" in routed.prompt
    # Voice and citation rules are stated once, where every subagent gets the same copy.
    assert "(card:12)" in PERSONA
    assert "(diary:12)" in PERSONA


def test_the_diary_is_written_only_by_its_subagent() -> None:
    advisor_tools = {tool["function"]["name"] for tool in SAFWA_TOOLS}
    assert "diary" not in advisor_tools
    assert "diary" in MUTATION_TOOLS
    assert diary_routed(StubDayReader("")).mutation_tools == ("diary",)


@pytest.mark.parametrize("timezone", ["Europe/Istanbul", "Pacific/Kiritimati"])
async def test_a_day_is_read_between_its_own_local_midnights(timezone: str) -> None:
    history = StubDayReader("[10:00] [User]: Morning.")
    read_day = day_read_tool(history, chat_id=42, timezone=timezone)
    tz = ZoneInfo(timezone)

    await read_day.run(ProviderToolCall(id="call-1", name="read_day", arguments_json="{}"))

    start: datetime = history.reads[0]["start"]
    end: datetime = history.reads[0]["end"]
    local_start = start.astimezone(tz)
    assert (local_start.hour, local_start.minute) == (0, 0)
    assert (end - start).days == 1
    # No date argument means the subagent's own local day, not the host's.
    assert local_start.date() == datetime.now(tz).date()


async def test_an_unreadable_date_is_repaired_rather_than_read() -> None:
    history = StubDayReader("")
    read_day = day_read_tool(history, chat_id=42)

    result = await read_day.run(
        ProviderToolCall(
            id="call-1", name="read_day", arguments_json=json.dumps({"date": "yesterday"})
        )
    )

    assert result["code"] == "invalid_arguments"
    assert result["retryable"] is True
    assert history.reads == []


def test_the_clock_is_the_only_volatile_line_a_diary_session_gets() -> None:
    clock = diary_clock("Europe/Istanbul")
    today = datetime.now(ZoneInfo("Europe/Istanbul")).date().isoformat()
    assert clock.startswith(f"Today is {today}")
    assert today not in DIARY_PROMPT
