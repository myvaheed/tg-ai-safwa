"""The retro analysis: the record it reads, the questions it asks, and what it leaves behind."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from datetime import date, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from ui_harness import FakeCallback, FakeMessage, button_texts, services_for

from llm_gateway import CompletionRequest, CompletionTurn, ToolCall
from safwa.features.cards.model import CardStage
from safwa.features.cards.use_cases import (
    create_card,
    delete_one_card,
    finish_action,
    move_card,
    record_today_morning,
)
from safwa.features.diary.use_cases import create_diary_entry
from safwa.features.memory.store import MemoryFileStore
from safwa.features.planning.api import sprint_metrics
from safwa.features.planning.closing import RetroStatistics
from safwa.features.planning.model import Sprint, SprintCommitment
from safwa.features.planning.use_cases import finish_sprint, start_sprint
from safwa.features.retro.analysis import (
    ANALYSIS_DAY_BATCH,
    ANALYSIS_PROMPT,
    CROSS_PROMPT,
    DAYS_PROMPT,
    FACT_CHARS,
    ITEM_CHARS,
    ITEMS_MAX,
    METRIC_CHARS,
    NOTE_CHARS,
    OVERVIEW_PROMPT,
    REVIEW_PROMPT,
    SENTENCE_CHARS,
    TRENDS_MAX,
    Finding,
    SamePair,
    SprintAnalysis,
    SprintAnalyst,
    _at_once,
    without_same,
)
from safwa.features.retro.telegram import analysis_text, open_retro
from safwa.features.retro.use_cases import RETRO_SPRINTS_BEFORE, analysis_input, mark_criterion
from telegram_llm.text import TELEGRAM_TEXT_LIMIT
from tg_agent_shell.ai.contracts import tool_json_schema
from tg_agent_shell.foundation.clock import utcnow
from tg_agent_shell.foundation.errors import DomainError
from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.history import TelegramMessage
from tg_agent_shell.telegram import OwnerAndWritingMiddleware, callback_token_handler

# ------------------------------------------------------------------------------ fixtures


def _today() -> date:
    """The owner's today: the workspace the fixtures build is in Europe/Istanbul."""
    return utcnow().astimezone(ZoneInfo("Europe/Istanbul")).date()


async def _ended_sprint(sessions, *, criteria: str, days: int = 3, titles=("Run", "Write")) -> int:
    """One Sprint that ran `days` local days and ended today, with these Actions finished."""
    async with sessions() as session:
        cards = [
            await create_card(
                session,
                kind="action",
                title=title,
                stage="sprint",
                effort_points=2,
                categories={"work"} if title == "Write" else {"self"},
                energy_types={"cognitive"} if title == "Write" else {"physical"},
            )
            for title in titles
        ]
        sprint = await start_sprint(session, success_criteria=criteria)
        sprint.actual_started_at = utcnow() - timedelta(days=days - 1)
        for card in cards:
            await move_card(session, card.id, CardStage.TODAY)
        await record_today_morning(session)
        for card in cards:
            await finish_action(session, card.id)
        await finish_sprint(session)
        await session.commit()
        return sprint.id


