"""The one question memory asks the model: which candidates are patterns already known."""

from __future__ import annotations

from collections.abc import Sequence

from llm_gateway import LlmProvider
from tg_agent_shell.ai.mini import run_mini_session

from .absorb import Candidate, Pattern, review_text
from .agent import PATTERN_PROMPT, PATTERN_TOOL


class PatternReviewer:
    """The one question, on the one provider the application has."""

    def __init__(self, provider: LlmProvider) -> None:
        self.provider = provider

    async def __call__(
        self, patterns: Sequence[Pattern], found: Sequence[Candidate]
    ) -> list[tuple[int, int]]:
        result = await run_mini_session(
            self.provider,
            system_prompt=PATTERN_PROMPT,
            context=review_text(patterns, found),
            terminals=(PATTERN_TOOL,),
            max_tool_calls=None,
        )
        return [(pair.candidate, pair.pattern) for pair in result.payload.same]
