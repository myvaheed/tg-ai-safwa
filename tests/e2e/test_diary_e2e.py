from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from agent_turns import PLAN, forward_turn
from review_e2e_helpers import QueueTestMessage, review_services
from sqlalchemy import select
from test_subagent_e2e import diary_subagent
from ui_harness import button_texts, press

from llm_gateway import CompletionTurn as ProviderTurn
from llm_gateway import ToolCall as ProviderToolCall
from safwa.bootstrap.modules import PROPOSALS, SYSTEM_PROMPT
from safwa.features.diary.model import DiaryEntry
from safwa.features.diary.telegram import render_diary
from safwa.features.diary.use_cases import create_diary_entry
from tg_agent_shell.ai.outcome import AIOutcomeKind
from tg_agent_shell.ai.runs import AgentRun, AgentStep
from tg_agent_shell.foundation.clock import utcnow
from tg_agent_shell.proposals.use_cases import approve_proposal
from tg_agent_shell.telegram.dialogue import ordinary_text

pytestmark = pytest.mark.e2e

TODAY = date.today().isoformat()


def turn(*calls: tuple[str, dict[str, object]]) -> ProviderTurn:
    return ProviderTurn(
        content=PLAN,
        tool_calls=tuple(
            ProviderToolCall(id=f"call-{index}", name=name, arguments_json=json.dumps(arguments))
            for index, (name, arguments) in enumerate(calls, start=1)
        ),
    )


def write(date_value: str, body: str, **extra: object) -> ProviderTurn:
    return turn(("diary", {"mode": "update", "date": date_value, "body": body, **extra}))


def read(date_value: str) -> ProviderTurn:
    # New words for a day come only after that day was read (DI-READ-023).
    return turn(("read_day", {"date": date_value}))


async def _save(harness, advisor, proposal_id: int) -> tuple[list[int], object]:
    async with harness.sessions() as session:
        description = await advisor.describe_proposal(session, proposal_id)
        affected = await approve_proposal(session, advisor.reviews, PROPOSALS, proposal_id)
        await session.commit()
    return affected, description


async def test_di_write_008_routed_write_is_saved_through_proposal(e2e_harness):
    """DI-WRITE-008 — tests/brd/diary.feature"""
    advisor, _ = e2e_harness.advisor(
        [
            turn(("route", {"name": "diary"})),
            turn(("read_day", {})),
            write(TODAY, "Рынок закрыл.", remark="One thing held.", feeling_score=7),
        ],
        subagents=(diary_subagent(e2e_harness),),
    )

    outcome = await advisor.handle("Запиши, как прошёл день")

    assert outcome.kind is AIOutcomeKind.PROPOSAL
    affected, description = await _save(e2e_harness, advisor, outcome.proposal_id)

    async with e2e_harness.sessions() as session:
        entries = list(await session.scalars(select(DiaryEntry)))
    assert [(entry.body, entry.feeling_score) for entry in entries] == [("Рынок закрыл.", 7)]
    assert affected == [entries[0].id]
    assert entries[0].entry_date.isoformat() == TODAY
    # The receipt names the day's shape and never repeats the day into the conversation.
    assert "Entry: 13 characters" in description.fields
    assert "Feeling: 7" in description.fields
    assert not any("Рынок закрыл" in field for field in description.fields)
    assert description.summary == f"New Diary entry for {TODAY} with feeling score 7"


async def test_saved_day_is_edited_through_buttons_without_calling_the_model(e2e_harness):
    """DI-EDIT-030 — tests/brd/diary.feature"""
    async with e2e_harness.sessions() as session:
        entry = await create_diary_entry(
            session, entry_date=date.today(), body="Original.", feeling_score=7,
        )
        await session.commit()
        entry_id = entry.id
    advisor, provider = e2e_harness.advisor([])
    services = review_services(e2e_harness, advisor)
    message = QueueTestMessage()
    await render_diary(message, services, entry_id)
    await press(services, message, message.markups[-1], "✏️ Edit")
    await press(services, message, message.markups[-1], "📝 Entry")
    typed = QueueTestMessage(message_id=950, text="New day.", is_bot=False, parent=message)
    await ordinary_text(typed, services)
    markup = message.bot.edited[-1][1]
    assert button_texts(markup) == ["✏️ Edit", "↩️ Menu"]
    await press(services, message, markup, "✏️ Edit")
    await press(services, message, message.markups[-1], "🙂 Feeling")
    await press(services, message, message.markups[-1], "3 🙁")
    async with e2e_harness.sessions() as session:
        saved = await session.get(DiaryEntry, entry_id)
        assert (saved.body, saved.feeling_score, saved.version) == ("New day.", 3, 3)
        assert list(await session.scalars(select(AgentRun))) == []
    assert provider.calls == []