class AnalysisProvider:
    """Answers each question with the one call its tool asks for, and keeps every request.

    A claim about the day's rating carries the dates of the batch it was asked about, so
    the review sees where each came from; "поздний отбой" is claimed as raising the rating
    and as lowering it, so the cross review has a pair to name. `unrelated` is always
    empty, so that list is never asked about. `fails` names a tool the model answers in
    prose instead, for good, and `on_request` is called with each tool's name before it is
    answered.
    """

    def __init__(
        self,
        *,
        fails: str | None = None,
        hold: asyncio.Event | None = None,
        on_request: Callable[[str], object] | None = None,
    ) -> None:
        self.requests: list[CompletionRequest] = []
        self.fails = fails
        self.hold = hold
        self.on_request = on_request

    async def complete(self, request: CompletionRequest) -> CompletionTurn:
        self.requests.append(request)
        if self.hold is not None:
            await self.hold.wait()
        name = request.tools[0]["function"]["name"]
        if self.on_request is not None:
            self.on_request(name)
        context = request.messages[1]["content"]
        if name == self.fails:
            return CompletionTurn("I would rather not.")
        if name == "overview_verdict":
            metric = context.splitlines()[0][:40]
            arguments = {
                "verbose_analyse": "Numbers rise.",
                "findings": [{"metric": metric, "trend": "up", "note": "2 of 2 against 1 of 2"}],
            }
        elif name == "day_batch_findings":
            days = re.findall(r"^Day (\S+)", context, re.MULTILINE)
            arguments = {
                "verbose_analyse": "Good days had a run.",
                "helped": [
                    {"claim": "утренняя пробежка", "days": days},
                    {"claim": "поздний отбой", "days": days[-1:]},
                ],
                "hurt": [
                    {"claim": "три рабочих Действия подряд", "days": days[:1]},
                    {"claim": "поздний отбой", "days": days[:1]},
                ],
                "unrelated": [],
            }
        elif name == "claim_review":
            found = re.findall(r"^\d+\. (.+?) — days (.+)$", context, re.MULTILINE)
            claims = [claim for claim, _days in found]
            # Said twice, or resting on two or more days, is confirmed.
            backed = [claim for claim, days in found if claims.count(claim) > 1 or "," in days]
            arguments = {
                "verbose_analyse": "A claim said twice is confirmed.",
                "confirmed": list(dict.fromkeys(backed)),
                "contradicted": [],
                "single": [claim for claim in claims if claim not in backed],
            }
        elif name == "cross_review":
            numbered = re.findall(r"^(\d+)\. \[(.+?)\] (.+)$", context, re.MULTILINE)
            arguments = {
                "verbose_analyse": "One thing stands in two lists.",
                "same": [
                    {"one": int(one), "other": int(other)}
                    for one, list_one, claim_one in numbered
                    for other, list_other, claim_other in numbered
                    if int(one) < int(other) and list_one != list_other and claim_one == claim_other
                ],
            }
        else:
            confirmed = re.findall(
                r"^- (.+)$", context.split("Confirmed claims", 1)[1], re.MULTILINE
            )
            arguments = {
                "verbose_analyse": "It went well.",
                "headline": "Лучший Спринт из трёх.",
                "dynamics": [{"metric": "Actions finished", "trend": "up", "note": "2 of 2"}],
                "helped": [claim for claim in confirmed if claim != "nothing confirmed"][:1],
                "hurt": [],
                "noise": [],
                "experiment": "Одна пробежка каждое утро.",
                "memory_fact": "Утренняя пробежка поднимает оценку дня.",
            }
        return CompletionTurn("", tool_calls=(ToolCall("call-1", name, json.dumps(arguments)),))

    async def aclose(self) -> None:
        return None


def _services(sessions, provider: AnalysisProvider, tmp_path):
    services = services_for(sessions)
    services.features = SimpleNamespace(
        analyst=SprintAnalyst(provider),
        memory=MemoryFileStore(tmp_path / "memory.md", sessions),
    )
    return services


def _button(message: FakeMessage, text: str):
    markup = message.edits[-1][1]
    for row in markup.inline_keyboard:
        for button in row:
            if button.text == text:
                return button
    raise AssertionError(f"no button {text!r} among {button_texts(markup)}")


async def _press(message: FakeMessage, services, text: str) -> None:
    token = _button(message, text).callback_data.split(":", 1)[1]
    await callback_token_handler(FakeCallback(token, message), services)


# ------------------------------------------------------------------------------- record


