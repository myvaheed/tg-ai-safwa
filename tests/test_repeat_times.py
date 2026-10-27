"""Several copies of a repeating Action or Check closed in one Save, and what Save says it left open."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError
from schedule_helpers import rule_for
from sqlalchemy import select

from safwa.bootstrap.modules import PROPOSALS
from safwa.constants import REPEAT_TIMES_MAX
from safwa.features.cards.agent import ActionToolInput
from safwa.features.cards.model import Card, CardStage
from safwa.features.cards.telegram.review import CardProposalPresenter
from safwa.features.cards.use_cases import create_card
from safwa.features.checks.agent import CheckToolInput
from safwa.features.checks.model import Check
from safwa.features.checks.telegram import CheckProposalPresenter
from safwa.features.checks.use_cases import create_check, resolve_check
from tg_agent_shell.proposals.api import ApplyContext, ToolPreparationError
from tg_agent_shell.proposals.model import ProposalChange
from tg_agent_shell.proposals.prepare import ChangePreparer

THREE_A_WEEK = {"kind": "quota", "period": "week", "count": 3}


async def _prepared(session, tool: str, arguments: dict[str, Any]) -> ProposalChange:
    change = PROPOSALS.change_from_tool(tool, arguments)
    prepared = await ChangePreparer(None, None, PROPOSALS).prepare(session, change)
    return ProposalChange(
        entity=change.entity,
        action=change.action,
        entity_id=change.id,
        expected_version=prepared.expected_version,
        values=prepared.values,
    )


async def _saved(session, tool: str, arguments: dict[str, Any]) -> tuple[list[int], list[str]]:
    change = await _prepared(session, tool, arguments)
    context = ApplyContext(session, frozenset())
    ids = await PROPOSALS.handler(change.entity).apply(context, change)
    return ids, context.said


async def _open(session, model, series: int):
    column = model.repeat_series_id if model is Card else model.series_id
    done = model.completed_at.is_(None) if model is Card else model.outcome.is_(None)
    return list(await session.scalars(select(model).where(column == series, done)))


async def test_sch_times_032_an_action_done_three_times_closes_three_copies(sessions):
    """SCH-TIMES-032 — tests/brd/schedules.feature"""
    async with sessions() as session:
        run = await create_card(
            session, kind="action", title="Run", schedule="3 times a week", schedule_rule=THREE_A_WEEK
        )
        change = await _prepared(session, "action", {"mode": "complete", "id": run.id, "times": 3})
        presenter = CardProposalPresenter()
        details = await presenter.details(session, change, None)
        assert "×3" in await presenter.summary(session, change, details)
        context = ApplyContext(session, frozenset())
        finished = await PROPOSALS.handler("card").apply(context, change)
        await session.commit()

        assert len(finished) == 3 and finished[0] == run.id
        for card_id in finished:
            assert (await session.get(Card, card_id)).effective_stage == CardStage.DONE.value
        [still_open] = await _open(session, Card, run.repeat_series_id or run.id)
        assert context.said == [
            f"Finished 3 copies. The open one now: [Run](card:{still_open.id})."
        ]


async def test_sch_times_032_save_stops_where_the_series_ends(sessions):
    """SCH-TIMES-032 — tests/brd/schedules.feature"""
    async with sessions() as session:
        run = await create_card(
            session, kind="action", title="Run", schedule="3 times a week, 2 in all",
            schedule_rule={**THREE_A_WEEK, "limit": 2},
        )
        finished, said = await _saved(session, "action", {"mode": "complete", "id": run.id, "times": 3})

    assert len(finished) == 2
    assert said == ["Finished 2 copies. The series has ended; no copy is open."]


async def test_sch_times_032_a_check_is_answered_copy_after_copy(sessions):
    """SCH-TIMES-032 — tests/brd/schedules.feature"""
    async with sessions() as session:
        read = await create_check(
            session, title="Read?", schedule="daily 21:00",
            schedule_rule=await rule_for(session, "daily 21:00"),
        )
        change = await _prepared(session, "check", {"mode": "missed", "id": read.id, "times": 2})
        presenter = CheckProposalPresenter()
        details = await presenter.details(session, change, None)
        assert (await presenter.summary(session, change, details)).endswith("(Missed ×2)")
        context = ApplyContext(session, frozenset())
        answered = await PROPOSALS.handler("check").apply(context, change)
        await session.commit()

        assert len(answered) == 2
        for check_id in answered:
            assert (await session.get(Check, check_id)).outcome == "missed"
        [still_open] = await _open(session, Check, read.series_id or read.id)
        assert context.said == [
            f"Answered 2 copies. The open one now: [Read?](check:{still_open.id})."
        ]


async def test_sch_times_032_what_closes_only_once_is_sent_back(sessions):
    """SCH-TIMES-032 — tests/brd/schedules.feature"""
    async with sessions() as session:
        once = await create_card(session, kind="action", title="Call the bank")
        asked = await create_check(session, title="Warmed up?")
        checked = await create_card(
            session, kind="action", title="Run", schedule="3 times a week",
            schedule_rule=THREE_A_WEEK, check_ids={asked.id},
        )
        # Answered, so only the copies after it would ask again.
        await resolve_check(session, asked.id, "passed")
        loose = await create_check(session, title="Calm?")
        await session.commit()
        for tool, arguments, code in (
            ("action", {"mode": "complete", "id": once.id, "times": 2}, "does not repeat"),
            ("action", {"mode": "complete", "id": checked.id, "times": 2}, "has Checks"),
            ("check", {"mode": "passed", "id": loose.id, "times": 2}, "no Schedule of its own"),
        ):
            with pytest.raises(ToolPreparationError, match=code):
                await _prepared(session, tool, arguments)

    for invalid in (
        {"mode": "complete", "id": 1, "times": 2, "tracked_mins": 30},
        {"mode": "update", "id": 1, "times": 2},
        {"mode": "complete", "id": 1, "times": REPEAT_TIMES_MAX + 1},
    ):
        with pytest.raises(ValidationError):
            ActionToolInput.model_validate(invalid)
    with pytest.raises(ValidationError):
        CheckToolInput.model_validate({"mode": "update", "id": 1, "title": "Calm?", "times": 2})


async def test_pr_note_038_a_save_says_which_copy_it_left_open(sessions):
    """PR-NOTE-038 — tests/brd/tg_agent_shell/proposals.feature"""
    async with sessions() as session:
        warm = await create_check(session, title="Warmed up?")
        run = await create_card(
            session, kind="action", title="Run", schedule="3 times a week",
            schedule_rule=THREE_A_WEEK, check_ids={warm.id},
        )
        await resolve_check(session, warm.id, "passed")
        once = await create_card(session, kind="action", title="Call the bank")
        await session.commit()

        _, said = await _saved(session, "action", {"mode": "complete", "id": run.id})
        [next_copy] = await _open(session, Card, run.repeat_series_id or run.id)
        [next_check] = [
            check for check in await _open(session, Check, warm.series_id or warm.id)
        ]
        assert said == [
            f"Its next copy is open: [Run](card:{next_copy.id}), "
            f"with [Warmed up?](check:{next_check.id})."
        ]
        assert (await _saved(session, "action", {"mode": "complete", "id": once.id}))[1] == []
