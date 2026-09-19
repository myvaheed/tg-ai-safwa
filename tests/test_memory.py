"""Memory: what the retro leaves, how it accumulates, and the poll that writes it."""

from __future__ import annotations

import html
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from ui_harness import FakeMessage, services_for

from safwa.features.cards.use_cases import create_card
from safwa.features.memory.absorb import (
    MEMORY_PATTERNS_MAX,
    MEMORY_UNCONFIRMED_SPRINTS,
    Candidate,
    Observation,
    Pattern,
    absorb,
    active,
    matched,
    review_text,
)
from safwa.features.memory.agent import PATTERN_PROMPT, Pair, PatternReview
from safwa.features.memory.background import MEMORY_RETRO_INTERVAL_SECONDS
from safwa.features.memory.model import MemoryObservation, MemoryPattern
from safwa.features.memory.render import NOTHING_YET, memory_text
from safwa.features.memory.telegram import command_memory
from safwa.features.memory.use_cases import (
    AbsorbResult,
    MemoryReader,
    absorb_due,
    read_observations,
    remembered,
    taken_in,
)
from safwa.features.planning.model import Sprint
from safwa.features.planning.use_cases import finish_sprint, start_sprint
from safwa.features.retro.analysis import ITEM_CHARS, SENTENCE_CHARS
from safwa.features.retro.api import AnalysedSprint, analysed_sprints
from safwa.features.retro.use_cases import record_analysis
from telegram_llm.text import TELEGRAM_TEXT_LIMIT
from tg_agent_shell.foundation.clock import utcnow
from tg_agent_shell.foundation.kinds import MARKS, MessageKind
from tg_agent_shell.turn import TurnManager

WALK = "утренняя прогулка поднимает день"
MEETINGS = "три встречи подряд"
BLOCK = "один большой блок работы до обеда"
LATE = "поздний отбой"


def _analysis(helped=(), hurt=(), **rest) -> dict:
    return {
        "headline": rest.get("headline", "Лучший Спринт из трёх."),
        "dynamics": [],
        "helped": list(helped),
        "hurt": list(hurt),
        "noise": ["погода"],
        "experiment": rest.get("experiment", "Не больше двух встреч в день."),
        "notable": list(rest.get("notable", ())),
        "dropped": [],
        "compared": [],
        "met": rest.get("met"),
        "analysed_at": utcnow().isoformat(),
    }


def _sprint(number: str, analysis: dict | None = None, *, last: date | None = None) -> AnalysedSprint:
    last = last or date(2026, 9, 18)
    return AnalysedSprint(
        id=int(number.replace(".", "").replace("-", "")),
        number=number,
        first=last - timedelta(days=13),
        last=last,
        analysis=analysis or _analysis(),
        memory_at=None,
    )


async def _match_none(patterns, found) -> PatternReview:
    return PatternReview(verbose_analyse="", same=[])


async def _match_same_text(patterns, found) -> PatternReview:
    """The model with no judgement: the same words are the same thing."""
    return PatternReview(
        verbose_analyse="",
        same=[
            Pair(candidate=index + 1, pattern=position + 1)
            for index, candidate in enumerate(found)
            for position, pattern in enumerate(patterns)
            if candidate.text == pattern.text
        ],
    )


def _written(observations: Sequence[Observation], start: int) -> list[Observation]:
    """What the rows would be: every observation of its own gets a pattern of its own."""
    written = []
    for observation in observations:
        if observation.pattern_id is None:
            observation = Observation(observation.sprint_id, start, observation.text, observation.raises)
            start += 1
        written.append(observation)
    return written


# ------------------------------------------------------------------ the patterns


