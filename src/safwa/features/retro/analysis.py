"""The retro analysis: what the model makes of a Sprint's record and its Diary.

Small questions, each answered with one call: three over the record against the Sprints
before it, one per three days of the Diary, one per list of claims those returned, one
over the three confirmed lists together, and one that writes the analysis from what the
others found. Nothing here reads the database — the record and the Diary are handed in,
and the analysis is handed back.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Coroutine, Sequence
from datetime import date
from typing import Annotated, Any, Literal

from pydantic import Field

from llm_gateway import LlmProvider
from tg_agent_shell.ai.contracts import ToolInput
from tg_agent_shell.ai.mini import TerminalTool, run_mini_session

from ..planning.closing import Bucket, DayTally
from .use_cases import AnalysisInput, DiaryDay, SprintColumn

# How many days of the Diary one question to the model holds; the batches are asked at once.
ANALYSIS_DAY_BATCH = 3

# Every answer opens with the model thinking aloud, and this is how long it may.
VERBOSE_TOKENS = 500
VERBOSE = f"First: think over what you were given aloud, in at most {VERBOSE_TOKENS} tokens."

# What the analysis screen can carry and still be one Telegram message. The last call
# refuses more, so the screen is drawn from what fits rather than cut.
TRENDS_MAX = 6
ITEMS_MAX = 3
SENTENCE_CHARS = 200
METRIC_CHARS = 40
NOTE_CHARS = 120
ITEM_CHARS = 140
FACT_CHARS = 120

Reporter = Callable[[int, int, str], Awaitable[None]]


# ----------------------------------------------------------------- what the model answers

Trend = Literal["up", "flat", "down", "unclear"]


class Finding(ToolInput):
    metric: str = Field(max_length=METRIC_CHARS, description="What it is about, in a few words.")
    trend: Trend = Field(description="up, flat, down, or unclear.")
    note: str = Field(
        max_length=NOTE_CHARS, description="One short sentence with the numbers that say so."
    )


class OverviewVerdict(ToolInput):
    verbose_analyse: str = Field(description=VERBOSE)
    findings: list[Finding] = Field(
        max_length=TRENDS_MAX,
        description="At most 6 findings about this Sprint against the ones before it.",
    )


class DayClaim(ToolInput):
    claim: str = Field(description="One thing about the days, in at most 12 words.")
    days: list[str] = Field(description="The dates it rests on, as YYYY-MM-DD.")


class DayBatchFindings(ToolInput):
    verbose_analyse: str = Field(description=VERBOSE)
    helped: list[DayClaim] = Field(
        description="What likely raised a day's rating. Empty when nothing did."
    )
    hurt: list[DayClaim] = Field(
        description="What likely lowered a day's rating. Empty when nothing did."
    )
    unrelated: list[DayClaim] = Field(
        description="What likely had nothing to do with the rating. Empty when nothing stands out."
    )


class ClaimReview(ToolInput):
    verbose_analyse: str = Field(description=VERBOSE)
    confirmed: list[str] = Field(
        description="Claims said twice, backed by another claim, or resting on two or more days, "
        "each merged into one wording."
    )
    contradicted: list[str] = Field(description="Claims whose opposite is also in the list.")
    single: list[str] = Field(description="Claims resting on one day, backed by nothing.")


class SamePair(ToolInput):
    one: int = Field(description="The number of a claim.")
    other: int = Field(
        description="The number of the claim in another list that is the same thing."
    )


class CrossReview(ToolInput):
    verbose_analyse: str = Field(description=VERBOSE)
    same: list[SamePair] = Field(
        description="Every pair of claims that is one and the same thing in two different lists, "
        "in the same or other words. Empty when there is none."
    )


Item = Annotated[str, Field(max_length=ITEM_CHARS)]


class SprintAnalysis(ToolInput):
    verbose_analyse: str = Field(description=VERBOSE)
    headline: str = Field(
        max_length=SENTENCE_CHARS, description="One sentence: how the Sprint went."
    )
    dynamics: list[Finding] = Field(
        max_length=TRENDS_MAX, description="At most 6 trends, from the overview findings."
    )
    helped: list[Item] = Field(
        max_length=ITEMS_MAX,
        description="At most 3 things that raised the day's rating, from the confirmed claims only.",
    )
    hurt: list[Item] = Field(
        max_length=ITEMS_MAX,
        description="At most 3 things that lowered the day's rating, from the confirmed claims only.",
    )
    noise: list[Item] = Field(
        max_length=ITEMS_MAX,
        description="At most 3 things that had nothing to do with it, from the confirmed claims only.",
    )
    experiment: str = Field(
        max_length=SENTENCE_CHARS, description="One sentence: one thing to try in the next Sprint."
    )
    memory_fact: str | None = Field(
        default=None,
        max_length=FACT_CHARS,
        description="One durable fact about the user this Sprint showed, at most 120 characters, or null.",
    )


OVERVIEW_TOOL = TerminalTool(
    "overview_verdict", "Your findings over the Sprints you were given.", OverviewVerdict
)
DAYS_TOOL = TerminalTool(
    "day_batch_findings",
    "What the days you were given say about the day's rating.",
    DayBatchFindings,
)
REVIEW_TOOL = TerminalTool("claim_review", "Every claim sorted.", ClaimReview)
CROSS_TOOL = TerminalTool(
    "cross_review", "The claims that stand in two lists at once.", CrossReview
)
ANALYSIS_TOOL = TerminalTool("sprint_analysis", "The analysis of the Sprint.", SprintAnalysis)

OVERVIEW_PROMPT = """You compare a Sprint with the Sprints that ended before it.
Read the Sprints oldest first. The last one is this Sprint.
Call overview_verdict once. Fill verbose_analyse first.
Then give at most 6 findings: one metric each, its trend across the Sprints, and the numbers that say so.
Write note in the language the Success criteria are written in."""

DAYS_PROMPT = """You read three days of the user's Sprint: what they planned and finished, and their Diary.
A day's rating is 0-10, given by the user.
Call day_batch_findings once. Fill verbose_analyse first.
Then name what likely raised a day's rating, what likely lowered it, and what likely had nothing to do with it.
One claim is one thing in at most 12 words, with the dates it rests on. Leave a list empty when nothing fits.
Write claims in the language the Diary is written in."""

REVIEW_PROMPT = """You are given claims gathered from different three-day batches of one Sprint, each with the days it rests on.
Call claim_review once. Fill verbose_analyse first.
Then sort every claim into one list:
confirmed - said twice, backed by another claim, or resting on two or more days; merge those into one wording.
contradicted - its opposite is also in the list.
single - rests on one day and nothing backs it.
Keep the language the claims are written in."""

CROSS_PROMPT = """You are given the confirmed claims about one Sprint, numbered, each in one of three lists: what raised the day's rating, what lowered it, what had nothing to do with it.
Call cross_review once. Fill verbose_analyse first.
Then name every pair of numbers that is one and the same thing standing in two different lists, in the same or other words.
Leave same empty when there is none."""

ANALYSIS_PROMPT = """You write the analysis of one Sprint from the findings and the confirmed claims you are given.
Call sprint_analysis once. Fill verbose_analyse first.
Then: headline in one sentence; at most 6 trends from the findings; at most 3 things that raised the day's rating, at most 3 that lowered it, at most 3 that had nothing to do with it, from the confirmed claims only; one experiment for the next Sprint in one sentence; one durable fact about the user this Sprint showed, or null when it showed none.
Write in the language the claims and the Success criteria are written in."""

# The three lists of claims, in the order every round names them.
LISTS = (
    ("helped", "raised the day's rating"),
    ("hurt", "lowered the day's rating"),
    ("unrelated", "had nothing to do with the day's rating"),
)


# ------------------------------------------------------------------------------ the run


class _Steps:
    """The run's progress: one step per answer, reported as each arrives."""

    def __init__(self, total: int, report: Reporter) -> None:
        self.total = total
        self.done = 0
        self.report = report

    async def advance(self, note: str) -> None:
        self.done += 1
        await self.report(self.done, self.total, note)