async def test_rt_stats_003_the_record_keeps_counts_shares_and_days(sessions) -> None:
    """RT-STATS-003 — tests/brd/retro.feature"""
    async with sessions() as session:
        both = await create_card(
            session,
            kind="action",
            title="Both",
            stage="sprint",
            effort_points=3,
            categories={"work", "self"},
            energy_types={"cognitive"},
        )
        await create_card(session, kind="action", title="Plain", stage="sprint", effort_points=1)
        sprint = await start_sprint(session, success_criteria="Ship")
        sprint.actual_started_at = utcnow() - timedelta(days=1)
        commitment = await session.scalar(
            select(SprintCommitment).where(SprintCommitment.card_id == both.id)
        )
        commitment.key_action = True
        await move_card(session, both.id, CardStage.TODAY)
        await record_today_morning(session)
        await finish_action(session, both.id)
        await finish_sprint(session)
        await session.commit()
        statistics = RetroStatistics.from_record((await session.get(Sprint, sprint.id)).retro)

    assert (statistics.planned, statistics.finished) == (2, 1)
    assert (statistics.key_total, statistics.key_finished) == (1, 1)
    # "Both" is in work and self whole; "Plain" carries nothing and is in none.
    work, none = statistics.by_category["work"], statistics.by_category["none"]
    assert (work.effort, work.done_effort, work.count, work.done_count) == (3, 3, 1, 1)
    assert (none.effort, none.done_effort, none.count, none.done_count) == (1, 0, 1, 0)
    assert statistics.by_category["self"].done_count == 1
    assert statistics.by_energy["cognitive"].done_effort == 3
    assert statistics.by_energy["none"].count == 1
    # Two local days: yesterday with nothing, today with one Action in Today and one finished.
    assert [day.day for day in statistics.days] == [
        (_today() - timedelta(days=1)).isoformat(),
        _today().isoformat(),
    ]
    yesterday, today = statistics.days
    assert (yesterday.planned, yesterday.done) == (0, 0)
    assert (today.planned, today.done) == (1, 1)
    assert today.done_by_category == {"work": 1, "self": 1}
    assert today.done_by_energy == {"cognitive": 1}
    # "Plain" was never told key or not; "Both" was.
    assert statistics.key_unknown == 1


async def test_rt_stats_003_the_calendar_and_the_effort_are_the_sprints_own(sessions) -> None:
    """RT-STATS-003 — tests/brd/retro.feature"""
    async with sessions() as session:
        gone = await create_card(session, kind="action", title="Gone", stage="sprint", effort_points=5)
        sprint = await start_sprint(session, success_criteria="Ship")
        sprint.actual_started_at = utcnow() - timedelta(days=2)
        await session.commit()
        # The Sprint screen and the record add the effort up the same way.
        assert await sprint_metrics(session, sprint.id) == {
            "committed": 5,
            "added": 0,
            "removed": 0,
            "completed": 0,
        }
        await delete_one_card(session, gone.id)
        await finish_sprint(session)
        await session.commit()
        statistics = RetroStatistics.from_record((await session.get(Sprint, sprint.id)).retro)

    # Every Action is gone, and the three days the Sprint ran are still there.
    assert statistics.planned == 0
    assert [day.day for day in statistics.days] == [
        (_today() - timedelta(days=offset)).isoformat() for offset in (2, 1, 0)
    ]
    assert (statistics.first_day, statistics.last_day) == (_today() - timedelta(days=2), _today())
    assert (statistics.taken, statistics.done) == (0, 0)


# --------------------------------------------------------------------------- the mark


async def test_rt_crit_004_whether_the_criteria_were_met_is_the_owners_word(
    sessions, tmp_path
) -> None:
    """RT-CRIT-004 — tests/brd/retro.feature"""
    async with sessions() as session:
        await create_card(session, kind="action", title="Planned", stage="sprint", effort_points=2)
        running = await start_sprint(session, success_criteria="Ship v2")
        await session.commit()
        with pytest.raises(DomainError, match="has not ended"):
            await mark_criterion(session, running.id, True)
        await finish_sprint(session)
        await session.commit()

    services = _services(sessions, AnalysisProvider(), tmp_path)
    message = FakeMessage(330, bot_message=True)
    await open_retro(message, services, running.id)
    assert "Met: not marked yet" in message.edits[-1][0]

    await _press(message, services, "✅ Met")
    text, markup = message.edits[-1]
    assert "Met: yes" in text
    assert button_texts(markup)[:2] == ["❌ Not met", "❓ Unmark"]

    await _press(message, services, "❌ Not met")
    text, markup = message.edits[-1]
    assert "Met: no" in text
    assert button_texts(markup)[:2] == ["✅ Met", "❓ Unmark"]

    await _press(message, services, "❓ Unmark")
    text, markup = message.edits[-1]
    assert "Met: not marked yet" in text
    async with sessions() as session:
        assert (await session.get(Sprint, running.id)).criterion_met is None


# ----------------------------------------------------------------------------- the run


async def _analysis_of(sessions, sprint_id: int):
    async with sessions() as session:
        return (await session.get(Sprint, sprint_id)).analysis


async def _first_request(provider: AnalysisProvider) -> None:
    for _ in range(500):
        if provider.requests:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the run never asked the model anything")