async def test_di_day_002_second_write_replaces_the_day(e2e_harness):
    """DI-DAY-002 — tests/brd/diary.feature"""
    subagent = diary_subagent(e2e_harness)
    advisor, _ = e2e_harness.advisor(
        [turn(("route", {"name": "diary"})), read(TODAY), write(TODAY, "Утро прошло спокойно.")],
        subagents=(subagent,),
    )
    first = await advisor.handle("Запиши утро")
    entry_ids, _ = await _save(e2e_harness, advisor, first.proposal_id)

    advisor, _ = e2e_harness.advisor(
        [turn(("route", {"name": "diary"})), read(TODAY), write(TODAY, "Утро и вечер вместе.")],
        subagents=(subagent,),
    )
    second = await advisor.handle("Допиши вечер")
    _, description = await _save(e2e_harness, advisor, second.proposal_id)

    async with e2e_harness.sessions() as session:
        entries = list(await session.scalars(select(DiaryEntry)))
    # Preparation read the saved day and aimed the second write at it.
    assert [(entry.id, entry.body, entry.version) for entry in entries] == [
        (entry_ids[0], "Утро и вечер вместе.", 2)
    ]
    assert description.summary == f"Edit Diary entry for {TODAY}"


async def test_di_mood_014_an_unnamed_score_keeps_the_saved_one(e2e_harness):
    """DI-MOOD-014 — tests/brd/diary.feature"""
    subagent = diary_subagent(e2e_harness)
    advisor, _ = e2e_harness.advisor(
        [turn(("route", {"name": "diary"})), read(TODAY), write(TODAY, "Утро.", feeling_score=8)],
        subagents=(subagent,),
    )
    first = await advisor.handle("Запиши утро")
    await _save(e2e_harness, advisor, first.proposal_id)

    advisor, _ = e2e_harness.advisor(
        [turn(("route", {"name": "diary"})), read(TODAY), write(TODAY, "Утро и вечер.")],
        subagents=(subagent,),
    )
    second = await advisor.handle("Допиши вечер")
    _, description = await _save(e2e_harness, advisor, second.proposal_id)

    async with e2e_harness.sessions() as session:
        entries = list(await session.scalars(select(DiaryEntry)))
    assert [(entry.body, entry.feeling_score) for entry in entries] == [("Утро и вечер.", 8)]
    # The screen the owner approved said 8 too, so Save stored what the screen showed.
    assert "Feeling: 8" in description.fields

    advisor, _ = e2e_harness.advisor(
        [
            turn(("route", {"name": "diary"})),
            read(TODAY),
            write(TODAY, "Вечер испортился.", feeling_score=3),
        ],
        subagents=(subagent,),
    )
    third = await advisor.handle("Стало хуже")
    await _save(e2e_harness, advisor, third.proposal_id)

    async with e2e_harness.sessions() as session:
        entries = list(await session.scalars(select(DiaryEntry)))
    assert [(entry.body, entry.feeling_score) for entry in entries] == [("Вечер испортился.", 3)]


async def test_di_date_003_named_day_is_used(e2e_harness):
    """DI-DATE-003 — tests/brd/diary.feature"""
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    advisor, _ = e2e_harness.advisor(
        [
            turn(("route", {"name": "diary"})),
            turn(("read_day", {"date": yesterday})),
            write(yesterday, "Вчера дошёл до рынка."),
        ],
        subagents=(diary_subagent(e2e_harness),),
    )

    outcome = await advisor.handle("Добавь это, пожалуйста, во вчерашний дневник")
    _, description = await _save(e2e_harness, advisor, outcome.proposal_id)

    async with e2e_harness.sessions() as session:
        entries = list(await session.scalars(select(DiaryEntry)))
    assert [entry.entry_date.isoformat() for entry in entries] == [yesterday]
    assert description.summary == f"New Diary entry for {yesterday}"


