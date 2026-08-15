from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from safwa.ai.diary import DiarySubagent
from safwa.ai.provider import ProviderToolCall, ProviderTurn
from safwa.ai.service import ProposalService, query_read_tool
from safwa.ai.sql import ReadOnlyQueryRunner
from safwa.domain import create_diary_entry
from safwa.models import DiaryEntry, DiaryStamp

pytestmark = pytest.mark.e2e

TODAY = date.today().isoformat()


class SubagentProvider:
    """The subagent's own boundary; the advisor keeps the harness one."""

    def __init__(self, turns: list[ProviderTurn]) -> None:
        self.turns = turns

    async def complete_turn(self, _messages, **_kwargs) -> ProviderTurn:
        if not self.turns:
            raise AssertionError("The subagent made an unexpected provider call")
        return self.turns.pop(0)


class StubDayReader:
    async def day_transcript(self, _chat_id: int, *, start, end, token_budget) -> str:  # noqa: ARG002
        return "[08:40] [User]: Долгий день, но рынок закрыл."


def turn(*calls: tuple[str, dict[str, object]]) -> ProviderTurn:
    return ProviderTurn(
        content="",
        tool_calls=tuple(
            ProviderToolCall(id=f"call-{index}", name=name, arguments=json.dumps(arguments))
            for index, (name, arguments) in enumerate(calls, start=1)
        ),
    )


def diary_for(harness, *turns: ProviderTurn) -> DiarySubagent:
    return DiarySubagent(
        harness.sessions,
        SubagentProvider(list(turns)),  # type: ignore[arg-type]
        StubDayReader(),
        query_read_tool(ReadOnlyQueryRunner(harness.database_path)),
        chat_id=42,
        timezone="Europe/Istanbul",
    )


async def _newest_stamp(harness, *, besides: str = "") -> DiaryStamp:
    async with harness.sessions() as session:
        stamp = await session.scalar(
            select(DiaryStamp).where(DiaryStamp.stamp != besides).order_by(DiaryStamp.created_at.desc())
        )
    assert stamp is not None
    return stamp


async def _save(harness, stamp: str) -> tuple[list[int], object]:
    """The saving turn, scripted from a stamp that exists only once the subagent ran."""
    advisor, _ = harness.advisor([turn(("propose_diary_update", {"stamp": stamp}))])
    proposal = await advisor.handle("Сохрани")
    assert proposal.proposal_id is not None
    async with harness.sessions() as session:
        description = await advisor.describe_proposal(session, proposal.proposal_id)
        affected = await ProposalService(session).apply(proposal.proposal_id)
        await session.commit()
    return affected, description


async def test_a_draft_travels_from_the_subagent_to_a_saved_entry(e2e_harness):
    subagent = diary_for(
        e2e_harness,
        turn(("read_day", {})),
        turn(("diary_report", {"date": TODAY, "entry": "Рынок закрыл.", "remark": "One thing held."})),
    )
    advisor, _ = e2e_harness.advisor(
        [
            turn(("call_subagent", {"name": "diary", "request": "Запиши сегодняшний день."})),
            "Запись за сегодня готова.",
        ],
        subagents=(subagent,),
    )

    outcome = await advisor.handle("Запиши, как прошёл день")

    assert outcome.kind == "answer"
    stamp = await _newest_stamp(e2e_harness)
    affected, description = await _save(e2e_harness, stamp.stamp)

    async with e2e_harness.sessions() as session:
        entries = list(await session.scalars(select(DiaryEntry)))
        spent = await session.get(DiaryStamp, stamp.stamp)
    assert [entry.body for entry in entries] == ["Рынок закрыл."]
    assert affected == [entries[0].id]
    assert entries[0].entry_date.isoformat() == TODAY
    # The receipt carries the whole entry, so the model reads the saved day back.
    assert "Entry: Рынок закрыл." in description.fields
    assert description.summary == f"New Diary entry for {TODAY}"
    # Saving settles the day, so the stamp behind it is spent.
    assert spent is None


async def test_a_second_draft_the_same_day_overwrites_rather_than_adding(e2e_harness):
    subagent = diary_for(
        e2e_harness,
        turn(("diary_report", {"date": TODAY, "entry": "Утро прошло спокойно.", "remark": "Early."})),
        turn(("diary_report", {"date": TODAY, "entry": "Утро и вечер вместе.", "remark": "Fuller."})),
    )
    advisor, _ = e2e_harness.advisor(
        [
            turn(("call_subagent", {"name": "diary", "request": "Запиши утро."})),
            "Утро записал.",
        ],
        subagents=(subagent,),
    )
    await advisor.handle("Запиши утро")
    first = await _newest_stamp(e2e_harness)
    entry_ids, _ = await _save(e2e_harness, first.stamp)

    advisor, _ = e2e_harness.advisor(
        [
            turn(("call_subagent", {"name": "diary", "request": "Допиши вечер."})),
            "Вечер добавил.",
        ],
        subagents=(subagent,),
    )
    await advisor.handle("Допиши вечер")
    second = await _newest_stamp(e2e_harness, besides=first.stamp)
    # The subagent read the saved entry itself and aimed the second draft at it.
    assert (second.entry_id, second.action) == (entry_ids[0], "update")

    _, description = await _save(e2e_harness, second.stamp)

    async with e2e_harness.sessions() as session:
        entries = list(await session.scalars(select(DiaryEntry)))
    assert [(entry.id, entry.body, entry.version) for entry in entries] == [
        (entry_ids[0], "Утро и вечер вместе.", 2)
    ]
    assert description.summary == f"Edit Diary entry for {TODAY}"