class SprintAnalyst:
    """The analysis, on the one provider the application has."""

    def __init__(self, provider: LlmProvider) -> None:
        self.provider = provider

    async def analyse(self, given: AnalysisInput, *, report: Reporter) -> dict[str, Any]:
        """Run the questions and return the analysis as the Sprint row keeps it."""
        batches = day_batches(given)
        steps = _Steps(3 + len(batches) + 3 + 1 + 1, report)
        await report(0, steps.total, "")

        async def overview(text: str, note: str) -> OverviewVerdict:
            verdict = await self._ask(OVERVIEW_PROMPT, text, OVERVIEW_TOOL)
            await steps.advance(note)
            return verdict

        async def days(batch: Sequence[tuple[DayTally, DiaryDay | None]]) -> DayBatchFindings:
            found = await self._ask(DAYS_PROMPT, days_text(batch), DAYS_TOOL)
            await steps.advance(f"Diary {_short(batch[0][0].day)} – {_short(batch[-1][0].day)}")
            return found

        answers = await _at_once(
            overview(overview_text(given.sprints), "the Sprints"),
            overview(
                shares_text(given.sprints, "Category", "Categories", "by_category"), "Categories"
            ),
            overview(
                shares_text(given.sprints, "Energy type", "Energy types", "by_energy"),
                "Energy types",
            ),
            *(days(batch) for batch in batches),
        )
        verdicts: list[OverviewVerdict] = answers[:3]
        found: list[DayBatchFindings] = answers[3:]
        findings = [finding for verdict in verdicts for finding in verdict.findings]

        async def review(name: str, about: str) -> ClaimReview:
            claims = [claim for batch in found for claim in getattr(batch, name)]
            if claims:
                reviewed = await self._ask(REVIEW_PROMPT, claims_text(claims, about), REVIEW_TOOL)
            else:
                reviewed = ClaimReview(verbose_analyse="", confirmed=[], contradicted=[], single=[])
            await steps.advance(f"reviewing what {about}")
            return reviewed

        reviews = await _at_once(*(review(name, about) for name, about in LISTS))
        confirmed = [review.confirmed for review in reviews]

        # A claim confirmed in two lists at once proves nothing and leaves both. The model
        # names the numbers, and the code takes the claims out.
        dropped: list[str] = []
        if sum(1 for claims in confirmed if claims) >= 2:
            crossed: CrossReview = await self._ask(CROSS_PROMPT, cross_text(confirmed), CROSS_TOOL)
            confirmed, dropped = without_same(confirmed, crossed.same)
        await steps.advance("reading the lists together")

        analysis: SprintAnalysis = await self._ask(
            ANALYSIS_PROMPT, synthesis_text(given.sprint, findings, confirmed), ANALYSIS_TOOL
        )
        await steps.advance("writing the analysis")
        return {
            **analysis.model_dump(exclude={"verbose_analyse"}),
            "dropped": dropped,
            "compared": [column.number for column in given.sprints[:-1]],
        }

    async def _ask(self, prompt: str, context: str, terminal: TerminalTool) -> Any:
        result = await run_mini_session(
            self.provider,
            system_prompt=prompt,
            context=context,
            terminals=(terminal,),
            max_tool_calls=None,
        )
        return result.payload