async def test_di_delete_005_existing_day_is_deleted(e2e_harness):
    """DI-DELETE-005 — tests/brd/diary.feature"""
    async with e2e_harness.sessions() as session:
        await create_diary_entry(session, entry_date=date.today(), body="Запись на удаление.")
        await session.commit()
    advisor, _ = e2e_harness.advisor(
        [turn(("route", {"name": "diary"})), turn(("diary", {"mode": "delete", "date": TODAY}))],
        subagents=(diary_subagent(e2e_harness),),
    )

    outcome = await advisor.handle("Удали сегодняшнюю запись из дневника")
    await _save(e2e_harness, advisor, outcome.proposal_id)

    async with e2e_harness.sessions() as session:
        assert await session.scalar(select(DiaryEntry)) is None


async def test_di_delete_005_missing_day_is_retryable(e2e_harness):
    """DI-DELETE-005 — tests/brd/diary.feature"""
    advisor, provider = e2e_harness.advisor(
        [
            turn(("route", {"name": "diary"})),
            turn(("diary", {"mode": "delete", "date": TODAY})),
            "За этот день ничего не записано.",
            "За этот день ничего не записано.",
        ],
        subagents=(diary_subagent(e2e_harness),),
    )

    outcome = await advisor.handle("Удали сегодняшнюю запись")

    assert outcome.message == "За этот день ничего не записано."
    refused = json.loads(
        next(item for item in provider.calls[2] if item.get("role") == "tool")["content"]
    )
    assert refused["code"] == "target_not_found"
    assert refused["retryable"] is True


async def test_a_correction_reaches_the_session_that_wrote_the_refused_day(e2e_harness):
    """AG-WORDS-018 — tests/brd/tg_agent_shell/agents.feature"""
    advisor, provider = e2e_harness.advisor(
        [
            turn(("route", {"name": "diary"})),
            read(TODAY),
            write(TODAY, "встретил ахмета на рынке."),
            turn(("route", {"name": "diary"})),
            # The resumed session read that day before, so it writes straight away.
            write(TODAY, "Встретил Ахмета на рынке."),
        ],
        subagents=(diary_subagent(e2e_harness),),
    )
    first = await advisor.handle("Запиши день")
    assert first.proposal_id is not None

    # The owner answers with words instead of a button: the screen freezes, the Diary
    # session stays unfinished, and the request that routed to it takes the words.
    await advisor.cancel_approval_for_proposal(first.proposal_id)
    async with e2e_harness.sessions() as session:
        diary_run = await session.scalar(
            select(AgentRun).where(AgentRun.kind == "diary").order_by(AgentRun.id.desc())
        )
        assert diary_run.status == "interrupted"

    second = await advisor.handle("Всё нравится, но имя напиши с заглавной")

    assert second.proposal_id is not None
    affected, _ = await _save(e2e_harness, advisor, second.proposal_id)
    async with e2e_harness.sessions() as session:
        entries = list(await session.scalars(select(DiaryEntry)))
        diary_runs = list(
            await session.scalars(
                select(AgentRun).where(AgentRun.kind == "diary").order_by(AgentRun.id)
            )
        )
    assert [entry.body for entry in entries] == ["Встретил Ахмета на рынке."]
    assert affected == [entries[0].id]
    # One session, resumed — not a second one started from scratch.
    assert len(diary_runs) == 1
    assert diary_runs[0].status == "awaiting_approval"
    # It resumed on a settled record: its refused proposal came back as a tool result.
    replayed = [item for item in provider.calls[4] if item.get("role") == "tool"]
    assert [json.loads(str(item["content"])).get("status") for item in replayed] == [
        None,
        "discarded",
    ]
    # The day itself never entered the conversation the Advisor reads.
    assert "Ахмета" not in json.dumps(provider.calls[0], ensure_ascii=False)


async def test_a_refused_day_is_over_once_the_advisor_answers_something_else(e2e_harness):
    """AG-WORDS-020 — tests/brd/tg_agent_shell/agents.feature"""
    subagent = diary_subagent(e2e_harness)
    advisor, _ = e2e_harness.advisor(
        [
            turn(("route", {"name": "diary"})),
            read(TODAY),
            write(TODAY, "встретил ахмета на рынке."),
        ],
        subagents=(subagent,),
    )
    first = await advisor.handle("Запиши день")
    await advisor.cancel_approval_for_proposal(first.proposal_id)

    # The owner's words turned out to be about something else, so the request that routed
    # to the Diary answers them itself and ends — and the unfinished draft ends with it.
    advisor, _ = e2e_harness.advisor(["Сегодня вторник."], subagents=(subagent,))
    await advisor.handle("Какой сегодня день недели?")

    async with e2e_harness.sessions() as session:
        lapsed = await session.scalar(
            select(AgentRun).where(AgentRun.kind == "diary").order_by(AgentRun.id.desc())
        )
    assert lapsed.status == "abandoned"

    # A later route therefore starts clean: the refused draft is not waiting behind it.
    advisor, provider = e2e_harness.advisor(
        [turn(("route", {"name": "diary"})), read(TODAY), write(TODAY, "Спокойный день.")],
        subagents=(subagent,),
    )
    second = await advisor.handle("Запиши день заново")
    affected, _ = await _save(e2e_harness, advisor, second.proposal_id)

    async with e2e_harness.sessions() as session:
        entries = list(await session.scalars(select(DiaryEntry)))
        diary_runs = list(
            await session.scalars(
                select(AgentRun).where(AgentRun.kind == "diary").order_by(AgentRun.id)
            )
        )
    assert [entry.body for entry in entries] == ["Спокойный день."]
    assert affected == [entries[0].id]
    assert [run.status for run in diary_runs] == ["abandoned", "awaiting_approval"]
    # A later route therefore starts clean: an ended session is nobody's to resume.
    assert "ахмета" not in json.dumps(provider.calls[1], ensure_ascii=False)


