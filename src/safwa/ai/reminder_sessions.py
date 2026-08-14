"""The Reminder setup mini-session: free-text timing into schedule parameters."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from ..constants import MINI_SESSION_MAX_TOOL_CALLS, WEEKDAY_NAMES
from ..reminders import Schedule, ScheduleError, resolve
from .contracts import NotClearEnoughInput, ReminderConfigInput
from .mini import TerminalTool, run_mini_session
from .provider import OpenAICompatibleProvider

SETUP_PROMPT = """You turn one plain-language timing phrase into schedule parameters.

Call set_reminder_config when the phrase determines a schedule, or not_clear_enough when it
does not. Exactly one call, then stop.

What the parameters mean:
- interval_minutes repeats every N minutes.
- days + time repeats at that local wall clock on those weekdays; all seven means every day.
- date is ALWAYS a start date, never a fire time, and always needs time as well.
- time is the fire clock when days are given, and a start clock otherwise.
- date + time with no interval and no days is a single occurrence.
- quiet_windows suppress hours of the day and only apply to an interval.

Never guess. "every morning", "soon", "twice a week", "a few times a day" do not determine a
schedule — call not_clear_enough with the single question the owner must answer.

Relative phrases are resolved against the current time given below: "in 90 minutes" is a
single occurrence at that moment, not an interval."""

async def resolve_schedule(
    provider: OpenAICompatibleProvider,
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
                description="The phrase does not determine a schedule; ask the owner this.",
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
