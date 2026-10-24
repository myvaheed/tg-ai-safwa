"""What the model may do with a Reminder.

The reminder subagent lives here with its two tools, because they are one contract with the
setup session that turns a free-text ``when`` into the parameters of a clock time. `reminder`
makes, changes or deletes a Reminder; `remind` turns on the Remind of an Action, a Goal or a
Check, a change the feature owning that item applies, and is the one way to change the Reminder
it makes. The workspace mutator keeps neither, so no time crowds its prompt, and the subagent
has no tool that deletes anything else. The open items with a Schedule follow its dialogue,
see `items`, so it names the owner's by meaning.

A time arrives as the owner's words, and its shape is derived from which parameters the setup
session sent back. A Reminder on answers of Checks or Actions arrives as `on_answers` alone:
a short session of its own picks the items from the open items and reads what it counts
from the owner's words in the conversation, and a count the words do not say goes back to
the subagent to ask. Code checks the items it names and keeps the series of each.

Every way the words fail to become a time ends in one :class:`ScheduleError`: what is unclear,
then the forms that work, so the owner always hears both. Words that count answers are no time:
:class:`CountsAnswers` sends the subagent to `on_answers` instead.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, PositiveInt, PrivateAttr, model_validator

from llm_gateway import LlmProvider
from tg_agent_shell.ai.contracts import AgentChange, ChangeAction, RecordToolInput, ToolInput
from tg_agent_shell.ai.mini import (
    MINI_SESSION_MAX_TOOL_CALLS,
    MiniSessionError,
    TerminalTool,
    run_mini_session,
)
from tg_agent_shell.proposals.api import MutationToolSpec, entity_change
from tg_agent_shell.telegram.manifest import AgentSpec

from ...constants import (
    PROPOSING_TURN,
    READING_THE_WORKSPACE,
    REMIND_LEAD_MAX_MINUTES,
    WEEKDAY_NAMES,
)
from .api import (
    BOUNDS_LINES,
    LAST_MONTH_DAY,
    MONTH_DAYS_DESCRIPTION,
    UNTIL_DESCRIPTION,
    Schedule,
    ScheduleError,
    resolve,
)
from .conditions import named_items
from .items import listed_items, reminder_items

NO_MISSED = "An Action has no Missed."

REMIND_DESCRIPTION = (
    "Remind the user at its time, 0, or this many minutes before it: "
    "'remind me' is 0, 'remind me 25 minutes before' is 25, '3 hours before' is 180. "
    "null turns it off."
)


class ConditionFields(ToolInput):
    items: str = Field(
        description="The Checks or Actions it counts, by #id from Open items: 'Check #12' or 'Action #3, Action #5'."
    )
    answer: Literal["yes", "no", "any"] = Field(
        description="yes: Passed of a Check or Done of an Action. no: Missed of a Check. any: every answer."
    )
    count: PositiveInt = Field(description="How many of that answer; 1 for each time.")
    in_row: bool = Field(default=False, description="Counted in a row. Only with yes or no.")
    once: bool = Field(
        default=False,
        description=(
            "true for 'after N', 'when I reach N' or 'as soon as': it fires once, at N. false for "
            "'every N' or 'each time'."
        ),
    )

    @model_validator(mode="after")
    def counts_a_row_of_one_answer(self) -> ConditionFields:
        named_items(self.items)
        if self.in_row and self.answer == "any":
            raise ValueError("in_row needs answer yes or no: set in_row=false")
        return self


class ConditionUnclearInput(ToolInput):
    reason: str = Field(description="What is missing or unclear, in one short sentence.")


class ReminderToolInput(RecordToolInput):
    create_requires = ("instruction",)

    mode: Literal["create", "update", "delete"]
    instruction: str | None = Field(
        default=None,
        description=(
            "What Safwa does when it fires, as a request to the Advisor. Name every item it "
            "concerns by #id."
        ),
    )
    when: str | None = Field(
        default=None,
        description=(
            "The user's own words for a clock time, in their language. Omit it for a Reminder on "
            "answers, and on update to leave the schedule untouched."
        ),
    )
    on_answers: bool = Field(
        default=False,
        description="For a Reminder on answers of Checks or Actions, instead of when.",
    )

    @model_validator(mode="after")
    def validate_target(self) -> ReminderToolInput:
        if self.mode == "delete":
            if self.id is None:
                raise ValueError("a deleted Reminder needs an id")
            return self
        if self.when and self.on_answers:
            raise ValueError("set when or on_answers, not both")
        if self.mode == "create" and not (self.when or "").strip() and not self.on_answers:
            raise ValueError("a new Reminder needs when or on_answers")
        return super().validate_target()


class ReminderConfigInput(ToolInput):
    days: list[Literal["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]] | None = Field(
        default=None, description="Weekdays to fire on; all seven means every day."
    )
    month_days: list[Annotated[int, Field(ge=1, le=LAST_MONTH_DAY)]] | None = Field(
        default=None, description=MONTH_DAYS_DESCRIPTION
    )
    time: str | None = Field(
        default=None,
        description=(
            "Local wall clock HH:MM. With days it is the time it fires at; otherwise it is "
            "when the schedule starts."
        ),
    )
    first_day: str | None = Field(
        default=None,
        description=(
            "Local calendar date dd.mm.yyyy or dd.mm, today, tomorrow, or a weekday such as Thu "
            "for the next one: the day when it fires once, the first day when it repeats. "
            "Needs time as well."
        ),
    )
    last_day: str | None = Field(default=None, description=UNTIL_DESCRIPTION)
    interval_minutes: PositiveInt | None = Field(
        default=None, description="Repeat every N minutes."
    )
    in_minutes: PositiveInt | None = Field(default=None, description="Fire once, N minutes from now.")
    limit: PositiveInt | None = Field(
        default=None, description="How many times it fires in total. Only when the text says."
    )


class ReminderUnclearInput(ToolInput):
    reason: str = Field(description="What is missing or unclear, in one short sentence.")
    about: Literal["time", "answers"] | None = Field(
        default=None,
        description="time when the phrase means a clock time, answers when it counts answers.",
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


class RemindToolInput(ToolInput):
    item: Literal["action", "goal", "check"]
    id: PositiveInt
    minutes_before: int | None = Field(ge=0, le=REMIND_LEAD_MAX_MINUTES, description=REMIND_DESCRIPTION)


def _remind_change(call: BaseModel) -> AgentChange:
    """An update of the item with Remind alone, applied by the feature that owns it. A Goal's
    Remind is an Action's, so the call names no kind for a Card to refuse."""
    return AgentChange(
        entity="check" if call.item == "check" else "card",
        action=ChangeAction.UPDATE,
        id=call.id,
        values={"remind": call.minutes_before},
    )