async def test_rt_ai_005_analysing_is_one_run_watched_on_one_message(
    sessions, tmp_path, monkeypatch
) -> None:
    """RT-AI-005 — tests/brd/retro.feature"""
    sprint_id = await _ended_sprint(sessions, criteria="Бегать каждое утро", days=4)
    provider = AnalysisProvider()
    services = _services(sessions, provider, tmp_path)
    message = FakeMessage(340, bot_message=True, answer_as_new=True)
    await open_retro(message, services, sprint_id)

    await _press(message, services, "🔎 Analyse with AI")

    # The run's first move took the buttons off the retro screen; its last drew the analysis.
    bare_text, bare_markup = message.edits[1]
    assert "retro</b>" in bare_text and bare_markup is None
    assert "analysis</b>" in message.edits[-1][0]
    progress = message.sent_messages[0]
    assert "Analysing Sprint" in progress.text
    assert "0%" in progress.text
    percents = [
        int(match.group(1))
        for _id, text, _markup in message.bot.edits
        if (match := re.search(r"(\d+)%", text))
    ]
    assert percents == sorted(percents) and percents[-1] == 100
    assert progress.message_id in message.bot.deleted
    async with sessions() as session:
        rows = list(await session.scalars(select(TelegramMessage)))
    assert all(row.kind != MessageKind.STATUS.value for row in rows)

    # The owner's message reaches the middleware, which ends the run; nothing of it is kept.
    import tg_agent_shell.telegram.services as core_module

    monkeypatch.setattr(core_module, "Message", FakeMessage)
    async with sessions() as session:
        (await session.get(Sprint, sprint_id)).analysis = None
        await session.commit()
    held = AnalysisProvider(hold=asyncio.Event())
    services = _services(sessions, held, tmp_path)
    again = FakeMessage(341, bot_message=True, answer_as_new=True)
    await open_retro(again, services, sprint_id)
    pressing = asyncio.create_task(_press(again, services, "🔎 Analyse with AI"))
    await _first_request(held)
    assert again.edits[-1][1] is None and services.turn.background
    handled: list[str] = []

    async def handler(event, _data) -> None:
        handled.append(event.text)

    owner_says = FakeMessage(500, text="Как прошёл день?", bot_message=False, bot=again.bot)
    await OwnerAndWritingMiddleware()(handler, owner_says, {"services": services})
    await pressing
    assert handled == ["Как прошёл день?"]
    assert not services.turn.active
    assert again.sent_messages[0].message_id in again.bot.deleted
    assert await _analysis_of(sessions, sprint_id) is None

    # A run whose turn was taken while its last answer was being written writes nothing.
    stale = AnalysisProvider()
    services = _services(sessions, stale, tmp_path)
    stale.on_request = lambda name: services.turn.cancel() if name == "sprint_analysis" else None
    losing = FakeMessage(342, bot_message=True, answer_as_new=True)
    await open_retro(losing, services, sprint_id)
    await _press(losing, services, "🔎 Analyse with AI")
    assert await _analysis_of(sessions, sprint_id) is None
    assert losing.sent_messages[0].message_id in losing.bot.deleted
    assert losing.edits[-1][1] is None

    # A run that fails says so, and the retro screen comes back with its buttons: the next
    # tap starts over.
    services = _services(sessions, AnalysisProvider(fails="sprint_analysis"), tmp_path)
    failing = FakeMessage(343, bot_message=True, answer_as_new=True)
    await open_retro(failing, services, sprint_id)
    await _press(failing, services, "🔎 Analyse with AI")
    assert "could not be finished" in failing.sent_messages[-1].text
    text, markup = failing.edits[-1]
    assert "retro</b>" in text and "🔎 Analyse with AI" in button_texts(markup)
    assert await _analysis_of(sessions, sprint_id) is None
    services.features.analyst = SprintAnalyst(AnalysisProvider())
    await _press(failing, services, "🔎 Analyse with AI")
    assert "analysis</b>" in failing.edits[-1][0]
    assert await _analysis_of(sessions, sprint_id) is not None


async def test_a_question_that_fails_ends_the_others_before_the_failure_is_raised() -> None:
    ended = asyncio.Event()
    asked = asyncio.Event()

    async def slow() -> str:
        try:
            asked.set()
            await asyncio.sleep(60)
            return "never"
        finally:
            ended.set()

    async def failing() -> str:
        await asked.wait()
        raise ValueError("prose instead of a call")

    with pytest.raises(ValueError):
        await _at_once(slow(), failing())
    assert ended.is_set()