async def test_mem_retro_013_a_pattern_is_what_the_sprints_observed_of_one_thing() -> None:
    """MEM-RETRO-013 — tests/brd/memory.feature"""
    august, september = _sprint("25.08-02", last=date(2026, 8, 21)), _sprint("25.09-01", last=date(2026, 9, 4))
    held = [
        Observation(august.id, 1, WALK, True),
        Observation(september.id, 1, "прогулка утром", True),
        Observation(september.id, 2, MEETINGS, False),
        Observation(september.id, 3, LATE, False),
    ]
    this = _sprint(
        "25.09-02",
        _analysis(
            helped=["прогулка утром перед работой", BLOCK, LATE], hurt=["встречи весь день", BLOCK]
        ),
    )
    shown: list[list[str]] = []

    async def match(patterns, found) -> PatternReview:
        shown.append([pattern.text for pattern in patterns])
        return PatternReview(
            verbose_analyse="",
            same=[Pair(candidate=1, pattern=1), Pair(candidate=4, pattern=2), Pair(candidate=3, pattern=3)],
        )

    ours = await absorb(held, this, [august, september], match)

    # The model read the patterns strongest first, worded as the earliest Sprint said them.
    assert shown == [[WALK, MEETINGS, LATE]]
    # Each claim is an observation of this Sprint's: the same thing, with either effect, is an
    # observation of that pattern; a thing no pattern is about starts one of its own.
    assert ours == [
        Observation(this.id, 1, "прогулка утром перед работой", True),
        Observation(this.id, None, BLOCK, True),
        Observation(this.id, 3, LATE, True),
        Observation(this.id, 2, "встречи весь день", False),
        Observation(this.id, None, BLOCK, False),
    ]

    patterns = active(held + _written(ours, 4), [august, september, this])
    assert patterns[0] == Pattern(1, WALK, ("25.08-02", "25.09-01", "25.09-02"), ())
    assert patterns[1] == Pattern(2, MEETINGS, (), ("25.09-01", "25.09-02"))
    # The opposite of what one Sprint observed is the pattern with Sprints on both sides.
    assert patterns[2] == Pattern(3, LATE, ("25.09-02",), ("25.09-01",))
    assert patterns[3] == Pattern(4, BLOCK, ("25.09-02",), ())
    assert patterns[4] == Pattern(5, BLOCK, (), ("25.09-02",))
    # A pattern is counted by Sprints, and a Sprint that saw both effects is one Sprint.
    assert patterns[2].sprints == 2 and patterns[0].sprints == 3
    assert Pattern(9, BLOCK, ("25.09-02",), ("25.09-02",)).sprints == 1
    # Nothing that had nothing to do with the day's rating is a candidate.
    assert "погода" not in [observation.text for observation in ours]


async def test_mem_retro_016_the_model_matches_and_the_code_decides_what_a_match_is() -> None:
    """MEM-RETRO-016 — tests/brd/memory.feature"""
    patterns = [Pattern(1, WALK, ("25.09-01",), ())]
    found = [Candidate("прогулка", False), Candidate(BLOCK, True)]
    # Numbers that are not there count for nothing; the first pair about a candidate counts.
    pairs = [
        Pair(candidate=2, pattern=9),
        Pair(candidate=0, pattern=1),
        Pair(candidate=1, pattern=1),
        Pair(candidate=1, pattern=2),
    ]
    assert matched(pairs, patterns, found) == {0: 0}
    # What the model reads: both lists numbered from 1, the words alone.
    assert review_text(patterns, found) == "\n".join(
        ["Patterns, numbered:", f"1. {WALK}", "Candidates, numbered:", "1. прогулка", f"2. {BLOCK}"]
    )
    assert "same" in PATTERN_PROMPT and "opposite" not in PATTERN_PROMPT
    assert "effect may differ" in PATTERN_PROMPT

    # Asked once, only when there are patterns and new claims both.
    asked: list[int] = []

    async def match(patterns, found) -> PatternReview:
        asked.append(len(found))
        return PatternReview(verbose_analyse="", same=[])

    earlier = _sprint("25.09-01", last=date(2026, 9, 4))
    this = _sprint("25.09-02", _analysis(helped=["прогулка утром"], hurt=[MEETINGS]))
    held = [Observation(earlier.id, 1, WALK, True)]
    assert await absorb([], this, [], match) == [
        Observation(this.id, None, "прогулка утром", True),
        Observation(this.id, None, MEETINGS, False),
    ]
    assert await absorb(held, _sprint("25.10-01"), [earlier], match) == []
    already = [Observation(this.id, 1, "прогулка утром", True), Observation(this.id, 2, MEETINGS, False)]
    assert await absorb(held + already, this, [earlier, this], match) == already
    assert asked == []
    await absorb(held, this, [earlier], match)
    assert asked == [2]