REMIND_TOOL = MutationToolSpec(
    name="remind",
    input_model=RemindToolInput,
    description="Propose Remind on one Action, Goal or Check: a message at its time or before it.",
    to_change=_remind_change,
)

REMINDER_AGENT_PROMPT = f"""You keep the user's Reminders and the Remind on their Actions, Goals and Checks.

{{items}}

{PROPOSING_TURN}

# Which tool
- At an Action's, a Goal's or a Check's own time, or before it: `remind` on that item.
- At any other time: `reminder` with when.
- On answers of Checks or Actions: `reminder` with on_answers=true.
- To delete a Reminder: `reminder` with mode=delete and its id.
- The Morning and Evening times are Profile fields, not Reminders.

# when
- The user's own words for the time, in their language. Never invent a date or an hour.

# Find the item
- For `remind`: pick the user's item from Open items by meaning, in any language.
- Not in Open items: find it with `search`.

{READING_THE_WORKSPACE}"""

REMINDER_AGENT = AgentSpec(
    name="reminder",
    purpose=(
        'set, change or delete a Reminder: at a time, on answers of Checks or Actions such as '
        '"tell me when …", or Remind before an Action, a Goal or a Check.'
    ),
    instructions=REMINDER_AGENT_PROMPT,
    # The Reminders, and the items a Remind or a count of answers names. No Sprint and no
    # workspace state: what it decides is when, never what the work is.
    views=("ai_reminders", "ai_checks", "ai_cards", "ai_tags", "ai_values"),
    mutation_tools=("reminder", "remind"),
    current=reminder_items,
)