async def test_a_back_dated_entry_lands_on_the_day_the_subagent_chose(e2e_harness):
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    subagent = diary_for(
        e2e_harness,
        turn(("read_day", {"date": yesterday})),
        turn(("diary_report", {"date": yesterday, "entry": "Вчера дошёл до рынка.", "remark": "Late but true."})),
    )
    advisor, _ = e2e_harness.advisor(
        [
            turn(("call_subagent", {"name": "diary", "request": "Добавь это во вчерашний день."})),
            "Записал во вчерашний день.",
        ],
        subagents=(subagent,),
    )

    await advisor.handle("Добавь это, пожалуйста, во вчерашний дневник")

    stamp = await _newest_stamp(e2e_harness)
    assert stamp.entry_date.isoformat() == yesterday
    _, description = await _save(e2e_harness, stamp.stamp)

    async with e2e_harness.sessions() as session:
        entries = list(await session.scalars(select(DiaryEntry)))
    assert [entry.entry_date.isoformat() for entry in entries] == [yesterday]
    assert description.summary == f"New Diary entry for {yesterday}"


async def test_a_removal_deletes_the_day_and_says_so_in_the_conversation(e2e_harness):
    async with e2e_harness.sessions() as session:
        await create_diary_entry(session, entry_date=date.today(), body="Запись на удаление.")
        await session.commit()
    subagent = diary_for(
        e2e_harness, turn(("diary_report", {"date": TODAY, "remove": True}))
    )
    advisor, _ = e2e_harness.advisor(
        [
            turn(("call_subagent", {"name": "diary", "request": "Удали сегодняшнюю запись."})),
            "Готов удалить.",
        ],
        subagents=(subagent,),
    )

    await advisor.handle("Удали сегодняшнюю запись из дневника")

    stamp = await _newest_stamp(e2e_harness)
    assert (stamp.action, stamp.body) == ("delete", "")
    _, description = await _save(e2e_harness, stamp.stamp)

    async with e2e_harness.sessions() as session:
        assert list(await session.scalars(select(DiaryEntry))) == []
    assert description.summary == f"Delete Diary entry for {TODAY}"
    assert "Entry: removed" in description.fields


async def test_a_discarded_change_survives_but_a_saved_day_clears_every_draft(e2e_harness):
    subagent = diary_for(
        e2e_harness,
        turn(("diary_report", {"date": TODAY, "entry": "Первый черновик.", "remark": "One."})),
        turn(("diary_report", {"date": TODAY, "entry": "Второй черновик.", "remark": "Two."})),
    )
    advisor, _ = e2e_harness.advisor(
        [turn(("call_subagent", {"name": "diary", "request": "Запиши день."})), "Готово."],
        subagents=(subagent,),
    )
    await advisor.handle("Запиши день")
    first = await _newest_stamp(e2e_harness)

    advisor, _ = e2e_harness.advisor([turn(("propose_diary_update", {"stamp": first.stamp}))])
    discarded = await advisor.handle("Покажи")
    async with e2e_harness.sessions() as session:
        await ProposalService(session).reject(discarded.proposal_id or 0)
        await session.commit()
        # Discard changes nothing, so the same change is offered again from the same stamp.
        assert await session.get(DiaryStamp, first.stamp) is not None

    advisor, _ = e2e_harness.advisor(
        [turn(("call_subagent", {"name": "diary", "request": "Перепиши день."})), "Готово."],
        subagents=(subagent,),
    )
    await advisor.handle("Перепиши день")
    second = await _newest_stamp(e2e_harness, besides=first.stamp)
    await _save(e2e_harness, second.stamp)

    async with e2e_harness.sessions() as session:
        remaining = list(await session.scalars(select(DiaryStamp)))
        entries = list(await session.scalars(select(DiaryEntry)))
    # The older draft describes the day as it was; re-proposing it would revert the save.
    assert remaining == []
    assert [entry.body for entry in entries] == ["Второй черновик."]


async def test_a_stamp_the_advisor_invented_is_refused_and_retryable(e2e_harness):
    advisor, provider = e2e_harness.advisor(
        [
            turn(("propose_diary_update", {"stamp": "never-issued"})),
            "Мне нужно сначала прочитать день.",
        ]
    )

    outcome = await advisor.handle("Сохрани дневник")

    assert outcome.proposal_id is None
    refused = json.loads(
        next(item for item in provider.calls[1] if item.get("role") == "tool")["content"]
    )
    assert refused["code"] == "diary_stamp_not_found"
    assert refused["retryable"] is True


async def test_an_expired_stamp_is_refused_and_left_in_place(e2e_harness):
    async with e2e_harness.sessions() as session:
        session.add(
            DiaryStamp(
                stamp="yesterday",
                entry_date=date.today() - timedelta(days=1),
                action="create",
                body="Вчерашний день.",
                expires_at=datetime.now(UTC) - timedelta(hours=1),
            )
        )
        await session.commit()
    advisor, provider = e2e_harness.advisor(
        [turn(("propose_diary_update", {"stamp": "yesterday"})), "День уже закрыт."]
    )

    await advisor.handle("Сохрани вчерашнее")

    refused = json.loads(
        next(item for item in provider.calls[1] if item.get("role") == "tool")["content"]
    )
    assert refused["code"] == "diary_stamp_expired"
    async with e2e_harness.sessions() as session:
        assert await session.get(DiaryStamp, "yesterday") is not None