async def test_a_screen_still_open_keeps_its_session_restorable(e2e_harness):
    """Save is the owner's to press, so a live screen is not a lapsed session."""
    workspace = e2e_harness.subagent("workspace_mutator")
    diary = diary_subagent(e2e_harness)
    advisor, _ = e2e_harness.advisor(
        [turn(("route", {"name": "diary"})), read(TODAY), write(TODAY, "Долгий день.")],
        subagents=(workspace, diary),
    )
    first = await advisor.handle("Запиши день")
    assert first.proposal_id is not None
    # Its screen is drawn: a review never drawn ends with the next call (AG-TURN-015).
    advisor.reviews.proposal(first.proposal_id).shown_at = utcnow()

    # An escalation runs an ordinary Advisor turn while that screen is still standing.
    advisor, _ = e2e_harness.advisor(["Понял."], subagents=(workspace, diary))
    await advisor.handle("Напомни, что у меня в спринте?")

    async with e2e_harness.sessions() as session:
        diary_run = await session.scalar(
            select(AgentRun).where(AgentRun.kind == "diary").order_by(AgentRun.id.desc())
        )
    assert diary_run.status == "awaiting_approval"
    affected, _ = await _save(e2e_harness, advisor, first.proposal_id)
    async with e2e_harness.sessions() as session:
        entries = list(await session.scalars(select(DiaryEntry)))
    assert affected == [entries[0].id]


async def test_di_read_006_advisor_reads_and_cites_day(e2e_harness):
    """DI-READ-006 — tests/brd/diary.feature"""
    yesterday = date.today() - timedelta(days=1)
    async with e2e_harness.sessions() as session:
        entry = await create_diary_entry(session, entry_date=yesterday, body="Дошёл до рынка.")
        await session.commit()
        entry_id = entry.id

    advisor, provider = e2e_harness.advisor(
        [
            turn(("query_data", {"sql": "SELECT id, entry_date, body FROM ai_diary"})),
            f"Вчера — [{yesterday.strftime('%d.%m.%Y')}](diary:{entry_id}).",
        ],
        subagents=(e2e_harness.subagent("workspace_mutator"), diary_subagent(e2e_harness)),
    )

    outcome = await advisor.handle("Что записано в дневнике за вчера?")

    assert outcome.kind is AIOutcomeKind.ANSWER
    assert f"(diary:{entry_id})" in outcome.message
    # The turn stayed with the Advisor: no hand-over, and the day came back through the view.
    async with e2e_harness.sessions() as session:
        runs = list(await session.scalars(select(AgentRun)))
    assert [(run.kind, run.status) for run in runs] == [("advisor", "completed")]
    assert "Дошёл до рынка." in json.dumps(provider.calls[1], ensure_ascii=False)