def test_mem_retro_014_a_pattern_nobody_confirms_is_left_out() -> None:
    """MEM-RETRO-014 — tests/brd/memory.feature"""
    order = [_sprint(number) for number in ("25.08-01", "25.08-02", "25.09-01", "25.09-02", "25.10-01")]
    one, two, three, four, five = (sprint.id for sprint in order)
    observations = [
        Observation(one, 1, "old alone", True),
        Observation(three, 2, "young alone", True),
        Observation(one, 3, "old but twice", False),
        Observation(two, 3, "old but twice", False),
        Observation(five, 4, "newest alone", False),
    ]

    kept = active(observations, order)

    # One Sprint, and MEMORY_UNCONFIRMED_SPRINTS taken in after it: left out.
    assert MEMORY_UNCONFIRMED_SPRINTS == 3
    assert [pattern.text for pattern in kept] == ["old but twice", "newest alone", "young alone"]
    # A Sprint not yet taken in is none of the three: with only two taken in, it stands.
    assert [pattern.text for pattern in active(observations, order[:2])] == [
        "old but twice",
        "old alone",
    ]
    # An observation of a Sprint not taken in counts for nothing.
    assert active(observations, order[:2])[0].lowered == ("25.08-01", "25.08-02")

    # Over the ceiling the weakest go: fewest Sprints first, then the oldest.
    many = [
        Observation(order[3 + index % 2].id, 10 + index, f"p{index}", True)
        for index in range(MEMORY_PATTERNS_MAX + 5)
    ]
    strong = [Observation(one, 9, "strong", True), Observation(five, 9, "strong", True)]
    kept = active([*many, *strong], order)
    assert len(kept) == MEMORY_PATTERNS_MAX == 20
    assert kept[0] == Pattern(9, "strong", ("25.08-01", "25.10-01"), ())
    assert [pattern.raised for pattern in kept[1:]] == [("25.10-01",)] * 12 + [("25.09-02",)] * 7
    # The Sprints are read in the order they ended, whatever order the rows were written in.
    reversed_rows = [Observation(five, 1, "x", True), Observation(two, 1, "x", True)]
    assert active(reversed_rows, order)[0] == Pattern(1, "x", ("25.08-02", "25.10-01"), ())


async def test_mem_retro_015_taking_a_sprint_in_again_gives_the_same_memory() -> None:
    """MEM-RETRO-015 — tests/brd/memory.feature"""
    earlier = _sprint("25.09-01", _analysis(helped=[WALK]), last=date(2026, 9, 4))
    this = _sprint("25.09-02", _analysis(helped=["прогулка утром"], hurt=[MEETINGS]))
    held = [Observation(earlier.id, 1, WALK, True)]
    asked: list[tuple[int, int]] = []

    async def match(patterns, found) -> PatternReview:
        asked.append((len(patterns), len(found)))
        return PatternReview(verbose_analyse="", same=[Pair(candidate=1, pattern=1)])

    once = _written(await absorb(held, this, [earlier], match), 2)
    assert once == [
        Observation(this.id, 1, "прогулка утром", True),
        Observation(this.id, 2, MEETINGS, False),
    ]
    # The second time its claims are the ones it made: no question, the same rows.
    twice = await absorb(held + once, this, [earlier, this], match)
    assert twice == once
    assert asked == [(1, 2)]

    # Corrected: the claim it no longer makes is gone, the one it still makes keeps its pattern.
    corrected = _sprint("25.09-02", _analysis(hurt=[MEETINGS]))
    assert await absorb(held + once, corrected, [earlier, this], match) == [
        Observation(this.id, 2, MEETINGS, False)
    ]
    assert asked == [(1, 2)]
    restored = held + [Observation(this.id, 2, MEETINGS, False)]
    assert active(restored, [earlier, this]) == [
        Pattern(2, MEETINGS, (), ("25.09-02",)),
        Pattern(1, WALK, ("25.09-01",), ()),
    ]


