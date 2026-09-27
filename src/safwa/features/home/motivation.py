"""The few words the Home dashboard puts under each Value in focus.

One mini-session per Value, asked as the dashboard is drawn and kept nowhere else. What the
owner is, wants, did and wrote comes first and the Value last, so every session after the
first reads the same prefix.
"""

from __future__ import annotations

import asyncio
import logging

from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from llm_gateway import LlmProvider, OpenAICompatibleError
from tg_agent_shell.ai.contracts import ToolInput
from tg_agent_shell.ai.mini import TerminalTool, run_mini_session

from ..cards.api import finished_actions, open_goal_titles
from ..diary.api import last_entries
from ..profile.api import about_me
from ..values.api import values_in_focus

logger = logging.getLogger(__name__)

# What a session reads besides its Value: the Actions finished last and the Diary days
# written last, however old.
MOTIVATION_DONE_ACTIONS = 10
MOTIVATION_DIARY_ENTRIES = 2
# How long the words under one Value may be.
MOTIVATION_MAX_CHARS = 200

MOTIVATION_PROMPT = (
    "Write a few words that move the owner to live by one Value today.\n"
    "Tie the Value to one real thing from About me, the Goals, the finished Actions or "
    "the Diary.\n"
    f"One or two sentences, at most {MOTIVATION_MAX_CHARS} characters.\n"
    'Speak to the owner as "you".\n'
    "Write in the language of the Diary and About me.\n"
    "Call motivate with the text."
)


class MotivateInput(ToolInput):
    text: str = Field(
        min_length=1,
        max_length=MOTIVATION_MAX_CHARS,
        description="The words for the owner, one or two sentences.",
    )


MOTIVATE_TOOL = TerminalTool(
    name="motivate",
    description="Give the words for this Value.",
    model=MotivateInput,
)


async def owner_context(session: AsyncSession) -> str:
    """What every session reads before its Value."""
    goals = await open_goal_titles(session)
    done = await finished_actions(session, MOTIVATION_DONE_ACTIONS)
    days = await last_entries(session, MOTIVATION_DIARY_ENTRIES)
    lines = ["About me:", (await about_me(session)).strip() or "Nothing written.", "", "Goals:"]
    lines += [f"- {title}" for title in goals] or ["None."]
    lines += ["", "Actions finished last, newest first:"]
    lines += [f"- {card.completed_at:%Y-%m-%d} {card.title}" for card in done] or ["None."]
    lines += ["", "Diary, newest first:"]
    lines += [f"{day.entry_date:%Y-%m-%d}: {day.body}" for day in days] or ["Nothing written."]
    return "\n".join(lines)


class Motivator:
    """The words for every Value in focus, on the one provider the application has."""

    def __init__(self, provider: LlmProvider) -> None:
        self.provider = provider

    async def write(self, sessions: async_sessionmaker[AsyncSession]) -> dict[int, str]:
        """The words by Value id; a Value whose session gave none is absent."""
        async with sessions() as session:
            values = [
                (value.id, value.name, value.description)
                for value in await values_in_focus(session)
            ]
            shared = await owner_context(session) if values else ""
        answers = await asyncio.gather(
            *(
                self._ask(f"{shared}\n\nValue: {name}" + (f" — {about}" if about else ""))
                for _, name, about in values
            )
        )
        return {
            value_id: words
            for (value_id, _, _), words in zip(values, answers, strict=True)
            if words is not None
        }

    async def _ask(self, context: str) -> str | None:
        try:
            result = await run_mini_session(
                self.provider,
                system_prompt=MOTIVATION_PROMPT,
                context=context,
                terminals=(MOTIVATE_TOOL,),
                max_tool_calls=None,
            )
        # `MiniSessionError` is a RuntimeError, and so is a provider that sent nothing back.
        except (RuntimeError, OpenAICompatibleError):
            logger.exception("No words for a Value on the Home dashboard")
            return None
        return result.payload.text.strip()
