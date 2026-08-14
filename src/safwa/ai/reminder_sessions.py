"""The two Reminder mini-sessions and the escalation text they feed.

Setup runs before a Reminder proposal is written, turning free-text timing into parameters
the code can compute with.  Relevance runs at fire time, reading the items the instruction
names so the main advisor does not have to look them up itself.

Neither writes anything.  The setup session's output becomes an ordinary Save/Discard
proposal; the relevance session's output becomes two lines of an escalation.
"""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from ..constants import RELEVANCE_MAX_TOOL_CALLS, WEEKDAY_NAMES
from ..enums import RelevanceVerdict
from ..reminders import Schedule, ScheduleError, resolve
from .contracts import NotClearEnoughInput, RelevanceCheckInput, ReminderConfigInput
from .mini import MiniSessionError, ReadTool, TerminalTool, run_mini_session
from .provider import OpenAICompatibleProvider

logger = logging.getLogger(__name__)

SETUP_PROMPT = """You turn one plain-language timing phrase into schedule parameters. You do
nothing else: you never write the Reminder, never judge whether it is a good idea, and never
touch its text.

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
schedule — call not_clear_enough with the single question the owner must answer. A guessed
time is only discovered when the Reminder fires at 03:00.

Relative phrases are resolved against the current time given below: "in 90 minutes" is a
single occurrence at that moment, not an interval."""

RELEVANCE_PROMPT = """A Reminder is about to fire. Report the current state of every Safwa
item its text names by #id, and judge whether the Reminder still makes sense.

Use query_safwa to read the items. Then call complete_relevance_check exactly once.

verdict:
- trigger — the items are still live, or the text names nothing that could expire.
- irrelevant — what the Reminder watches is Done, Cancelled, or no longer exists.

state: one or two sentences, each item by #id with its current stage or outcome. This text
is shown to the advisor verbatim, so write it for a reader, not as a data dump. When the
verdict is irrelevant, say plainly what happened and when.

You are not deciding whether to send anything, and you are not writing the reminder message.
Both verdicts are reported onward. Time of day is never your concern."""


async def resolve_schedule(
    provider: OpenAICompatibleProvider,
    *,
    when: str,
    instruction: str,
    now: datetime,
    tz: ZoneInfo,
) -> Schedule:
    """Resolve free-text timing into a Schedule, or raise with the question to ask the owner.

    Runs before the proposal row is written so the review screen shows a real schedule
    rather than the words the model happened to use.
    """
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
        max_tool_calls=RELEVANCE_MAX_TOOL_CALLS,
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


async def check_relevance(
    provider: OpenAICompatibleProvider,
    *,
    instruction: str,
    read_tool: tuple[dict, ReadTool],
    now: datetime,
    tz: ZoneInfo,
) -> tuple[RelevanceVerdict, str | None]:
    """Read the items an instruction names and judge whether it still makes sense.

    A failure here must not stop the Reminder: the fallback is to fire with no state line,
    which is exactly what an instruction naming no item does anyway.
    """
    context = (
        f"Reminder text: {instruction}\n"
        f"Current local time: {now.astimezone(tz):%Y-%m-%d %H:%M}, timezone {tz.key}"
    )
    try:
        result = await run_mini_session(
            provider,
            system_prompt=RELEVANCE_PROMPT,
            context=context,
            terminals=(
                TerminalTool(
                    name="complete_relevance_check",
                    description="Report the items' state and whether the Reminder still applies.",
                    model=RelevanceCheckInput,
                ),
            ),
            read_tool=read_tool,
            max_tool_calls=RELEVANCE_MAX_TOOL_CALLS,
        )
    except (MiniSessionError, RuntimeError):
        logger.exception("Relevance check failed; firing without a state line")
        return RelevanceVerdict.TRIGGER, None
    payload: RelevanceCheckInput = result.payload
    return RelevanceVerdict(payload.verdict), payload.state.strip() or None
