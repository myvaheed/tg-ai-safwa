"""The Diary subagent: it reads one day and reports it back in the owner's voice."""

from __future__ import annotations

import secrets
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..constants import DIARY_DAY_TOKEN_BUDGET, WEEKDAY_NAMES
from ..models import DiaryStamp
from .contracts import DiaryReportInput
from .mini import MiniSessionResult, ReadToolSpec, TerminalTool, Trace, run_mini_session
from .provider import OpenAICompatibleProvider, ProviderToolCall

DIARY_PROMPT = """You keep the owner's Diary. You write one day's entry, and nothing else.

The entry is the owner's own voice: first person, their words, their register, what they
would have written themselves. It is not a report about them and never addresses them as
"you". Write it in the language the owner writes their own messages in, whatever language
this instruction happens to be in.

Read before you write, and read both sources. `read_day` returns the day's conversation.
`query_safwa` returns what actually happened in the plan — `ai_card_events` for work
finished, moved, or dropped, and `ai_checks.resolved_at` for what held and what did not.
Neither is the day on its own: work done from the buttons never reaches the conversation,
and what the day felt like never reaches the database.

The entry carries what the day held and what the owner made of it — what they did, what
came of it, what they decided, what weighed on them. Not a task list, not advice, not
encouragement. Keep people and Safwa items named as the owner named them, and never write
a fact neither source shows.

Call diary_report exactly once, and stop:
- `entry` is the whole day as it now stands. When the day already has a saved entry you are
  given it: keep what is still true and fold the rest in. Your entry replaces it, so
  anything you leave out is lost.
- `remark` is one sentence in Safwa's own voice about the day — noticing, not praising.
- `question` replaces both when the day holds nothing to write yet: the one thing that would
  make it writable. Never send it beside an entry."""

READ_DAY_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "read_day",
        "description": (
            "The owner's conversation with Safwa for this day, oldest first. Takes no "
            "arguments."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}
DIARY_REPORT = TerminalTool(
    name="diary_report",
    description="The finished day: its entry and a remark, or the question that blocks it.",
    model=DiaryReportInput,
)


class DayReader(Protocol):
    """The slice of the history source a Diary run needs."""

    async def day_transcript(
        self, chat_id: int, *, start: datetime, token_budget: int
    ) -> str: ...


class DiarySubagent:
    """Reads the day from both sources and reports a draft, never a change."""

    name = "diary"

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        provider: OpenAICompatibleProvider,
        history: DayReader,
        query_tool: ReadToolSpec,
        *,
        chat_id: int,
        timezone: str = "UTC",
        day_token_budget: int = DIARY_DAY_TOKEN_BUDGET,
    ) -> None:
        self.sessions = sessions
        self.provider = provider
        self.history = history
        self.query_tool = query_tool
        self.chat_id = chat_id
        self.tz = ZoneInfo(timezone)
        self.day_token_budget = day_token_budget

    async def run(self, request: str, *, trace: Trace) -> dict[str, Any]:
        now = datetime.now(self.tz)
        entry_date = now.date()
        start = datetime.combine(entry_date, time.min, tzinfo=self.tz).astimezone(UTC)

        async def read_day(_call: ProviderToolCall) -> dict[str, Any]:
            transcript = await self.history.day_transcript(
                self.chat_id, start=start, token_budget=self.day_token_budget
            )
            return {
                "date": entry_date.isoformat(),
                "conversation": transcript or "The owner said nothing to Safwa today.",
            }

        context = (
            f"Date: {entry_date.isoformat()} ({WEEKDAY_NAMES[now.weekday()]}), "
            f"local time now {now:%H:%M}, timezone {self.tz.key}\n"
            f"Entry already saved for this date: none.\n"
            f"What Safwa asks of you: {request}"
        )
        result = await run_mini_session(
            self.provider,
            system_prompt=DIARY_PROMPT,
            context=context,
            terminals=(DIARY_REPORT,),
            read_tools=(ReadToolSpec(READ_DAY_TOOL, read_day), self.query_tool),
            # A wall-clock deadline in the runner bounds the run instead; a read loop
            # that stalls is cut off by the clock, which a call count cannot do.
            max_tool_calls=None,
            trace=trace,
        )
        return await self._report(entry_date, result)

    async def _report(self, entry_date: date, result: MiniSessionResult) -> dict[str, Any]:
        payload = result.payload
        if not payload.entry:
            return {
                "status": "reported",
                "shape": "question",
                "entry_date": entry_date.isoformat(),
                "question": payload.question,
                "next": (
                    "Put this question to the owner in your own words, and write no entry "
                    "yourself."
                ),
            }
        stamp = await self._issue_stamp(entry_date, payload.entry, payload.remark or "")
        return {
            "status": "reported",
            "shape": "draft",
            "entry_date": entry_date.isoformat(),
            "stamp": stamp,
            "characters": len(payload.entry),
            "remark": payload.remark or "",
            # The text stays host-side on purpose: passing it through the advisor costs
            # tokens and invites the silent edits the Diary rules forbid.
            "next": (
                "The entry is held under this stamp until the end of the day; you were not "
                "given its text and must not write your own. Tell the owner it is ready, "
                "using remark as your own words about the day."
            ),
        }

    async def _issue_stamp(self, entry_date: date, body: str, remark: str) -> str:
        expires_at = datetime.combine(
            entry_date + timedelta(days=1), time.min, tzinfo=self.tz
        )
        stamp = secrets.token_urlsafe(9)
        async with self.sessions() as session:
            session.add(
                DiaryStamp(
                    stamp=stamp,
                    entry_date=entry_date,
                    body=body,
                    remark=remark,
                    expires_at=expires_at.astimezone(UTC),
                )
            )
            await session.commit()
        return stamp
