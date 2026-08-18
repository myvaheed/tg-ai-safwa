"""The Diary subagent: it reads one day and settles it, in the owner's voice."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from ..constants import DIARY_DAY_TOKEN_BUDGET, WEEKDAY_NAMES
from .mini import ReadToolSpec
from .provider import ProviderToolCall

DIARY_PROMPT = """You keep the user's Diary. One day, one entry, in their own voice.

1. Pick the day: today, unless the user names another.
2. Read it from every source — work done with buttons never reaches the conversation, and how the
   day felt never reaches the database.
   - `read_day(date)` — that day's conversation.
   - `query_safwa` — one read-only SELECT over these views only:
     `ai_diary(id, entry_date, body, feeling_score, created_at, updated_at)` — the saved days;
     `ai_card_events(id, card_id, sprint_id, actor, operation, created_at)` — work done;
     `ai_checks(id, title, repeatable, status, resolved_at, series_id, card_ids)` — what held;
     `ai_cards(id, title, kind, stage, priority, effort_points, parent_id)` — item names.
3. Then call the tool once:
   - `diary(mode="update", date=…, pov=…, ai_comment=…, feeling_score=…)` — whether or not that day
     is written already. Fold in the saved entry: yours replaces it, so what you leave out is lost.
   - `diary(mode="delete", date=…)` — the user asked for that day to go.
   If your sources do not make the day writable, say in one sentence what is missing instead.

# pov
`pov` is the day itself, and only the user speaks in it: first person, their words, their language.
Never "you". No advice, no praise, no task list. Name people and items as the user names them, and
write nothing your sources do not show.
`ai_comment` is your one line to the user about that day — noticing, not praising.

# feeling_score
0-10: how the day felt to *them*, read from what they said about it, not from how much they
finished. Pick the band first, then the number inside it.
- 1 the worst day this month. Distressed, no way through, and they say so plainly.
- 2 several things went wrong. Angry, grieving, or worn down by the end.
- 3 one thing went clearly wrong and coloured the rest of the day.
- 4 nothing broke, but they were tired, flat, or unsure of themselves.
- 5 an ordinary day. Work went on, the mood was even, nothing stood out.
- 6 a little lighter than usual. One small thing pleased them.
- 7 one clear good thing, and they were glad of it.
- 8 several good things, or one they had been waiting for.
- 9 excited, proud, or moved. They call the day great.
- 10 one of the best days of their life. Probably a day they will never forget.
When two numbers both fit, take the one nearer 5. Omit `feeling_score` when the day left no sign at
all of how it felt. Send 0 only when the user asks for it in words. Never choose 0 yourself.
"""

READ_DAY_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "read_day",
        "description": "One day of the user's conversation with Safwa, oldest first.",
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


class DayReader(Protocol):
    """The slice of the history source a Diary run needs."""

    async def day_transcript(
        self, chat_id: int, *, start: datetime, end: datetime, token_budget: int
    ) -> str: ...


def day_read_tool(
    history: DayReader,
    *,
    chat_id: int,
    timezone: str = "UTC",
    day_token_budget: int = DIARY_DAY_TOKEN_BUDGET,
) -> ReadToolSpec:
    """`read_day` bound to one chat: the day as the owner and Safwa actually spoke it."""
    tz = ZoneInfo(timezone)

    async def read_day(call: ProviderToolCall) -> dict[str, Any]:
        arguments = json.loads(call.arguments or "{}")
        raw = str(arguments.get("date") or "").strip()
        try:
            day = date.fromisoformat(raw) if raw else datetime.now(tz).date()
        except ValueError:
            return {
                "status": "error",
                "code": "invalid_arguments",
                "error": f"{raw!r} is not a calendar date.",
                "hint": 'Retry read_day with {"date": "YYYY-MM-DD"}, or no arguments.',
                "retryable": True,
            }
        midnight = datetime.combine(day, time.min, tzinfo=tz)
        transcript = await history.day_transcript(
            chat_id,
            start=midnight.astimezone(UTC),
            end=(midnight + timedelta(days=1)).astimezone(UTC),
            token_budget=day_token_budget,
        )
        return {
            "date": day.isoformat(),
            "conversation": transcript or "The user said nothing to Safwa that day.",
        }

    return ReadToolSpec(READ_DAY_TOOL, read_day)


def diary_clock(timezone: str) -> str:
    """The one volatile line a Diary session needs: which day 'today' is."""
    now = datetime.now(ZoneInfo(timezone))
    return (
        f"Today is {now.date().isoformat()} ({WEEKDAY_NAMES[now.weekday()]}), "
        f"local time now {now:%H:%M}, timezone {timezone}"
    )
