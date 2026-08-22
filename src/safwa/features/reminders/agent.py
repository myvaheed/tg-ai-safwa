"""What the model may do with a Reminder.

Two things live here because they are one contract: the mutation tool the board subagent
calls, and the setup session that turns its free-text ``when`` into schedule parameters.
The model never names a schedule shape — it is derived from which parameters came back.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from llm_gateway import LlmProvider

from ...ai.contracts import NotClearEnoughInput, ReminderConfigInput, ReminderToolInput
from ...ai.mini import TerminalTool, run_mini_session
from ...constants import MINI_SESSION_MAX_TOOL_CALLS, WEEKDAY_NAMES
from ..proposals.api import MutationToolSpec, entity_change
from .schedule import Schedule, ScheduleError, resolve

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