async def test_rt_ai_006_the_analysis_reads_the_record_and_the_diary(sessions, tmp_path) -> None:
    """RT-AI-006 — tests/brd/retro.feature"""
    numbers = []
    for criteria in ("First", "Second", "Third", "Fourth"):
        sprint_id = await _ended_sprint(sessions, criteria=criteria, days=2)
        async with sessions() as session:
            sprint = await session.get(Sprint, sprint_id)
            numbers.append(sprint.number)
            if criteria == "Third":
                await mark_criterion(session, sprint_id, True)
            await session.commit()
    today = _today()
    async with sessions() as session:
        await create_diary_entry(
            session, entry_date=today, body="Очень длинный день. " * 50, feeling_score=8
        )
        await session.commit()
        given = await analysis_input(session, sprint_id)

    assert [column.number for column in given.sprints] == numbers[-(RETRO_SPRINTS_BEFORE + 1) :]
    assert [column.criteria for column in given.sprints] == ["Second", "Third", "Fourth"]
    assert [column.met for column in given.sprints] == [None, True, None]
    assert given.diary[today].score == 8
    assert len(given.diary[today].body) == len(("Очень длинный день. " * 50).strip())

    provider = AnalysisProvider()
    await SprintAnalyst(provider).analyse(given, report=_no_report)
    overview = provider.requests[0].messages[1]["content"]
    assert (
        "Second" in overview
        and "Fourth" in overview
        and "met: ?" in overview
        and "met: yes" in overview
    )
    # No hook ran here, so no Action was told key or not; the model is told that, not "0 of 0".
    assert "key Actions finished 0 of 0; 2 Actions not yet told key or not" in overview
    days = [r for r in provider.requests if r.tools[0]["function"]["name"] == "day_batch_findings"]
    assert ("Очень длинный день. " * 50).strip() in days[-1].messages[1]["content"]
    assert all(len(request.tools) == 1 for request in provider.requests)


async def _no_report(done: int, total: int, note: str) -> None:
    return None