async def _at_once(*questions: Coroutine[Any, Any, Any]) -> list[Any]:
    """Every answer, asked at once. One that fails ends the others, and the failure is
    raised only once they have ended, so nothing keeps talking to the model, or to the
    progress note, after the run is over."""
    tasks = [asyncio.create_task(question) for question in questions]
    try:
        return await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


# ------------------------------------------------------------ what the model is handed


def day_batches(given: AnalysisInput) -> list[list[tuple[DayTally, DiaryDay | None]]]:
    """The Sprint's days in threes, each with the Diary of that day when there is one."""
    days = [
        (tally, given.diary.get(date.fromisoformat(tally.day)))
        for tally in given.sprint.statistics.days
    ]
    return [
        days[start : start + ANALYSIS_DAY_BATCH]
        for start in range(0, len(days), ANALYSIS_DAY_BATCH)
    ]


def _short(day: str) -> str:
    """An ISO date as the owner reads it in a note: DD.MM."""
    return f"{day[8:10]}.{day[5:7]}"


def _label(position: int, count: int) -> str:
    back = count - 1 - position
    return {0: "this Sprint", 1: "the Sprint before"}.get(back, f"{back} Sprints before")


def _met(met: bool | None) -> str:
    return {True: "yes", False: "no"}.get(met, "?")


def _heading(column: SprintColumn, position: int, count: int) -> str:
    days = (column.last - column.first).days + 1
    return (
        f"Sprint {column.number} — {_label(position, count)} "
        f"({column.first.isoformat()} – {column.last.isoformat()}, {days} days)"
    )


def overview_text(columns: Sequence[SprintColumn]) -> str:
    lines = []
    if len(columns) == 1:
        lines.append("No Sprint ended before this one: describe this Sprint on its own.")
    for position, column in enumerate(columns):
        stats = column.statistics
        lines += [
            _heading(column, position, len(columns)),
            f"- Actions: finished {stats.finished} of {stats.planned} taken in; "
            f"{stats.remaining} still open, {stats.blocked} of them blocked",
            f"- Effort: finished {stats.done:g} of {stats.taken:g} EP ({stats.done_share}%); "
            f"added {stats.added:g} EP, taken out {stats.removed:g} EP",
            f'- Success criteria: "{column.criteria}" — met: {_met(column.met)}; '
            f"key Actions finished {stats.key_finished} of {stats.key_total}"
            + (
                f"; {stats.key_unknown} Actions not yet told key or not"
                if stats.key_unknown
                else ""
            ),
        ]
    return "\n".join(lines)