REMINDER_WHEN_PROMPT = f"""Read the clock time one Reminder fires at. End with one call: set_reminder_config or not_clear_enough.
Clock time, set_reminder_config:
'every day at 8', 'every morning at 8': days=[Mon, Tue, Wed, Thu, Fri, Sat, Sun], time=08:00.
'every evening at 19': days=[Mon, Tue, Wed, Thu, Fri, Sat, Sun], time=19:00.
'every weekday at 8am': days=[Mon, Tue, Wed, Thu, Fri], time=08:00.
'every Monday at 9:30', 'on Mondays at 9:30': days=[Mon], time=09:30.
'on Monday at 9:30', without every: first_day=Mon, time=09:30. It fires once.
'on the 5th and 20th of each month at 9': month_days=[5, 20], time=09:00.
'on the last day of each month at 21': month_days=[31], time=21:00.
'tomorrow at 15:00': first_day=tomorrow, time=15:00. 'on 20 October at 15:00': first_day=20.10, time=15:00.
'in 90 minutes', 'in 2 hours': in_minutes=90, in_minutes=120. It fires once.
'every 2 hours': interval_minutes=120.
'every weekday at 8 until 15 December': days=[Mon, Tue, Wed, Thu, Fri], time=08:00, last_day=15.12.
'every 2 hours until Sunday': interval_minutes=120, last_day=Sun.
'every day at 8 for 3 weeks': days=[Mon, Tue, Wed, Thu, Fri, Sat, Sun], time=08:00, last_day=3 weeks.
'every Friday at 18 from 6 November for 2 months': days=[Fri], time=18:00, first_day=06.11, last_day=2 months.
'every day at 9, 10 times': days=[Mon, Tue, Wed, Thu, Fri, Sat, Sun], time=09:00, limit=10.
{BOUNDS_LINES}
Resolve relative dates against the current time. Never invent a time.
Not clear, not_clear_enough:
No clock time, like 'every morning', 'soon', 'twice a week', 'once a month': about=time.
Counts answers of Checks or Actions, like 'every 3 Missed of Check #12': about=answers.
reason: what is missing, in one short sentence."""

# The forms the owner is shown when the words cannot be scheduled.
REMINDER_TIME_FORMS = (
    "tomorrow at 15:00",
    "on Thursday at 10:00",
    "every weekday at 8:00",
    "every 2 hours",
)
UNREADABLE_WHEN = "Could not read when the Reminder fires."
COUNTS_ANSWERS = "when counts answers of Checks or Actions."


class CountsAnswers(ScheduleError):
    """Words in `when` that count answers, which `on_answers` reads instead."""


def unclear(reason: str) -> ScheduleError:
    """What is unclear, then the forms that work."""
    forms = ", ".join(f"'{form}'" for form in REMINDER_TIME_FORMS)
    return ScheduleError(f"{reason}\nBy time: {forms}.")


async def resolve_schedule(
    provider: LlmProvider,
    *,
    when: str,
    now: datetime,
    tz: ZoneInfo,
) -> Schedule:
    """Resolve a free-text time into a Schedule, or raise with what is unclear and the forms."""
    local = now.astimezone(tz)
    context = (
        f"Timing phrase: {when}\n"
        f"Current local time: {local:%Y-%m-%d %H:%M} ({WEEKDAY_NAMES[local.weekday()]}), "
        f"timezone {tz.key}"
    )
    refusals: list[str] = []

    # Why the parameters make no schedule goes back to the model first, so it can fix them,
    # and to the owner when it cannot. No docstring: it would reach the model as the schema's.
    class ResolvedConfig(ReminderConfigInput):
        _schedule: Schedule | None = PrivateAttr(default=None)

        @model_validator(mode="after")
        def resolves(self) -> ResolvedConfig:
            try:
                self._schedule = resolve(
                    days=self.days,
                    month_days=self.month_days,
                    clock=self.time,
                    day=self.first_day,
                    interval_minutes=self.interval_minutes,
                    in_minutes=self.in_minutes,
                    until=self.last_day,
                    limit=self.limit,
                    now=now,
                    tz=tz,
                )
            except ScheduleError as error:
                refusals.append(str(error))
                raise ValueError(str(error)) from error
            return self

    try:
        result = await run_mini_session(
            provider,
            system_prompt=REMINDER_WHEN_PROMPT,
            context=context,
            terminals=(
                TerminalTool(
                    name="set_reminder_config",
                    description="The phrase is a clock time; these are its parameters.",
                    model=ResolvedConfig,
                ),
                TerminalTool(
                    name="not_clear_enough",
                    description="The phrase does not say when; say what is missing.",
                    model=ReminderUnclearInput,
                ),
            ),
            max_tool_calls=MINI_SESSION_MAX_TOOL_CALLS,
            plan_required=True,
        )
    except MiniSessionError as error:
        raise unclear(refusals[-1] if refusals else UNREADABLE_WHEN) from error
    if result.name == "not_clear_enough":
        if result.payload.about == "answers":
            raise CountsAnswers(COUNTS_ANSWERS)
        raise unclear(result.payload.reason)
    return result.payload._schedule