@pytest.mark.parametrize(
    ("question", "arguments", "day", "transcript", "answer"),
    [
        ("О чём мы сегодня разговаривали?", {}, "2026-10-18", "Обсудим поездку.", "Обсуждали поездку."),
        (
            "О чём мы вчера разговаривали?",
            {"date": "2026-10-17"},
            "2026-10-17",
            "Обсудим поездку.",
            "Обсуждали поездку.",
        ),
        (
            "Напомни разговор за 10 октября 2026",
            {"date": "2026-10-10"},
            "2026-10-10",
            "Обсудим поездку.",
            "Обсуждали поездку.",
        ),
        ("О чём мы сегодня разговаривали?", {}, "2026-10-18", "", "За этот день переписки нет."),
    ],
)
async def test_di_recall_028_a_conversation_question_is_answered_without_a_proposal(
    e2e_harness, monkeypatch, question, arguments, day, transcript, answer
):
    """DI-RECALL-028 — tests/brd/diary.feature"""
    # UTC still says yesterday; both the clock and the read use the owner's local date.
    now = datetime(2026, 10, 17, 22, 20, tzinfo=UTC)
    monkeypatch.setattr(
        "safwa.features.diary.agent.SystemClock", lambda: SimpleNamespace(now=lambda: now)
    )
    windows = []

    class History:
        async def day_transcript(self, chat_id, *, start, end, token_budget):
            windows.append((chat_id, start, end))
            return f"[08:40] [User]: {transcript}" if transcript else ""

    diary = e2e_harness.subagent("diary", history=History())
    advisor, provider = e2e_harness.advisor(
        [
            turn(("route", {"name": "diary"})),
            turn(("read_conversation", arguments)),
            answer,
            forward_turn("diary"),
        ],
        subagents=(diary,),
    )

    outcome = await advisor.handle(question)

    assert outcome.kind is AIOutcomeKind.ANSWER
    assert answer in outcome.message
    assert outcome.proposal_id is None
    local_midnight = datetime.fromisoformat(day).replace(tzinfo=ZoneInfo("Europe/Istanbul"))
    assert windows == [
        (42, local_midnight.astimezone(UTC), (local_midnight + timedelta(days=1)).astimezone(UTC))
    ]
    read_result = next(item for item in provider.calls[2] if item.get("name") == "read_conversation")
    assert json.loads(read_result["content"])["date"] == day
    conversation = json.loads(read_result["content"])["conversation"]
    if transcript:
        assert transcript in conversation
    else:
        assert "nothing" in conversation
    async with e2e_harness.sessions() as session:
        assert await session.scalar(select(DiaryEntry)) is None
        steps = list(await session.scalars(select(AgentStep).order_by(AgentStep.id)))
    assert [step.kind for step in steps] == ["route", "read", "forward"]
    # Routing is chosen by the model; the scripted boundary checks the assembled instructions.
    assert (
        '`route("diary")` — questions about what the user and Safwa talked about today, '
        "yesterday or on a named day"
    ) in SYSTEM_PROMPT


async def test_di_open_010_advisor_opens_day_without_routing(e2e_harness):
    """DI-OPEN-010 — tests/brd/diary.feature"""
    requested_date = date.today() - timedelta(days=4)
    async with e2e_harness.sessions() as session:
        entry = await create_diary_entry(
            session, entry_date=requested_date, body="День, который нужно открыть."
        )
        await session.commit()
        entry_id = entry.id

    advisor, _ = e2e_harness.advisor(
        [
            turn(
                (
                    "query_data",
                    {
                        "sql": (
                            "SELECT id FROM ai_diary "
                            f"WHERE entry_date = '{requested_date.isoformat()}'"
                        )
                    },
                )
            ),
            turn(("open", {"item_type": "diary", "id": entry_id})),
            "Вот запись за нужную дату.",
        ],
        subagents=(e2e_harness.subagent("workspace_mutator"), diary_subagent(e2e_harness)),
    )

    outcome = await advisor.handle(
        f"Открой запись дневника за {requested_date.strftime('%d.%m.%Y')}"
    )

    assert outcome.open_item == f"diary-{entry_id}"
    async with e2e_harness.sessions() as session:
        runs = list(await session.scalars(select(AgentRun)))
    assert [(run.kind, run.status) for run in runs] == [("advisor", "completed")]


async def test_di_open_010_missing_day_is_reported_without_routing(e2e_harness):
    """DI-OPEN-010 — tests/brd/diary.feature"""
    advisor, _ = e2e_harness.advisor(
        [
            turn(
                (
                    "query_data",
                    {"sql": f"SELECT id FROM ai_diary WHERE entry_date = '{TODAY}'"},
                )
            ),
            "За сегодня записи в дневнике нет.",
        ],
        subagents=(e2e_harness.subagent("workspace_mutator"), diary_subagent(e2e_harness)),
    )

    outcome = await advisor.handle("Открой запись дневника за сегодня")

    assert outcome.open_item is None
    assert outcome.message == "За сегодня записи в дневнике нет."
    async with e2e_harness.sessions() as session:
        runs = list(await session.scalars(select(AgentRun)))
    assert [(run.kind, run.status) for run in runs] == [("advisor", "completed")]
