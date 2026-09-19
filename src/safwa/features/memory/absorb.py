"""How one Sprint's analysis is taken into what Safwa remembers, and what memory shows of it.

An observation is what one Sprint's analysis said: a claim as it worded it, and whether it
raised the day's rating or lowered it. A pattern is the observations, across Sprints, that
name one and the same thing; the row it has is the identity they share. Taking a Sprint in
replaces its own observations and no other Sprint's: a claim it made last time keeps its
pattern without a question, a new one is matched against the patterns by the model, and one
it no longer makes is gone. So taking a Sprint in again gives the same memory, a Sprint that
ended earlier but was analysed later adds its evidence where it belongs, and a corrected
analysis gives back what the wrong one took away.

What memory shows is a selection over the observations of the Sprints taken in, made anew
each time it is read: a pattern one Sprint observed that the next `MEMORY_UNCONFIRMED_SPRINTS`
Sprints taken in did not is left out, and over `MEMORY_PATTERNS_MAX` the weakest go first.
Nothing is deleted for it. The model only says which candidate is which pattern; everything
written is decided here, so an answer that cannot be read leaves memory as it was.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from llm_gateway import LlmProvider
from tg_agent_shell.ai.mini import run_mini_session

from ..retro.api import AnalysedSprint
from .agent import PATTERN_PROMPT, PATTERN_TOOL, Pair, PatternReview

# How many Sprints may be taken in after the one Sprint that observed a pattern before it
# is left out of what memory shows.
MEMORY_UNCONFIRMED_SPRINTS = 3
# How many patterns memory shows at most; over it, the weakest go.
MEMORY_PATTERNS_MAX = 20


@dataclass(frozen=True, slots=True)
class Observation:
    """What one Sprint's analysis said of one pattern."""

    sprint_id: int
    # None until it is written: a pattern of its own, started by this observation.
    pattern_id: int | None
    text: str
    raises: bool


@dataclass(frozen=True, slots=True)
class Pattern:
    """A pattern as memory shows it: worded as the earliest Sprint observing it said it,
    with the numbers of the Sprints that saw it raise the day's rating and of those that
    saw it lower it, each in the order they ended."""

    id: int
    text: str
    raised: tuple[str, ...]
    lowered: tuple[str, ...]

    @property
    def sprints(self) -> int:
        """How many Sprints observed it; one that saw both effects counts once."""
        return len(set(self.raised) | set(self.lowered))


@dataclass(frozen=True, slots=True)
class Candidate:
    text: str
    raises: bool


Matcher = Callable[[Sequence[Pattern], Sequence[Candidate]], Awaitable[PatternReview]]


def candidates(analysis: dict[str, Any]) -> list[Candidate]:
    """What the analysis confirmed raised the day's rating, then what lowered it, each once."""
    found = [Candidate(item, True) for item in analysis.get("helped", ())] + [
        Candidate(item, False) for item in analysis.get("hurt", ())
    ]
    return list(dict.fromkeys(found))


def active(observations: Sequence[Observation], taken: Sequence[AnalysedSprint]) -> list[Pattern]:
    """The patterns memory shows, strongest first — more Sprints, then the latest of them —
    from the observations of the Sprints `taken` in, given in the order they ended.

    An observation of any other Sprint counts for nothing: its analysis is not in yet, or
    not any more. A Sprint not yet taken in is not a Sprint that failed to confirm."""
    position = {sprint.id: index for index, sprint in enumerate(taken)}
    number = {sprint.id: sprint.number for sprint in taken}
    grouped: dict[int, list[Observation]] = {}
    for observation in observations:
        if observation.sprint_id in position and observation.pattern_id is not None:
            grouped.setdefault(observation.pattern_id, []).append(observation)
    ranked: list[tuple[tuple[int, int, int], Pattern]] = []
    for pattern_id, ours in grouped.items():
        ours.sort(key=lambda observation: (position[observation.sprint_id], observation.text))
        seen = sorted({position[observation.sprint_id] for observation in ours})
        if len(seen) == 1 and len(taken) - 1 - seen[0] >= MEMORY_UNCONFIRMED_SPRINTS:
            continue
        pattern = Pattern(
            pattern_id,
            ours[0].text,
            tuple(dict.fromkeys(number[o.sprint_id] for o in ours if o.raises)),
            tuple(dict.fromkeys(number[o.sprint_id] for o in ours if not o.raises)),
        )
        ranked.append(((-len(seen), -seen[-1], pattern_id), pattern))
    ranked.sort(key=lambda item: item[0])
    return [pattern for _, pattern in ranked[:MEMORY_PATTERNS_MAX]]


def review_text(patterns: Sequence[Pattern], found: Sequence[Candidate]) -> str:
    lines = ["Patterns, numbered:"]
    lines += [f"{number}. {pattern.text}" for number, pattern in enumerate(patterns, start=1)]
    lines.append("Candidates, numbered:")
    lines += [f"{number}. {candidate.text}" for number, candidate in enumerate(found, start=1)]
    return "\n".join(lines)


def matched(
    pairs: Sequence[Pair], patterns: Sequence[Pattern], found: Sequence[Candidate]
) -> dict[int, int]:
    """Candidate index to pattern index, for the pairs that name numbers there are; the
    first pair about a candidate counts."""
    chosen: dict[int, int] = {}
    for pair in pairs:
        candidate, pattern = pair.candidate - 1, pair.pattern - 1
        if 0 <= candidate < len(found) and 0 <= pattern < len(patterns):
            chosen.setdefault(candidate, pattern)
    return chosen


async def absorb(
    observations: Sequence[Observation],
    sprint: AnalysedSprint,
    taken: Sequence[AnalysedSprint],
    match: Matcher,
) -> list[Observation]:
    """This Sprint's observations, from its analysis: a claim it made last time keeps its
    pattern, and a new one is matched against what the other Sprints taken in show.

    The other Sprints alone: this one is not counted among those that could have confirmed
    a pattern before it, so a pattern one Sprint observed is still there for it to confirm."""
    before = {
        (observation.text, observation.raises): observation.pattern_id
        for observation in observations
        if observation.sprint_id == sprint.id
    }
    others = [observation for observation in observations if observation.sprint_id != sprint.id]
    known = active(others, [item for item in taken if item.id != sprint.id])
    found = candidates(sprint.analysis)
    new = [candidate for candidate in found if (candidate.text, candidate.raises) not in before]
    where: dict[Candidate, int] = {}
    if known and new:
        review = await match(known, new)
        where = {
            new[index]: known[pattern].id for index, pattern in matched(review.same, known, new).items()
        }
    return [
        Observation(
            sprint.id,
            before.get((candidate.text, candidate.raises), where.get(candidate)),
            candidate.text,
            candidate.raises,
        )
        for candidate in found
    ]


class PatternReviewer:
    """The one question, on the one provider the application has."""

    def __init__(self, provider: LlmProvider) -> None:
        self.provider = provider

    async def __call__(
        self, patterns: Sequence[Pattern], found: Sequence[Candidate]
    ) -> PatternReview:
        result = await run_mini_session(
            self.provider,
            system_prompt=PATTERN_PROMPT,
            context=review_text(patterns, found),
            terminals=(PATTERN_TOOL,),
            max_tool_calls=None,
        )
        return result.payload
