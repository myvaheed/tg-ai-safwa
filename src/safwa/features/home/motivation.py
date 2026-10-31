"""The few words the Home dashboard puts under each Value in focus.

Mini-sessions write them for small groups of Values, with reasoning off: a few sentences
need none, and a model left to reason can spend its whole output on it and write nothing.
The words are kept in memory for a while, so a Home drawn again soon reads them instead
of asking again, and one generation runs at a time: whoever asks while it runs waits for it.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from llm_gateway import LlmProvider, OpenAICompatibleError
from telegram_llm.host import Spawn
from tg_agent_shell.ai.contracts import ToolInput
from tg_agent_shell.ai.mini import TerminalTool, run_mini_session
from tg_agent_shell.foundation.clock import utcnow
from tg_agent_shell.foundation.days import day_words, today_line

from ...foundation.workspace import require_workspace
from ..cards.api import finished_actions, goal_tree, order_cards, served_values
from ..checks.model import Check
from ..diary.api import last_entries
from ..planning.api import today_actions, values_in_focus
from ..planning.model import Sprint
from ..profile.api import about_me, motivator_instructions
from ..schedules.api import checks_today
from ..values.api import CheckValue, Value

logger = logging.getLogger(__name__)

# What the session reads besides the Values: the Actions finished last and the Diary days
# written last, however old.
MOTIVATION_DONE_ACTIONS = 5
MOTIVATION_DIARY_ENTRIES = 1
# How long the words under one Value may be.
MOTIVATION_MAX_CHARS = 200
# How long written words are shown again before they are asked for anew.
MOTIVATION_FRESH_MINUTES = 10
MOTIVATION_BATCH_SIZE = 3
MOTIVATION_CONCURRENCY = 2

MOTIVATION_PROMPT = (
    "Write a few words for each Value that move the user to live by it today.\n"
    "Mention a Card or Check only when its listed Values include that Value.\n"
    "Tie each Value to a relevant Today Card or scheduled Check.\n"
    "Also use About me, the Sprint's Success criteria, the finished Actions or the Diary "
    "when they fit.\n"
    "Connect a Today Action to the Sprint's Success criteria when relevant.\n"
    "Do not invent an Action or a connection to a Value.\n"
    "Read Previous motivation for each Value; use a different angle and wording.\n"
    "Do not repeat its message or merely rephrase it.\n"
    f"One or two sentences per Value, at most {MOTIVATION_MAX_CHARS} characters.\n"
    'Speak to the user as "you".\n'
    "Write in the language of the Diary and About me.\n"
    "Call motivate once, with the words for every Value."
)


class ValueWords(ToolInput):
    value: int = Field(description="The number of a Value.")
    text: str = Field(
        min_length=1,
        max_length=MOTIVATION_MAX_CHARS,
        description="The words for the owner, one or two sentences.",
    )


class MotivateInput(ToolInput):
    words: list[ValueWords] = Field(min_length=1, description="The words for each Value.")


MOTIVATE_TOOL = TerminalTool(
    name="motivate",
    description="Give the words for every Value.",
    model=MotivateInput,
)


async def owner_context(session: AsyncSession) -> tuple[str, dict[int, list[str]]]:
    """What the session reads before the Values."""
    workspace = await require_workspace(session)
    local_now = utcnow().astimezone(ZoneInfo(workspace.timezone))
    sprint = (
        await session.get(Sprint, workspace.active_sprint_id)
        if workspace.active_sprint_id else None
    )
    actions = await order_cards(session, await today_actions(session), now=local_now)
    today = await goal_tree(session, actions, now=local_now)
    done = await finished_actions(session, MOTIVATION_DONE_ACTIONS)
    days = await last_entries(session, MOTIVATION_DIARY_ENTRIES)
    cards = {row.card.id: row.card for row in today}
    cards.update((parent.id, parent) for row in today for parent in row.also_under)
    serves = await served_values(session, [*cards, *(card.id for card in done)])
    names = dict((await session.execute(select(Value.id, Value.name))).all())
    by_value: dict[int, list[str]] = {}
    for card in cards.values():
        for value_id in serves[card.id]:
            by_value.setdefault(value_id, []).append(f"{card.kind.capitalize()}: {card.title}")

    def values(card_id: int) -> str:
        return ", ".join(names[value_id] for value_id in serves[card_id]) or "none"

    lines = [
        "About me:", (await about_me(session)).strip() or "Nothing written.", "",
        today_line(local_now, workspace.timezone),
    ]
    lines += [
        "", "Current Sprint's Success criteria:",
        sprint.success_criteria.strip() if sprint else "No Sprint is running.",
    ]
    lines += ["", "Today Actions, in Today's order, each under its Goals and Subgoals:"]
    lines += [
        f"{'  ' * row.depth}- {row.card.title} "
        f"(kind={row.card.kind}, priority={row.card.priority}, values={values(row.card.id)})"
        + (
            f" (also under: {', '.join(parent.title for parent in row.also_under)})"
            if row.also_under
            else ""
        )
        for row in today
    ] or ["None."]
    checks = await checks_today(session, now=local_now)
    check_values: dict[int, list[int]] = {}
    if checks:
        for check_id, value_id in await session.execute(
            select(CheckValue.check_id, CheckValue.value_id)
            .join(Value, Value.id == CheckValue.value_id)
            .where(CheckValue.check_id.in_([check.id for check, _ in checks]))
            .order_by(Value.order)
        ):
            check_values.setdefault(check_id, []).append(value_id)
    lines += ["", "Scheduled Checks remaining today:"]
    check_lines = []
    for check, count in checks:
        linked = check_values.get(check.id, [])
        for value_id in linked:
            by_value.setdefault(value_id, []).append(f"Check: {check.title}")
        check_lines.append(
            f"- {check.title} (schedule={check.schedule}, remaining_today={count}, "
            f"values={', '.join(names[value_id] for value_id in linked) or 'none'})"
        )
    lines += check_lines or ["None."]
    lines += ["", "Actions finished last, newest first:"]
    lines += [
        f"- {day_words(card.completed_at.astimezone(local_now.tzinfo).date(), local_now.date())} "
        f"{card.title} "
        f"(values={values(card.id)})" for card in done
    ] or ["None."]
    lines += ["", "Diary, newest first:"]
    lines += [
        f"{day_words(day.entry_date, local_now.date())}: {day.body}" for day in days
    ] or ["Nothing written."]
    return "\n".join(lines), by_value


class Motivator:
    """The words for every Value in focus, on the one provider the application has.

    `spawn` starts the session as a task the application ends on shutdown.
    """

    def __init__(self, provider: LlmProvider, *, spawn: Spawn) -> None:
        self.provider = provider
        self.spawn = spawn
        self._kept: tuple[datetime, dict[int, str]] | None = None
        self._previous: dict[int, str] = {}
        self._writing: asyncio.Task[None] | None = None

    def fresh(self) -> dict[int, str] | None:
        """The words written within the last `MOTIVATION_FRESH_MINUTES`, or None."""
        if self._kept is None:
            return None
        written, words = self._kept
        if utcnow() - written >= timedelta(minutes=MOTIVATION_FRESH_MINUTES):
            return None
        return words

    async def write(self, sessions: async_sessionmaker[AsyncSession]) -> dict[int, str]:
        """The words by Value id: fresh ones, else those of the session already running, else
        a new session's. A Value with no words is absent.

        The session is a task of its own, so a caller that stops waiting does not stop it.
        """
        words = self.fresh()
        if words is not None:
            return words
        if self._writing is None:
            self._writing = self.spawn(self._write(sessions), "Home words")
        await asyncio.shield(self._writing)
        return self.fresh() or {}

    async def _write(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        try:
            words = await self._ask(sessions)
        finally:
            self._writing = None
        # Words that failed are not kept: the next Home asks again, against the last words
        # that were written.
        if words:
            self._kept = (utcnow(), words)
            self._previous.update(words)

    async def _ask(self, sessions: async_sessionmaker[AsyncSession]) -> dict[int, str]:
        async with sessions() as session:
            focus = await values_in_focus(session)
            values = [value for value, _ in focus]
            if not values:
                return {}
            shared, items = await owner_context(session)
            for value, holders in focus:
                linked = items.setdefault(value.id, [])
                for holder in holders:
                    kind = "Check" if isinstance(holder, Check) else holder.kind.capitalize()
                    label = f"{kind}: {holder.title}"
                    if label not in linked:
                        linked.append(label)
            custom = (await motivator_instructions(session)).strip()
        prompt = MOTIVATION_PROMPT + (f"\n\nMotivator instructions:\n{custom}" if custom else "")
        gate = asyncio.Semaphore(MOTIVATION_CONCURRENCY)
        batches = [values[i:i + MOTIVATION_BATCH_SIZE] for i in range(0, len(values), MOTIVATION_BATCH_SIZE)]

        async def write_batch(batch: list[Value]) -> dict[int, str]:
            async with gate:
                return await self._ask_batch(prompt, shared, batch, items)

        results = await asyncio.gather(*(write_batch(batch) for batch in batches))
        words = {value_id: said for result in results for value_id, said in result.items()}
        return {value.id: words[value.id] for value in values if value.id in words}

    async def _ask_batch(
        self, prompt: str, shared: str, values: list[Value], items: dict[int, list[str]],
    ) -> dict[int, str]:
        listed = [
            f"{number}. {value.name}"
            + (f" — {value.description}" if value.description else "")
            + f" (carried today by: {'; '.join(items.get(value.id, []))})"
            for number, value in enumerate(values, start=1)
        ]
        last = self._previous
        previous = [
            f"- Value {number}: {last[value.id]}"
            for number, value in enumerate(values, start=1)
            if value.id in last
        ]
        try:
            result = await run_mini_session(
                self.provider,
                system_prompt=prompt,
                context="\n".join(
                    [
                        shared, "", "Previous motivation:",
                        *(previous or ["None."]), "", "Values, numbered:", *listed,
                    ]
                ),
                terminals=(MOTIVATE_TOOL,),
                max_tool_calls=None,
                reasoning_effort="none",
            )
        # `MiniSessionError` is a RuntimeError, and so is a provider that sent nothing back.
        except (RuntimeError, OpenAICompatibleError):
            logger.exception("No words for the Values on the Home dashboard")
            return {}
        words: dict[int, str] = {}
        for said in result.payload.words:
            # A number that names no Value is dropped; the first words for a Value count.
            if 1 <= said.value <= len(values):
                words.setdefault(values[said.value - 1].id, said.text.strip())
        return words