REMINDER_CONDITION_PROMPT = """Read one Reminder on answers of Checks or Actions. End with one call: set_condition or not_clear_enough.
The user's words are in the conversation. Read the fields from the user's own words.
items: every item it counts, from Open items, by #id: 'Check #12' or 'Action #3, Action #5'. Checks or Actions, never both.
A group, such as 'tagged Sport' or 'my Health checks': every Open item with that Tag or Value.
Several items: their answers count together.
#12 is an item's id, never a count.
answer: yes is Passed of a Check or Done of an Action; no is Missed of a Check; any is every answer.
Read the answer against the item's title: 'when I stay up past 11' on «Went to bed by 23?» is no.
count: how many of that answer.
in_row: true only when the words say in a row. Never with any.
once: true when it fires once, at count; false when it fires at every count.
'every 3 misses in a row', 'каждые три пропуска подряд': answer=no, count=3, in_row=true, once=false.
'when I train 5 evenings in a row', 'когда 5 вечеров подряд отзанимаюсь': answer=yes, count=5, in_row=true, once=true.
'as soon as I reach 40 yeses', 'как только наберу 40 «да»': answer=yes, count=40, once=true.
'on the 50th time', 'на 50-й раз': answer=yes, count=50, once=true.
'every 12 done', 'каждые 12 сделанных': answer=yes, count=12, once=false.
'each time I skip it', 'каждый раз, когда пропущу': answer=no, count=1, once=false.
'every 6th skip, not in a row', 'каждый 6-й пропуск, не подряд': answer=no, count=6, in_row=false, once=false.
'whatever I answer', 'неважно, да или нет': answer=any.
Actions have only Done: answer=yes. Missed of Actions: not_clear_enough.
Not clear, not_clear_enough: no number, like 'a decent run' or 'серьёзная серия', or no Open item fits. Never for in a row or once: decide them from the words.
reason: what is missing, in one short sentence."""

UNREADABLE_CONDITION = "Could not read what the Reminder counts."


class ConditionUnclear(ValueError):
    """Words that do not say what a Reminder on answers counts."""


async def read_condition(
    provider: LlmProvider,
    *,
    open_items: str,
    conversation: str = "",
) -> ConditionFields:
    """The items of `open_items` the owner's words count answers of, and how, or
    ConditionUnclear with what they do not say. The words are read in the `conversation` the
    call came from. Items the block does not list go back to the session to name again.
    """
    listed = listed_items(open_items)

    class OpenItemsOnly(ConditionFields):
        @model_validator(mode="after")
        def listed_only(self) -> OpenItemsOnly:
            kind, ids = named_items(self.items)
            for item_id in ids:
                if (kind, item_id) not in listed:
                    raise ValueError(f"{kind.title()} #{item_id} is not in Open items.")
            return self

    try:
        result = await run_mini_session(
            provider,
            system_prompt=REMINDER_CONDITION_PROMPT,
            context=open_items,
            terminals=(
                TerminalTool(
                    name="set_condition",
                    description="The words say what it counts; these are its fields.",
                    model=OpenItemsOnly,
                ),
                TerminalTool(
                    name="not_clear_enough",
                    description="The words do not say what it counts; say what is missing.",
                    model=ConditionUnclearInput,
                ),
            ),
            max_tool_calls=MINI_SESSION_MAX_TOOL_CALLS,
            conversation=conversation,
            plan_required=True,
        )
    except MiniSessionError as error:
        raise ConditionUnclear(UNREADABLE_CONDITION) from error
    if result.name == "not_clear_enough":
        raise ConditionUnclear(result.payload.reason)
    kind, _ids = named_items(result.payload.items)
    # Checked here, not in the schema: told to fix it, the session would count Done instead.
    if kind == "action" and result.payload.answer == "no":
        raise ConditionUnclear(NO_MISSED)
    if kind == "action":
        # Done is an Action's only answer: no Missed to count, or to break a row.
        return result.payload.model_copy(update={"answer": "yes", "in_row": False})
    return result.payload
