"""What the model may do with a Reminder.

Two things live here because they are one contract: the mutation tool the workspace mutator
calls, and the setup session that turns its free-text ``when`` into schedule parameters.
The model never names a schedule shape — it is derived from which parameters came back.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import Field, PositiveInt

from llm_gateway import LlmProvider
from tg_agent_shell.ai.contracts import NotClearEnoughInput, RecordToolInput, ToolInput
from tg_agent_shell.ai.mini import MINI_SESSION_MAX_TOOL_CALLS, TerminalTool, run_mini_session
from tg_agent_shell.proposals.api import MutationToolSpec, entity_change

from ...constants import WEEKDAY_NAMES
from .schedule import Schedule, ScheduleError, resolve


class ReminderToolInput(RecordToolInput):
    create_requires = ("when",)

    instruction: str = Field(
        description=(
            "What Safwa should do when the time comes, handed to the advisor as a request. "
            "The bounded conversation is available then, but the instruction should remain clear "
            "after time has passed and must name every Safwa item it concerns by #id."
        )
    )
    when: str | None = Field(
        default=None,
        description=(
            "The timing in plain words, e.g. 'every weekday at 8am' or 'in 90 minutes'. "
            "Required to create. Omit it on update to leave the schedule untouched."
        ),
    )


class ReminderConfigInput(ToolInput):
    """The setup session's terminal call: free text resolved into parameters.

    `schedule_kind` is derived from which of these are present, so the model cannot name a
    shape that contradicts its own parameters.
    """

    days: list[Literal["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]] | None = Field(
        default=None, description="Weekdays to fire on; all seven means every day."
    )
    time: str | None = Field(
        default=None,
        description=(
            "Local wall clock HH:MM. With days it is the time it fires at; otherwise it is "
            "when the schedule starts."
        ),
    )
    date: str | None = Field(
        default=None,
        description=(
            "Local calendar date dd.mm.yyyy. Always the START date when the schedule "
            "repeats, and the date itself when it does not. Needs time as well."
        ),
    )
    interval_minutes: PositiveInt | None = Field(
        default=None, description="Repeat every N minutes."
    )
    quiet_windows: list[str] | None = Field(
        default=None,
        description=(
            "Local HH:MM-HH:MM ranges when it must not fire, e.g. ['22:00-09:00']. "
            "Interval schedules only. The end is exclusive."
        ),
    )


REMINDER_TOOL = MutationToolSpec(
    name="reminder",
    input_model=ReminderToolInput,
    description=(
        "Propose one Reminder — instruction text plus timing. The text comes back as a request "
        "when it fires."
    ),
    to_change=entity_change("reminder"),
)

SETUP_PROMPT = """You turn one plain-language timing phrase into schedule parameters.
Call set_reminder_config when the phrase determines a schedule, or not_clear_enough when it does
not. Exactly one call, then stop.

- days + time repeats weekly, interval_minutes repeats by the clock, and date + time alone fires
  once. Each field is described in the tool schema.
- Relative phrases resolve against the current time given below: "in 90 minutes" is a single
  occurrence at that moment, not an interval.
- Never guess. "every morning", "soon", "twice a week", "a few times a day" do not determine a
  schedule — call not_clear_enough with the single question the user must answer."""


async def resolve_schedule(
    provider: LlmProvider,
    *,
    when: str,
    instruction: str,
    now: datetime,
    tz: ZoneInfo,
) -> Schedule:
    """Resolve free-text timing into a Schedule, or raise with the question to ask the owner."""
    local = now.astimezone(tz)
    context = (
        f"Timing phrase: {when}\n"
        f"Reminder text: {instruction}\n"
        f"Current local time: {local:%Y-%m-%d %H:%M} ({WEEKDAY_NAMES[local.weekday()]}), "
        f"timezone {tz.key}"
    )
    result = await run_mini_session(
        provider,
        system_prompt=SETUP_PROMPT,
        context=context,
        terminals=(
            TerminalTool(
                name="set_reminder_config",
                description="The phrase determines a schedule; these are its parameters.",
                model=ReminderConfigInput,
            ),
            TerminalTool(
                name="not_clear_enough",
                description="The phrase does not determine a schedule; ask the user this.",
                model=NotClearEnoughInput,
            ),
        ),
        max_tool_calls=MINI_SESSION_MAX_TOOL_CALLS,
    )
    if result.name == "not_clear_enough":
        raise ScheduleError(result.payload.reason)
    config = result.payload
    return resolve(
        days=config.days,
        clock=config.time,
        day=config.date,
        interval_minutes=config.interval_minutes,
        quiet_windows=config.quiet_windows,
        now=now,
        tz=tz,
    )
