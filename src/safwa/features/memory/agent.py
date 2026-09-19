"""What the model is asked when a Sprint's analysis is taken into memory.

Memory routes to no subagent: this one question is the whole of what the model reads
here. It matches, and the code writes — which Sprints observed a pattern, with what
effect, and what memory shows of it are never the model's to change.
"""

from __future__ import annotations

from pydantic import Field

from tg_agent_shell.ai.contracts import ToolInput
from tg_agent_shell.ai.mini import TerminalTool

from ..retro.analysis import VERBOSE

PATTERN_PROMPT = """You are given numbered patterns from earlier Sprints and numbered candidates from the Sprint just analysed.
Each names one thing that raised a day's rating or lowered it.
Call pattern_review once. Fill verbose_analyse first.
same: every pair where a candidate names the same thing as a pattern, under the same stated conditions, in the same or other words. The effect may differ.
A shared word alone is not the same thing, and not doing a thing is not that thing.
Leave a candidate out when no pattern is about it."""


class Pair(ToolInput):
    candidate: int = Field(description="The number of a candidate.")
    pattern: int = Field(description="The number of the pattern that names the same thing.")


class PatternReview(ToolInput):
    verbose_analyse: str = Field(description=VERBOSE)
    same: list[Pair] = Field(
        description="A candidate and a pattern that name the same thing. Empty when none."
    )


PATTERN_TOOL = TerminalTool(
    "pattern_review", "Which candidates are patterns already known.", PatternReview
)