async def test_mem_retro_011_a_pattern_is_counted_by_its_sprints_whichever_came_in_first() -> None:
    """MEM-RETRO-011 — tests/brd/memory.feature"""
    a = _sprint("25.08-01", _analysis(helped=[WALK]), last=date(2026, 8, 7))
    b = _sprint("25.08-02", _analysis(helped=[WALK]), last=date(2026, 8, 21))
    c = _sprint("25.09-01", _analysis(hurt=[WALK]), last=date(2026, 9, 4))

    async def taken(order: Sequence[AnalysedSprint]) -> list[Pattern]:
        held: list[Observation] = []
        done: list[AnalysedSprint] = []
        for sprint in order:
            ours = await absorb(held, sprint, done, _match_same_text)
            held += _written(ours, len(held) + 1)
            done = sorted([*done, sprint], key=lambda item: item.last)
        return active(held, done)

    # The Sprint that ended first analysed last: the same two Sprints on one side, one on the other.
    assert await taken([a, b, c]) == await taken([b, c, a]) == [
        Pattern(1, WALK, ("25.08-01", "25.08-02"), ("25.09-01",))
    ]


def test_mem_retro_015_the_last_analysed_sprint_is_read_whole() -> None:
    """MEM-RETRO-015 — tests/brd/memory.feature"""
    patterns = [
        Pattern(1, WALK, ("25.08-02", "25.09-01", "25.09-02"), ()),
        Pattern(3, MEETINGS, (), ("25.09-01", "25.09-02")),
        Pattern(4, LATE, ("25.09-02",), ("25.08-02", "25.09-01")),
        Pattern(2, BLOCK, ("25.09-02",), ()),
    ]
    last = _sprint("25.09-02", _analysis(notable=["переезд 10.09"], met=True))

    text = memory_text(patterns, last)

    assert text == "\n".join(
        [
            "Patterns — what raised the day's rating, and in how many Sprints it showed",
            f"- {WALK} (3 Sprints)",
            f"- {BLOCK} (1 Sprint)",
            "Patterns — what lowered it",
            f"- {MEETINGS} (2 Sprints)",
            "Patterns — what raised it in some Sprints and lowered it in others",
            f"- {LATE} (raised it in 1 Sprint, lowered it in 2 Sprints)",
            "",
            "Last analysed Sprint 25.09-02, ended 2026-09-18, Success criteria met",
            "Лучший Спринт из трёх.",
            "Experiment it set, result not checked: Не больше двух встреч в день.",
            "Worth knowing: переезд 10.09",
        ]
    )
    # Nothing of the Sprint's numbers, its noise or the mark as it stands now reaches memory.
    assert "погода" not in text and "dynamics" not in text
    assert memory_text([], None) == NOTHING_YET
    assert memory_text([], _sprint("25.09-02")).startswith("Last analysed Sprint 25.09-02")
    assert "not marked" in memory_text([], _sprint("25.09-02"))


# ------------------------------------------------------------------ the poll


async def _ended(sessions, criteria: str, *, ended_days_ago: int) -> int:
    async with sessions() as session:
        await create_card(session, kind="action", title=criteria, stage="sprint", effort_points=1)
        sprint = await start_sprint(session, success_criteria=criteria)
        sprint.actual_started_at = utcnow() - timedelta(days=ended_days_ago + 3)
        await finish_sprint(session)
        sprint.actual_ended_at = utcnow() - timedelta(days=ended_days_ago)
        await session.commit()
        return sprint.id


async def _analysed(sessions, sprint_id: int, analysis: dict) -> None:
    async with sessions() as session:
        await record_analysis(session, sprint_id, analysis)
        await session.commit()


def _runner(turn: TurnManager):
    return turn.run_background


async def _rows(sessions) -> list[tuple[int, int, str, bool]]:
    async with sessions() as session:
        rows = await session.scalars(select(MemoryObservation).order_by(MemoryObservation.id))
        return [(row.sprint_id, row.pattern_id, row.text, row.raises) for row in rows]


async def _patterns(sessions) -> list[Pattern]:
    async with sessions() as session:
        return active(await read_observations(session), taken_in(await analysed_sprints(session)))