async def test_rt_ai_007_the_run_is_small_questions_each_answered_with_one_call(
    sessions, tmp_path
) -> None:
    """RT-AI-007 — tests/brd/retro.feature"""
    sprint_id = await _ended_sprint(sessions, criteria="Ship", days=7)
    async with sessions() as session:
        given = await analysis_input(session, sprint_id)
    provider = AnalysisProvider()
    reports: list[tuple[int, int, str]] = []

    async def report(done: int, total: int, note: str) -> None:
        reports.append((done, total, note))

    record = await SprintAnalyst(provider).analyse(given, report=report)

    names = [request.tools[0]["function"]["name"] for request in provider.requests]
    batches = -(-7 // ANALYSIS_DAY_BATCH)
    assert names[:3] == ["overview_verdict"] * 3
    assert names[3 : 3 + batches] == ["day_batch_findings"] * batches
    # `unrelated` came back empty from every batch, so only two lists are reviewed.
    assert names[3 + batches :] == [
        "claim_review",
        "claim_review",
        "cross_review",
        "sprint_analysis",
    ]
    prompts = [request.messages[0]["content"] for request in provider.requests]
    assert prompts[0] == OVERVIEW_PROMPT and prompts[3] == DAYS_PROMPT
    assert prompts[-3] == REVIEW_PROMPT and prompts[-2] == CROSS_PROMPT
    assert prompts[-1] == ANALYSIS_PROMPT
    for request in provider.requests:
        schema = request.tools[0]["function"]["parameters"]
        assert next(iter(schema["properties"])) == "verbose_analyse"
    # The cross review answers with numbers, so it alone names no language.
    for prompt in (OVERVIEW_PROMPT, DAYS_PROMPT, REVIEW_PROMPT, ANALYSIS_PROMPT):
        assert "language" in prompt
    # Two days inside one batch count as said twice.
    assert "resting on two or more days" in REVIEW_PROMPT
    day_batch = provider.requests[3].messages[1]["content"]
    assert len(re.findall(r"^Day ", day_batch, re.MULTILINE)) == ANALYSIS_DAY_BATCH
    review = provider.requests[3 + batches].messages[1]["content"]
    assert review.count("утренняя пробежка") == batches
    # The confirmed lists, numbered across, each claim with the list it stands in.
    cross = provider.requests[-2].messages[1]["content"]
    assert re.search(r"^1\. \[raised the day's rating\] утренняя пробежка$", cross, re.MULTILINE)
    assert re.search(r"^2\. \[raised the day's rating\] поздний отбой$", cross, re.MULTILINE)
    assert re.search(r"^4\. \[lowered the day's rating\] поздний отбой$", cross, re.MULTILINE)
    synthesis = provider.requests[-1].messages[1]["content"]
    assert "утренняя пробежка" in synthesis and "Ship" in synthesis
    assert "три рабочих Действия подряд" in synthesis
    assert "поздний отбой" not in synthesis
    # One step per question, the list nobody asked about included, reported from 0 up.
    steps = 3 + batches + 3 + 1 + 1
    assert [done for done, _total, _note in reports] == list(range(steps + 1))
    assert {total for _done, total, _note in reports} == {steps}
    assert record["headline"] == "Лучший Спринт из трёх."
    assert record["helped"] == ["утренняя пробежка"]
    assert record["dropped"] == ["поздний отбой"]
    assert "verbose_analyse" not in record
    assert set(record) >= set(SprintAnalysis.model_fields) - {"verbose_analyse"}


def test_a_pair_inside_one_list_or_off_the_numbers_takes_nothing_out() -> None:
    confirmed = [["run", "late night"], ["late night", "three tasks"], []]
    kept, dropped = without_same(
        confirmed, [SamePair(one=1, other=2), SamePair(one=2, other=3), SamePair(one=4, other=9)]
    )
    assert kept == [["run"], ["three tasks"], []]
    assert dropped == ["late night"]


async def test_rt_ai_007_a_short_sprint_is_one_batch_and_a_claim_on_two_days_is_confirmed(
    sessions,
) -> None:
    """RT-AI-007 — tests/brd/retro.feature"""
    sprint_id = await _ended_sprint(sessions, criteria="Ship", days=ANALYSIS_DAY_BATCH)
    async with sessions() as session:
        given = await analysis_input(session, sprint_id)
    provider = AnalysisProvider()
    record = await SprintAnalyst(provider).analyse(given, report=_no_report)

    names = [request.tools[0]["function"]["name"] for request in provider.requests]
    assert names.count("day_batch_findings") == 1
    # The run rests on three days, the late night on one; only the run is confirmed, and
    # with one list left with a claim the lists are not read together.
    assert "cross_review" not in names
    assert record["helped"] == ["утренняя пробежка"]
    assert record["dropped"] == []


async def test_rt_ai_008_what_the_run_leaves_behind(sessions, tmp_path) -> None:
    """RT-AI-008 — tests/brd/retro.feature"""
    # Six days are two batches, so a claim both make is confirmed.
    sprint_id = await _ended_sprint(sessions, criteria="Бегать каждое утро", days=6)
    services = _services(sessions, AnalysisProvider(), tmp_path)
    message = FakeMessage(350, bot_message=True, answer_as_new=True)
    await open_retro(message, services, sprint_id)
    await _press(message, services, "🔎 Analyse with AI")

    text, markup = message.edits[-1]
    # Headed by the six days the run read; the planned dates start today and run on.
    async with sessions() as session:
        statistics = RetroStatistics.from_record((await session.get(Sprint, sprint_id)).retro)
    assert f"{_today() - timedelta(days=5)} – {_today()}" in text
    assert f"{statistics.first_day} – {statistics.last_day}" in text
    assert "the criteria then not marked" in text and "since" not in text
    for line in (
        "analysis</b>",
        "<i>Лучший Спринт из трёх.</i>",
        "▲ Actions finished — 2 of 2",
        "• утренняя пробежка",
        "→ Одна пробежка каждое утро.",
        "Утренняя пробежка поднимает оценку дня.",
    ):
        assert line in text
    assert button_texts(markup) == ["💾 Remember", "🔁 Analyse again", "📊 Retro", "↩️ Menu"]
    async with sessions() as session:
        first = (await session.get(Sprint, sprint_id)).analysis
    assert first["experiment"] == "Одна пробежка каждое утро."

    await _press(message, services, "💾 Remember")
    memory = (tmp_path / "memory.md").read_text(encoding="utf-8")
    async with sessions() as session:
        number = (await session.get(Sprint, sprint_id)).number
    assert f"Sprint {number} retro: Утренняя пробежка поднимает оценку дня." in memory
    text, markup = message.edits[-1]
    assert "remembered in memory.md" in text
    assert "💾 Remember" not in button_texts(markup)

    # A later run replaces the analysis, and takes nothing back out of memory.md.
    await _press(message, services, "🔁 Analyse again")
    async with sessions() as session:
        second = (await session.get(Sprint, sprint_id)).analysis
    assert second["analysed_at"] > first["analysed_at"]
    assert (tmp_path / "memory.md").read_text(encoding="utf-8") == memory
    assert "💾 Remember" in button_texts(message.edits[-1][1])

    # The retro screen names the analysis from now on.
    await _press(message, services, "📊 Retro")
    assert button_texts(message.edits[-1][1]) == [
        "✅ Met",
        "❌ Not met",
        "🔁 Analyse again",
        "📊 Analysis",
        "↩️ Menu",
    ]

    # A mark set after the run is named on the analysis screen, and counted by the next run.
    await _press(message, services, "✅ Met")
    await _press(message, services, "📊 Analysis")
    text = message.edits[-1][0]
    assert "the criteria then not marked" in text
    assert "Marked met since; analyse again for the mark to count." in text
    await _press(message, services, "🔁 Analyse again")
    text = message.edits[-1][0]
    assert "the criteria then met" in text and "since" not in text
    async with sessions() as session:
        assert (await session.get(Sprint, sprint_id)).analysis["met"] is True


async def test_rt_ai_008_the_screen_fits_one_message_at_every_limit(sessions, tmp_path) -> None:
    """RT-AI-008 — tests/brd/retro.feature"""
    trend = Finding(metric="м" * METRIC_CHARS, trend="up", note="н" * NOTE_CHARS)
    full = SprintAnalysis(
        verbose_analyse="х" * 5_000,
        headline="з" * SENTENCE_CHARS,
        dynamics=[trend] * TRENDS_MAX,
        helped=["п" * ITEM_CHARS] * ITEMS_MAX,
        hurt=["о" * ITEM_CHARS] * ITEMS_MAX,
        noise=["ш" * ITEM_CHARS] * ITEMS_MAX,
        experiment="э" * SENTENCE_CHARS,
        memory_fact="ф" * FACT_CHARS,
    )
    record = {
        **full.model_dump(exclude={"verbose_analyse"}),
        "dropped": ["д" * ITEM_CHARS] * 3,
        "compared": ["26.08-02", "26.09-01"],
        "met": True,
        "analysed_at": utcnow().isoformat(),
        "remembered": True,
    }
    sprint_id = await _ended_sprint(sessions, criteria="Ship", days=14)
    async with sessions() as session:
        sprint = await session.get(Sprint, sprint_id)
        sprint.criterion_met = False
        statistics = RetroStatistics.from_record(sprint.retro)
        text = analysis_text(sprint, statistics, record, ZoneInfo("Europe/Istanbul"))
    assert len(text) <= TELEGRAM_TEXT_LIMIT

    # One over any limit is refused by the call, and the schema says so up front.
    for over in (
        {"helped": ["п"] * (ITEMS_MAX + 1)},
        {"dynamics": [trend] * (TRENDS_MAX + 1)},
        {"headline": "з" * (SENTENCE_CHARS + 1)},
        {"hurt": ["о" * (ITEM_CHARS + 1)]},
        {"memory_fact": "ф" * (FACT_CHARS + 1)},
    ):
        with pytest.raises(ValidationError):
            SprintAnalysis(**{**full.model_dump(), **over})
    with pytest.raises(ValidationError):
        Finding(metric="м" * (METRIC_CHARS + 1), trend="up", note="")
    properties = tool_json_schema(SprintAnalysis)["properties"]
    assert properties["helped"]["maxItems"] == ITEMS_MAX
    assert properties["helped"]["items"]["maxLength"] == ITEM_CHARS
    assert properties["dynamics"]["maxItems"] == TRENDS_MAX
    assert properties["headline"]["maxLength"] == SENTENCE_CHARS
    assert "maxLength" not in properties["verbose_analyse"]