def shares_text(columns: Sequence[SprintColumn], kind: str, kinds: str, buckets: str) -> str:
    lines = [
        f"Shares of effort (EP) and of Actions (n) by {kind}: taken into the Sprint, and finished.",
        f"Within one Sprint the shares of one kind add up to 1.00; an Action with two {kinds} is in both.",
    ]
    for position, column in enumerate(columns):
        stats = column.statistics
        by_bucket: dict[str, Bucket] = getattr(stats, buckets)
        lines.append(
            f"{_heading(column, position, len(columns))}: taken in {stats.taken:g} EP, "
            f"{stats.planned} Actions; finished {stats.done:g} EP, {stats.finished} Actions"
        )
        sums = [
            sum(bucket.effort for bucket in by_bucket.values()),
            sum(bucket.count for bucket in by_bucket.values()),
            sum(bucket.done_effort for bucket in by_bucket.values()),
            sum(bucket.done_count for bucket in by_bucket.values()),
        ]
        for name, bucket in by_bucket.items():
            taken = f"EP {_share(bucket.effort, sums[0])} n {_share(bucket.count, sums[1])}"
            done = (
                f"EP {_share(bucket.done_effort, sums[2])} n {_share(bucket.done_count, sums[3])}"
            )
            lines.append(f"- {name}: taken in {taken} · finished {done}")
    return "\n".join(lines)


def _share(part: float, whole: float) -> str:
    return f"{part / whole:.2f}" if whole else "0.00"


def _counts(counts: dict[str, int]) -> str:
    return ", ".join(f"{name} {count}" for name, count in counts.items()) or "none"


def days_text(batch: Sequence[tuple[DayTally, DiaryDay | None]]) -> str:
    lines = []
    for tally, entry in batch:
        rating = f"rating {entry.score} of 10" if entry and entry.score is not None else "not rated"
        lines += [
            f"Day {tally.day} — {rating}",
            f"- Actions: {tally.planned} in Today that morning, {tally.done} finished; "
            f"finished by Category: {_counts(tally.done_by_category)}; "
            f"by Energy type: {_counts(tally.done_by_energy)}",
            f"- Diary: {entry.body if entry else 'nothing written'}",
        ]
    return "\n".join(lines)


def claims_text(claims: Sequence[DayClaim], about: str) -> str:
    lines = [f"Claims about what {about}:"]
    lines += [
        f"{number}. {claim.claim} — days {', '.join(claim.days) or 'not named'}"
        for number, claim in enumerate(claims, start=1)
    ]
    return "\n".join(lines)


Numbered = list[tuple[int, int, str]]


def _numbered(confirmed: Sequence[Sequence[str]]) -> Numbered:
    """Every confirmed claim numbered from 1 across the three lists: number, list, claim."""
    numbered: Numbered = []
    for which, claims in enumerate(confirmed):
        for claim in claims:
            numbered.append((len(numbered) + 1, which, claim))
    return numbered


def cross_text(confirmed: Sequence[Sequence[str]]) -> str:
    lines = ["Confirmed claims, numbered; the list each is in stands in brackets:"]
    lines += [
        f"{number}. [{LISTS[which][1]}] {claim}" for number, which, claim in _numbered(confirmed)
    ]
    return "\n".join(lines)


def without_same(
    confirmed: Sequence[Sequence[str]], same: Sequence[SamePair]
) -> tuple[list[list[str]], list[str]]:
    """The lists without the claims the model paired across two of them, and those claims.

    A pair inside one list, or one naming a number that is not there, is no contradiction
    and changes nothing.
    """
    numbered = _numbered(confirmed)
    list_of = {number: which for number, which, _claim in numbered}
    gone: set[int] = set()
    for pair in same:
        if {pair.one, pair.other} <= set(list_of) and list_of[pair.one] != list_of[pair.other]:
            gone |= {pair.one, pair.other}
    kept = [
        [claim for number, which, claim in numbered if which == this and number not in gone]
        for this in range(len(confirmed))
    ]
    dropped = list(dict.fromkeys(claim for number, _which, claim in numbered if number in gone))
    return kept, dropped


def synthesis_text(
    column: SprintColumn, findings: Sequence[Finding], confirmed: Sequence[Sequence[str]]
) -> str:
    lines = [
        f"Sprint {column.number} ({column.first.isoformat()} – {column.last.isoformat()}). "
        f'Success criteria: "{column.criteria}" — met: {_met(column.met)}.',
        "Overview findings, this Sprint against the ones before it:",
        *(
            [f"- [{finding.trend}] {finding.metric}: {finding.note}" for finding in findings]
            or ["- none"]
        ),
    ]
    for claims, (_name, about) in zip(confirmed, LISTS, strict=True):
        lines.append(f"Confirmed claims about what {about}:")
        lines += [f"- {claim}" for claim in claims] or ["- nothing confirmed"]
    return "\n".join(lines)
