from __future__ import annotations

import json
from datetime import date, timedelta

import pytest
from sqlalchemy import select
from test_subagent_e2e import diary_subagent

from llm_gateway import CompletionTurn as ProviderTurn
from llm_gateway import ToolCall as ProviderToolCall
from safwa.ai.service import ProposalService
from safwa.bootstrap.modules import PROPOSALS
from safwa.features.diary.model import DiaryEntry
from safwa.features.diary.use_cases import create_diary_entry
from safwa.models import AgentRun

pytestmark = pytest.mark.e2e

TODAY = date.today().isoformat()


def turn(*calls: tuple[str, dict[str, object]]) -> ProviderTurn:
    return ProviderTurn(
        content="",
        tool_calls=tuple(
            ProviderToolCall(id=f"call-{index}", name=name, arguments_json=json.dumps(arguments))
            for index, (name, arguments) in enumerate(calls, start=1)
        ),
    )


def write(date_value: str, pov: str, **extra: object) -> ProviderTurn:
    return turn(("diary", {"mode": "update", "date": date_value, "pov": pov, **extra}))


async def _save(harness, advisor, proposal_id: int) -> tuple[list[int], object]:
    async with harness.sessions() as session:
        description = await advisor.describe_proposal(session, proposal_id)
        affected = await ProposalService(session, PROPOSALS).apply(proposal_id)
        await session.commit()
    return affected, description


async def test_di_write_008_routed_write_is_saved_through_proposal(e2e_harness):
    """DI-WRITE-008 — tests/brd/diary.feature"""
    advisor, _ = e2e_harness.advisor(
        [
            turn(("route", {"name": "diary"})),
            turn(("read_day", {})),
            write(TODAY, "Рынок закрыл.", ai_comment="One thing held.", feeling_score=7),
        ],
        subagents=(diary_subagent(e2e_harness),),
    )

    outcome = await advisor.handle("Запиши, как прошёл день")

    assert outcome.kind == "proposal"
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