async def test_mem_retro_011_an_analysed_sprint_reaches_memory_on_its_own(sessions) -> None:
    """MEM-RETRO-011 — tests/brd/memory.feature"""
    turn = TurnManager()
    assert MEMORY_RETRO_INTERVAL_SECONDS == 60
    assert await absorb_due(_match_none, sessions, run_background=_runner(turn)) == AbsorbResult.NOTHING

    first = await _ended(sessions, "First", ended_days_ago=20)
    second = await _ended(sessions, "Second", ended_days_ago=2)
    await _analysed(sessions, second, _analysis(helped=["прогулка утром"], hurt=[MEETINGS]))
    await _analysed(sessions, first, _analysis(helped=[WALK]))
    at = datetime(2026, 9, 19, 10, 0, tzinfo=UTC)

    # The oldest analysis owed goes first.
    assert await absorb_due(_match_none, sessions, run_background=_runner(turn), now=at) == (
        AbsorbResult.ABSORBED
    )
    async with sessions() as session:
        by_id = {sprint.id: sprint for sprint in await analysed_sprints(session)}
    assert await _rows(sessions) == [(first, 1, WALK, True)]
    assert by_id[first].memory_at == at and by_id[second].memory_at is None

    assert await absorb_due(_match_none, sessions, run_background=_runner(turn)) == (
        AbsorbResult.ABSORBED
    )
    assert await absorb_due(_match_none, sessions, run_background=_runner(turn)) == (
        AbsorbResult.NOTHING
    )
    async with sessions() as session:
        assert all(sprint.memory_at is not None for sprint in await analysed_sprints(session))
        text = await remembered(session)
        assert len(list(await session.scalars(select(MemoryPattern)))) == 3
    assert len(await _rows(sessions)) == 3
    assert f"- {WALK} (1 Sprint)" in text and f"- {MEETINGS} (1 Sprint)" in text
    assert "Last analysed Sprint" in text and "Second" not in text
    assert (await MemoryReader(sessions).sync()).text == text


async def test_mem_retro_011_a_sprint_analysed_late_adds_its_evidence_where_it_belongs(
    sessions,
) -> None:
    """MEM-RETRO-011 — tests/brd/memory.feature"""
    turn = TurnManager()
    a = await _ended(sessions, "A", ended_days_ago=40)
    b = await _ended(sessions, "B", ended_days_ago=25)
    c = await _ended(sessions, "C", ended_days_ago=10)
    await _analysed(sessions, b, _analysis(helped=[WALK]))
    await _analysed(sessions, c, _analysis(hurt=[WALK]))
    for _ in range(2):
        assert await absorb_due(_match_same_text, sessions, run_background=_runner(turn)) == (
            AbsorbResult.ABSORBED
        )
    assert [pattern.sprints for pattern in await _patterns(sessions)] == [2]

    # The Sprint that ended first, analysed last, is taken in then, as its own evidence.
    await _analysed(sessions, a, _analysis(helped=[WALK]))
    assert await absorb_due(_match_same_text, sessions, run_background=_runner(turn)) == (
        AbsorbResult.ABSORBED
    )
    (pattern,) = await _patterns(sessions)
    async with sessions() as session:
        numbers = {sprint.id: sprint.number for sprint in await analysed_sprints(session)}
    assert pattern.raised == (numbers[a], numbers[b]) and pattern.lowered == (numbers[c],)
    assert f"- {WALK} (raised it in 2 Sprints, lowered it in 1 Sprint)" in (
        await MemoryReader(sessions).sync()
    ).text


async def test_mem_retro_014_a_sprint_not_yet_taken_in_counts_as_none_of_the_three(
    sessions,
) -> None:
    """MEM-RETRO-014 — tests/brd/memory.feature"""
    turn = TurnManager()
    ids = [await _ended(sessions, f"S{index}", ended_days_ago=60 - index * 10) for index in range(4)]
    await _analysed(sessions, ids[0], _analysis(helped=[WALK, "old alone"]))
    for sprint_id in ids[1:]:
        await _analysed(sessions, sprint_id, _analysis(helped=[WALK]))

    # Four analyses ready before any check: taking the first in sees no Sprint after it.
    for _ in range(4):
        assert await absorb_due(_match_same_text, sessions, run_background=_runner(turn)) == (
            AbsorbResult.ABSORBED
        )
    patterns = await _patterns(sessions)
    assert [(pattern.text, pattern.sprints) for pattern in patterns] == [(WALK, 4)]
    # Three Sprints taken in after it without it: left out, and its observation still there.
    assert ("old alone", True) in [(text, raises) for _s, _p, text, raises in await _rows(sessions)]


