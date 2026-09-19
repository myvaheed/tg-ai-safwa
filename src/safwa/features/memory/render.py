"""What Safwa remembers, as the Advisor reads it and as the owner sees it with /memory.

Nothing here is stored: the patterns are a selection over the observations, made as the
text is rendered, and the last analysed Sprint's block is read off its row each time.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..retro.api import AnalysedSprint
from .absorb import Pattern

NOTHING_YET = "Nothing remembered yet: memory is written from a Sprint's retro analysis."

_MET = {None: "not marked", True: "met", False: "not met"}


def _count(count: int) -> str:
    return f"{count} Sprint{'' if count == 1 else 's'}"


def memory_text(patterns: Sequence[Pattern], last: AnalysedSprint | None) -> str:
    """The patterns in three lists, strongest first, then the last analysed Sprint."""
    if not patterns and last is None:
        return NOTHING_YET
    lines: list[str] = []
    raised = [pattern for pattern in patterns if pattern.raised and not pattern.lowered]
    lowered = [pattern for pattern in patterns if pattern.lowered and not pattern.raised]
    split = [pattern for pattern in patterns if pattern.raised and pattern.lowered]
    for title, ours in (
        ("Patterns — what raised the day's rating, and in how many Sprints it showed", raised),
        ("Patterns — what lowered it", lowered),
    ):
        if ours:
            lines.append(title)
            lines += [f"- {pattern.text} ({_count(pattern.sprints)})" for pattern in ours]
    if split:
        lines.append("Patterns — what raised it in some Sprints and lowered it in others")
        lines += [
            f"- {pattern.text} (raised it in {_count(len(pattern.raised))}, "
            f"lowered it in {_count(len(pattern.lowered))})"
            for pattern in split
        ]
    if last is not None:
        analysis = last.analysis
        if lines:
            lines.append("")
        lines += [
            f"Last analysed Sprint {last.number}, ended {last.last.isoformat()}, "
            f"Success criteria {_MET[last.met]}",
            analysis["headline"],
            f"Experiment it set, result not checked: {analysis['experiment']}",
        ]
        if analysis.get("notable"):
            lines.append(f"Worth knowing: {'; '.join(analysis['notable'])}")
    return "\n".join(lines)
