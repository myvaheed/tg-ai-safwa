"""The few words the Home dashboard puts under each Value in focus.

One mini-session writes them for every Value at once, with reasoning off: a few sentences
need none, and a model left to reason can spend its whole output on it and write nothing.
The words are kept in memory for a while, so a Home drawn again soon reads them instead
of asking again, and one session runs at a time: whoever asks while it runs waits for it.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from llm_gateway import LlmProvider, OpenAICompatibleError
from telegram_llm.host import Spawn
from tg_agent_shell.ai.contracts import ToolInput
from tg_agent_shell.ai.mini import TerminalTool, run_mini_session
from tg_agent_shell.foundation.clock import utcnow

from ...foundation.workspace import require_workspace
from ..cards.api import finished_actions
from ..diary.api import last_entries
from ..planning.api import today_actions
from ..planning.model import Sprint
from ..profile.api import about_me
from ..values.api import values_in_focus
from ..workspace_mutator.api import priority_goals

logger = logging.getLogger(__name__)

# What the session reads besides the Values: the Actions finished last and the Diary days
# written last, however old.
MOTIVATION_DONE_ACTIONS = 10
MOTIVATION_DIARY_ENTRIES = 2
# How long the words under one Value may be.
MOTIVATION_MAX_CHARS = 200
# How long written words are shown again before they are asked for anew.
MOTIVATION_FRESH_MINUTES = 10
# Successful generations kept as context after the displayed words expire.
MOTIVATION_HISTORY_SIZE = 5

MOTIVATION_PROMPT = (
    "Write a few words for each Value that move the user to live by it today.\n"
    "Tie each Value to one real thing from About me, the Priority Goals, the Sprint's "
    "Success criteria, Today Actions, the finished Actions or the Diary.\n"
    "Prefer higher-ranked Goals when they fit the Value.\n"
    "Connect a relevant Today Action to the Sprint's Success criteria.\n"
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


async def owner_context(session: AsyncSession) -> str:
    """What the session reads before the Values."""
    workspace = await require_workspace(session)
    local_now = utcnow().astimezone(ZoneInfo(workspace.timezone))
    goals = await priority_goals(session, local_now)
    sprint = (
        await session.get(Sprint, workspace.active_sprint_id)
        if workspace.active_sprint_id else None
    )
    today = await today_actions(session, now=local_now)
    done = await finished_actions(session, MOTIVATION_DONE_ACTIONS)
    days = await last_entries(session, MOTIVATION_DIARY_ENTRIES)
    lines = [
        "About me:", (await about_me(session)).strip() or "Nothing written.", "",
        f"Today: {local_now:%Y-%m-%d}", "Priority Goals, highest focus first:",
    ]
    lines += [
        f"- {goal.title} (priority={goal.priority})"
        + (
            f" deadline={goal.deadline_at.astimezone(local_now.tzinfo):%Y-%m-%d %H:%M}"
            if goal.deadline_at else ""
        )
        for goal in goals
    ] or ["None."]
    lines += [
        "", "Current Sprint's Success criteria:",
        sprint.success_criteria.strip() if sprint else "No Sprint is running.",
    ]
    lines += ["", "Today Actions, in Today's order:"]
    lines += [f"- {card.title} (priority={card.priority})" for card in today] or ["None."]
    lines += ["", "Actions finished last, newest first:"]
    lines += [f"- {card.completed_at:%Y-%m-%d} {card.title}" for card in done] or ["None."]
    lines += ["", "Diary, newest first:"]
    lines += [f"{day.entry_date:%Y-%m-%d}: {day.body}" for day in days] or ["Nothing written."]
    return "\n".join(lines)


class Motivator:
    """The words for every Value in focus, on the one provider the application has.

    `spawn` starts the session as a task the application ends on shutdown.
    """

    def __init__(self, provider: LlmProvider, *, spawn: Spawn) -> None:
        self.provider = provider
        self.spawn = spawn
        self._kept: tuple[datetime, dict[int, str]] | None = None
        self._history: deque[dict[int, str]] = deque(maxlen=MOTIVATION_HISTORY_SIZE)
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
        # Words that failed are not kept: the next Home asks again.
        if words:
            self._kept = (utcnow(), words)
            self._history.append(words.copy())

    async def _ask(self, sessions: async_sessionmaker[AsyncSession]) -> dict[int, str]:
        async with sessions() as session:
            values = [
                (value.id, value.name, value.description)
                for value in await values_in_focus(session)
            ]
            if not values:
                return {}
            shared = await owner_context(session)
        listed = [
            f"{number}. {name}" + (f" — {about}" if about else "")
            for number, (_, name, about) in enumerate(values, start=1)
        ]
        previous = [
            f"- Value {number}: {words[value_id]}"
            for words in reversed(self._history)
            for number, (value_id, _, _) in enumerate(values, start=1)
            if value_id in words
        ]
        try:
            result = await run_mini_session(
                self.provider,
                system_prompt=MOTIVATION_PROMPT,
                context="\n".join(
                    [
                        shared, "", "Previous motivation, newest first:",
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
                words.setdefault(values[said.value - 1][0], said.text.strip())
        return words