async def test_mem_retro_015_a_corrected_analysis_gives_back_what_the_wrong_one_took_away(
    sessions,
) -> None:
    """MEM-RETRO-015 — tests/brd/memory.feature"""
    turn = TurnManager()
    a = await _ended(sessions, "A", ended_days_ago=20)
    b = await _ended(sessions, "B", ended_days_ago=2)
    await _analysed(sessions, a, _analysis(helped=[WALK]))
    await _analysed(sessions, b, _analysis(hurt=[WALK]))
    asked: list[int] = []

    async def match(patterns, found) -> PatternReview:
        asked.append(len(found))
        return await _match_same_text(patterns, found)

    for _ in range(2):
        await absorb_due(match, sessions, run_background=_runner(turn))
    (pattern,) = await _patterns(sessions)
    assert (len(pattern.raised), len(pattern.lowered)) == (1, 1) and asked == [1]

    # The same analysis again: the same rows, and no question asked.
    rows = await _rows(sessions)
    await _analysed(sessions, b, _analysis(hurt=[WALK]))
    assert await absorb_due(match, sessions, run_background=_runner(turn)) == AbsorbResult.ABSORBED
    assert await _rows(sessions) == rows and asked == [1]

    # Corrected: B no longer says it, and A's observation stands as it was.
    await _analysed(sessions, b, _analysis(hurt=[MEETINGS]))
    assert await absorb_due(match, sessions, run_background=_runner(turn)) == AbsorbResult.ABSORBED
    async with sessions() as session:
        numbers = {sprint.id: sprint.number for sprint in await analysed_sprints(session)}
    assert await _patterns(sessions) == [
        Pattern(2, MEETINGS, (), (numbers[b],)),
        Pattern(1, WALK, (numbers[a],), ()),
    ]
    assert asked == [1, 1]
    assert [text for _s, _p, text, _r in await _rows(sessions)] == [WALK, MEETINGS]


async def test_mem_retro_012_absorbing_waits_its_turn_and_is_tried_until_it_is_done(
    sessions,
) -> None:
    """MEM-RETRO-012 — tests/brd/memory.feature"""
    turn = TurnManager()
    other = await _ended(sessions, "Other", ended_days_ago=30)
    await _analysed(sessions, other, _analysis(hurt=[MEETINGS]))
    assert await absorb_due(_match_none, sessions, run_background=_runner(turn)) == (
        AbsorbResult.ABSORBED
    )
    sprint_id = await _ended(sessions, "Ship", ended_days_ago=1)
    await _analysed(sessions, sprint_id, _analysis(helped=[WALK]))
    before = await _rows(sessions)

    async def failing(patterns, found) -> PatternReview:
        raise RuntimeError("the network is down")

    async def owed() -> bool:
        async with sessions() as session:
            return (await analysed_sprints(session))[-1].memory_at is None

    # A failed call: nothing written, the Sprint still owed, the failure raised to the poll.
    try:
        await absorb_due(failing, sessions, run_background=_runner(turn))
    except RuntimeError as error:
        assert "network" in str(error)
    else:
        raise AssertionError("the failure did not reach the poll")
    assert await owed() and await _rows(sessions) == before

    # The owner holds the turn: the work waits for the next poll.
    turn.begin(source_message_id=1)
    assert await absorb_due(_match_none, sessions, run_background=_runner(turn)) == AbsorbResult.BUSY
    turn.end(1)
    assert await owed()

    # The owner arrives while the model is answering: nothing is written.
    async def owner_arrives(patterns, found) -> PatternReview:
        turn.dialogue_revision += 1
        return PatternReview(verbose_analyse="", same=[])

    assert await absorb_due(owner_arrives, sessions, run_background=_runner(turn)) == AbsorbResult.BUSY
    assert await owed() and await _rows(sessions) == before

    # Done once, and stamped in the same transaction as the rows.
    assert await absorb_due(_match_none, sessions, run_background=_runner(turn)) == (
        AbsorbResult.ABSORBED
    )
    assert not await owed()
    assert [text for _s, _p, text, _r in await _rows(sessions)] == [MEETINGS, WALK]
    assert await absorb_due(_match_none, sessions, run_background=_runner(turn)) == (
        AbsorbResult.NOTHING
    )


