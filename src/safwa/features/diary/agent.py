"""The Diary subagent: it reads one day and settles it, in the owner's voice."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal, Protocol
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator, model_validator

from llm_gateway import ToolCall
from tg_agent_shell.ai.contracts import ToolInput, ToolResultStatus
from tg_agent_shell.ai.mini import ReadToolSpec
from tg_agent_shell.foundation.clock import Clock, SystemClock
from tg_agent_shell.proposals.api import MutationToolSpec, entity_change
from tg_agent_shell.telegram.manifest import AgentContext, AgentSpec

from ...constants import WEEKDAY_NAMES

DIARY_DAY_TOKEN_BUDGET = 12_000


class DiaryToolInput(ToolInput):
    """One day of the Diary: written in the user's voice, or removed."""

    mode: Literal["update", "delete"] = Field(
        description="update writes that day, replacing what is saved; delete removes it."
    )
    date: str = Field(description="The day this settles, as YYYY-MM-DD.")
    pov: str | None = Field(
        default=None,
        description="With update: that whole day in the user's voice. It replaces the saved entry.",
    )
    remark: str | None = Field(
        default=None,
        description="With update: one sentence of your own about the day, addressed to the user.",
    )
    feeling_score: int | None = Field(
        default=None,
        ge=0,
        le=10,
        description=(
            "With update: how the day felt, 0-10. Omit it to keep the score already saved."
        ),
    )

    @field_validator("date")
    @classmethod
    def validate_calendar_date(cls, value: str) -> str:
        try:
            return date.fromisoformat(value.strip()).isoformat()
        except ValueError as error:
            raise ValueError("date must be a calendar date written as YYYY-MM-DD") from error

    @model_validator(mode="after")
    def entry_needs_its_text(self) -> DiaryToolInput:
        if self.mode == "update" and not (self.pov or "").strip():
            raise ValueError("pov is the day itself and is required to write one")
        if self.mode == "delete" and (self.pov or self.feeling_score is not None):
            raise ValueError("A deletion carries only mode and date")
        return self


DIARY_PROMPT = """You keep the user's Diary. One day, one entry, in their own voice.

1. Pick the day: today, unless the user names another.
2. Read it from every source — work done with buttons never reaches the conversation, and how the
   day felt never reaches the database.
   - `read_day(date)` — that day's conversation.
   - `query_safwa` — one read-only SELECT over these views only:
     `ai_diary(id, entry_date, body, feeling_score, created_at, updated_at)` — the saved days;
     `ai_card_events(id, card_id, sprint_id, actor, operation, created_at)` — work done;
     `ai_checks(id, title, repeatable, status, resolved_at, series_id, card_id)` — what held;
     `ai_cards(id, title, kind, stage, priority, effort_points, parent_id)` — item names.
3. Write one line saying what you are about to do.
4. Use the `diary` tool:
   - `diary(mode="update", date=…, pov=…, remark=…, feeling_score=…)` — whether or not that day
     is written already. Fold in the saved entry: your `pov` replaces it, so what you leave out of
     `pov` is lost. `feeling_score` is the one exception — see below.
   - `diary(mode="delete", date=…)` — the user asked for that day to go.
   If your sources do not make the day writable, say in one sentence what is missing instead.

# pov
`pov` is the day itself, and only the user speaks in it: first person, their words, their language.
Never "you". No advice, no praise, no task list. Name people and items as the user names them, and
write nothing your sources do not show.
`remark` is your one line to the user about that day — noticing, not praising.

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
all of how it felt; a score already saved for that day then stays as it is.
Send 0 only when the user asks for it in words. Never choose 0 yourself.
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
    clock: Clock | None = None,
) -> ReadToolSpec:
    """`read_day` bound to one chat: the day as the owner and Safwa actually spoke it."""
    tz = ZoneInfo(timezone)
    current_clock = clock or SystemClock()

    async def read_day(call: ToolCall) -> dict[str, Any]:
        arguments = json.loads(call.arguments_json or "{}")
        raw = str(arguments.get("date") or "").strip()
        try:
            day = date.fromisoformat(raw) if raw else current_clock.now().astimezone(tz).date()
        except ValueError:
            return {
                "status": ToolResultStatus.ERROR.value,
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


def diary_clock(timezone: str, clock: Clock | None = None) -> str:
    """The one volatile line a Diary session needs: which day 'today' is."""
    now = (clock or SystemClock()).now().astimezone(ZoneInfo(timezone))
    return (
        f"Today is {now.date().isoformat()} ({WEEKDAY_NAMES[now.weekday()]}), "
        f"local time now {now:%H:%M}, timezone {timezone}"
    )


def _diary_read_tools(context: AgentContext) -> tuple[ReadToolSpec, ...]:
    return (
        day_read_tool(
            context.history,
            chat_id=context.owner_id,
            timezone=context.timezone,
        ),
    )


def _diary_clock(context: AgentContext) -> Callable[[], str]:
    # Enough to be told what to change about the day it just proposed; the day itself it
    # reads with `read_day`.
    return lambda: diary_clock(context.timezone)


DIARY_AGENT = AgentSpec(
    name="diary",
    purpose="write, rewrite or delete a day.",
    instructions=DIARY_PROMPT,
    mutation_tools=("diary",),
    read_tools=_diary_read_tools,
    clock=_diary_clock,
)

DIARY_TOOL = MutationToolSpec(
    name="diary",
    input_model=DiaryToolInput,
    description="Propose one day of the Diary, written in the user's voice, or remove it.",
    to_change=entity_change("diary"),
)
