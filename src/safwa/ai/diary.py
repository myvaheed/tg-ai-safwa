"""The Diary subagent: it reads one day and reports it back in the owner's voice."""

from __future__ import annotations

import json
import secrets
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..constants import DIARY_DAY_TOKEN_BUDGET, WEEKDAY_NAMES
from ..domain import diary_entry_for
from ..models import DiaryStamp
from .contracts import DiaryReportInput
from .mini import MiniSessionResult, ReadToolSpec, TerminalTool, Trace, run_mini_session
from .provider import OpenAICompatibleProvider, ProviderToolCall

DIARY_PROMPT = """You keep the owner's Diary. One day, one entry, in the owner's own voice.

Voice: first person, their words, their language. Never "you". No advice, no praise, no
task list. Name people and Safwa items as the owner names them. Write nothing your sources
do not show.

1. Pick the day. Today, unless the request names another one.
2. Read that day from both sources — button work never reaches the conversation, and how the
   day felt never reaches the database.
   - `read_day(date)` — that day's conversation.
   - `query_safwa` — one SELECT over:
     `ai_diary(id, entry_date, body, created_at, updated_at)` — what that day already says;
     `ai_card_events(id, card_id, sprint_id, actor, operation, created_at)` — work done;
     `ai_checks(id, title, repeatable, status, resolved_at, series_id, card_ids)` — what held;
     `ai_cards(id, title, kind, stage, priority, effort_points, parent_id)` — item names.
3. Call `diary_report` once, with `date` and exactly one of:
   - `entry` + `remark` — the whole day: what they did, decided, and carried. Fold in the
     saved entry; yours replaces it, so what you leave out is lost. `remark` is one sentence
     in Safwa's voice, noticing rather than praising.
   - `remove: true` — the owner asked for that day's entry to go.
   - `question` — the one thing that would make the day writable."""

READ_DAY_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "read_day",
        "description": "One day of the owner's conversation with Safwa, oldest first.",
        "parameters": {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "Local date as YYYY-MM-DD. Defaults to today.",
                }
            },
            "required": [],
        },
    },
}
DIARY_REPORT = TerminalTool(
    name="diary_report",
    description="The settled day: its entry and remark, its removal, or the question that blocks it.",
    model=DiaryReportInput,
)


class DayReader(Protocol):
    """The slice of the history source a Diary run needs."""

    async def day_transcript(
        self, chat_id: int, *, start: datetime, end: datetime, token_budget: int
    ) -> str: ...


class DiarySubagent:
    """Reads a day from both sources and reports the change, never applying one."""

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
        today = now.date()

        async def read_day(call: ProviderToolCall) -> dict[str, Any]:
            arguments = json.loads(call.arguments or "{}")
            raw = str(arguments.get("date") or "").strip()
            try:
                day = date.fromisoformat(raw) if raw else today
            except ValueError:
                return {
                    "status": "error",
                    "code": "invalid_arguments",
                    "error": f"{raw!r} is not a calendar date.",
                    "hint": 'Retry read_day with {"date": "YYYY-MM-DD"}, or no arguments.',
                    "retryable": True,
                }
            start, end = self._bounds(day)
            transcript = await self.history.day_transcript(
                self.chat_id, start=start, end=end, token_budget=self.day_token_budget
            )
            return {
                "date": day.isoformat(),
                "conversation": transcript or "The owner said nothing to Safwa that day.",
            }

        context = (
            f"Today is {today.isoformat()} ({WEEKDAY_NAMES[now.weekday()]}), "
            f"local time now {now:%H:%M}, timezone {self.tz.key}\n"
            f"What Safwa asks of you: {request}"
        )
        result = await run_mini_session(
            self.provider,
            system_prompt=DIARY_PROMPT,
            context=context,
            terminals=(DIARY_REPORT,),
            read_tools=(ReadToolSpec(READ_DAY_TOOL, read_day), self.query_tool),
            # The runner's deadline bounds this instead; a call count cannot interrupt
            # a call already in flight.
            max_tool_calls=None,
            trace=trace,
        )
        return await self._report(today, result)

    def _bounds(self, day: date) -> tuple[datetime, datetime]:
        midnight = datetime.combine(day, time.min, tzinfo=self.tz)
        return midnight.astimezone(UTC), (midnight + timedelta(days=1)).astimezone(UTC)

    async def _report(self, today: date, result: MiniSessionResult) -> dict[str, Any]:
        payload = result.payload
        if payload.question:
            return {
                "status": "reported",
                "shape": "question",
                "question": payload.question,
                "next": "Ask the user this in your own words. Write no entry yourself.",
            }
        entry_date = date.fromisoformat(str(payload.date))
        async with self.sessions() as session:
            saved = await diary_entry_for(session, entry_date)
        if payload.remove and saved is None:
            return {
                "status": "reported",
                "shape": "nothing_to_remove",
                "entry_date": entry_date.isoformat(),
                "next": "Tell the user that day has no Diary entry. Change nothing.",
            }
        action = "delete" if payload.remove else ("update" if saved else "create")
        stamp = await self._issue_stamp(
            today,
            entry_date,
            saved.id if saved is not None else None,
            action,
            payload.entry or "",
            payload.remark or "",
        )
        report = {
            "status": "reported",
            "shape": "removal" if payload.remove else "draft",
            "entry_date": entry_date.isoformat(),
            "action": action,
            "stamp": stamp,
            # The body stays host-side: the advisor cannot edit what it never saw.
            "next": (
                "Call propose_diary_update with this stamp in your next response. It "
                "carries the day and the text; you have neither and must not write one."
            ),
        }
        if not payload.remove:
            report["characters"] = len(payload.entry or "")
            report["remark"] = payload.remark or ""
            report["next"] += " Say remark to the user as your own words."
        return report

    async def _issue_stamp(
        self,
        today: date,
        entry_date: date,
        entry_id: int | None,
        action: str,
        body: str,
        remark: str,
    ) -> str:
        # Expires with the issuing day, not the described one; a back-dated entry would
        # otherwise be born expired.
        expires_at = datetime.combine(today + timedelta(days=1), time.min, tzinfo=self.tz)
        stamp = secrets.token_urlsafe(9)
        async with self.sessions() as session:
            session.add(
                DiaryStamp(
                    stamp=stamp,
                    entry_date=entry_date,
                    entry_id=entry_id,
                    action=action,
                    body=body,
                    remark=remark,
                    expires_at=expires_at.astimezone(UTC),
                )
            )
            await session.commit()
        return stamp