def _shown(text: str) -> str:
    """A message's text as the owner reads it: no mark, no HTML entities."""
    return html.unescape(MARKS.read(text)[2])


async def test_mem_retro_010_memory_is_what_the_retro_left_and_the_owner_reads_the_same(
    sessions,
) -> None:
    """MEM-RETRO-010 — tests/brd/memory.feature"""
    services = services_for(sessions)
    message = FakeMessage(360, bot_message=True, answer_as_new=True)

    await command_memory(message, services)
    assert NOTHING_YET in _shown(message.edits[-1][0]) and message.edits[-1][1] is None

    sprint_id = await _ended(sessions, "Ship", ended_days_ago=1)
    await _analysed(sessions, sprint_id, _analysis(helped=[WALK], notable=["переезд"]))
    await absorb_due(_match_none, sessions, run_background=_runner(TurnManager()))
    async with sessions() as session:
        number = (await session.get(Sprint, sprint_id)).number

    await command_memory(message, services)
    text = _shown(message.edits[-1][0])
    assert f"- {WALK} (1 Sprint)" in text
    assert f"Last analysed Sprint {number}" in text and "Worth knowing: переезд" in text
    assert text.split("\n", 1)[1] == (await MemoryReader(sessions).sync()).text
    assert message.answers == []


async def test_mem_retro_010_memory_is_shown_whole_in_as_many_messages_as_it_takes(
    sessions,
) -> None:
    """MEM-RETRO-010 — tests/brd/memory.feature"""

    def item(index: int) -> str:
        return f"{index:02d} 🙂 A&B <c> ".ljust(ITEM_CHARS - 1, "я") + "🙂"

    # Everything at the limit the schema allows: five Sprints of three and three, the first
    # two saying the same, so that over MEMORY_PATTERNS_MAX patterns stand and the last
    # Sprint's block is at its own limits.
    for sprint in range(5):
        sprint_id = await _ended(sessions, f"S{sprint}", ended_days_ago=50 - sprint * 10)
        base = max(sprint - 1, 0) * 6
        await _analysed(
            sessions,
            sprint_id,
            _analysis(
                helped=[item(base + n) for n in range(3)],
                hurt=[item(base + 3 + n) for n in range(3)],
                headline="Заголовок <&> 🙂 ".ljust(SENTENCE_CHARS, "х"),
                experiment="Эксперимент <&> 🙂 ".ljust(SENTENCE_CHARS, "х"),
                notable=[item(90 + n) for n in range(3)],
            ),
        )
    turn = TurnManager()
    for _ in range(5):
        await absorb_due(_match_same_text, sessions, run_background=_runner(turn))
    expected = (await MemoryReader(sessions).sync()).text
    assert len(expected) > TELEGRAM_TEXT_LIMIT and expected.count("\n- ") == MEMORY_PATTERNS_MAX

    services = services_for(sessions)
    message = FakeMessage(361, bot_message=True, answer_as_new=True)
    await command_memory(message, services)

    parts = [message.edits[-1][0], *message.answers]
    assert len(parts) >= 2
    assert all(len(MARKS.read(part)[2]) <= TELEGRAM_TEXT_LIMIT for part in parts)
    assert all(MARKS.read(part)[0] == MessageKind.DASHBOARD.value for part in parts)
    shown = [_shown(part) for part in parts]
    assert shown[0].startswith("<b>Persistent memory</b>\n")
    assert "\n".join([shown[0].split("\n", 1)[1], *shown[1:]]) == expected