async def test_di_day_002_second_write_replaces_the_day(e2e_harness):
    """DI-DAY-002 — tests/brd/diary.feature"""
    subagent = diary_subagent(e2e_harness)
    advisor, _ = e2e_harness.advisor(
        [turn(("route", {"name": "diary"})), write(TODAY, "Утро прошло спокойно.")],
        subagents=(subagent,),
    )
    first = await advisor.handle("Запиши утро")
    entry_ids, _ = await _save(e2e_harness, advisor, first.proposal_id)

    advisor, _ = e2e_harness.advisor(
        [turn(("route", {"name": "diary"})), write(TODAY, "Утро и вечер вместе.")],
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
        [turn(("route", {"name": "diary"})), write(TODAY, "Утро.", feeling_score=8)],
        subagents=(subagent,),
    )
    first = await advisor.handle("Запиши утро")
    await _save(e2e_harness, advisor, first.proposal_id)

    advisor, _ = e2e_harness.advisor(
        [turn(("route", {"name": "diary"})), write(TODAY, "Утро и вечер.")],
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
        [turn(("route", {"name": "diary"})), write(TODAY, "Вечер испортился.", feeling_score=3)],
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
    """The whole point of a resumable subagent: "the same, but capitalise the name"."""
    subagent = diary_subagent(e2e_harness)
    advisor, _ = e2e_harness.advisor(
        [turn(("route", {"name": "diary"})), write(TODAY, "встретил ахмета на рынке.")],
        subagents=(subagent,),
    )
    first = await advisor.handle("Запиши день")
    assert first.proposal_id is not None

    # The owner answers with words instead of a button: the screen freezes, and the
    # Diary session stays waiting while the Advisor takes the words.
    await advisor.cancel_approval_for_target("proposal", first.proposal_id)
    async with e2e_harness.sessions() as session:
        diary_run = await session.scalar(
            select(AgentRun).where(AgentRun.kind == "diary").order_by(AgentRun.id.desc())
        )
        assert diary_run.status == "awaiting_approval"

    advisor, provider = e2e_harness.advisor(
        [
            turn(("route", {"name": "diary"})),
            write(TODAY, "Встретил Ахмета на рынке."),
        ],
        subagents=(subagent,),
    )
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
    replayed = [item for item in provider.calls[1] if item.get("role") == "tool"]
    assert [json.loads(str(item["content"]))["status"] for item in replayed] == ["discarded"]
    # The day itself never entered the conversation the Advisor reads.
    assert "Ахмета" not in json.dumps(provider.calls[0], ensure_ascii=False)


async def test_words_over_a_screen_end_the_caller_but_not_the_draft(e2e_harness):
    """The draft keeps its one turn of grace; the session waiting on it does not."""
    advisor, _ = e2e_harness.advisor(
        [turn(("route", {"name": "diary"})), write(TODAY, "встретил ахмета.")],
        subagents=(diary_subagent(e2e_harness),),
    )
    first = await advisor.handle("Запиши день")

    await advisor.cancel_approval_for_target("proposal", first.proposal_id)

    async with e2e_harness.sessions() as session:
        runs = list(await session.scalars(select(AgentRun).order_by(AgentRun.id)))
    assert [(run.kind, run.status) for run in runs] == [
        ("advisor", "cancelled"),
        ("diary", "awaiting_approval"),
    ]


async def test_a_refused_day_is_over_once_the_advisor_answers_something_else(e2e_harness):
    """A saved session is restorable for one Advisor turn, and for no turn after it."""
    subagent = diary_subagent(e2e_harness)
    advisor, _ = e2e_harness.advisor(
        [turn(("route", {"name": "diary"})), write(TODAY, "встретил ахмета на рынке.")],
        subagents=(subagent,),
    )
    first = await advisor.handle("Запиши день")
    await advisor.cancel_approval_for_target("proposal", first.proposal_id)

    # The owner's words turned out to be about something else, so the Advisor answers them.
    advisor, _ = e2e_harness.advisor(["Сегодня вторник."], subagents=(subagent,))
    await advisor.handle("Какой сегодня день недели?")

    async with e2e_harness.sessions() as session:
        lapsed = await session.scalar(
            select(AgentRun).where(AgentRun.kind == "diary").order_by(AgentRun.id.desc())
        )
    assert lapsed.status == "abandoned"

    # A later route therefore starts clean: the refused draft is not waiting behind it.
    advisor, provider = e2e_harness.advisor(
        [turn(("route", {"name": "diary"})), write(TODAY, "Спокойный день.")],
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
    # The new session started from an empty transcript, not from the abandoned draft.
    assert "ахмета" not in json.dumps(provider.calls[1], ensure_ascii=False)


async def test_a_screen_still_open_keeps_its_session_restorable(e2e_harness):
    """Save is the owner's to press, so a live screen is not a lapsed session."""
    board = e2e_harness.board()
    diary = diary_subagent(e2e_harness)
    advisor, _ = e2e_harness.advisor(
        [turn(("route", {"name": "diary"})), write(TODAY, "Долгий день.")],
        subagents=(board, diary),
    )
    first = await advisor.handle("Запиши день")
    assert first.proposal_id is not None

    # An escalation runs an ordinary Advisor turn while that screen is still standing.
    advisor, _ = e2e_harness.advisor(["Понял."], subagents=(board, diary))
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
            turn(("query_safwa", {"sql": "SELECT id, entry_date, body FROM ai_diary"})),
            f"Вчера — [{yesterday.strftime('%d.%m.%Y')}](diary:{entry_id}).",
        ],
        subagents=(e2e_harness.board(), diary_subagent(e2e_harness)),
    )

    outcome = await advisor.handle("Что я писал вчера?")

    assert outcome.kind == "answer"
    assert f"(diary:{entry_id})" in outcome.message
    # The turn stayed with the Advisor: no hand-over, and the day came back through the view.
    async with e2e_harness.sessions() as session:
        runs = list(await session.scalars(select(AgentRun)))
    assert [(run.kind, run.status) for run in runs] == [("advisor", "completed")]
    assert "Дошёл до рынка." in json.dumps(provider.calls[1], ensure_ascii=False)


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
                    "query_safwa",
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
        subagents=(e2e_harness.board(), diary_subagent(e2e_harness)),
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
                    "query_safwa",
                    {"sql": f"SELECT id FROM ai_diary WHERE entry_date = '{TODAY}'"},
                )
            ),
            "За сегодня записи в дневнике нет.",
        ],
        subagents=(e2e_harness.board(), diary_subagent(e2e_harness)),
    )

    outcome = await advisor.handle("Открой запись дневника за сегодня")

    assert outcome.open_item is None
    assert outcome.message == "За сегодня записи в дневнике нет."
    async with e2e_harness.sessions() as session:
        runs = list(await session.scalars(select(AgentRun)))
    assert [(run.kind, run.status) for run in runs] == [("advisor", "completed")]
