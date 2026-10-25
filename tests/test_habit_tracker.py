"""📊 Also track: a new Reminder at a time of day turned into a habit tracker (RM-TRACK-039)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from llm_gateway import CompletionTurn, ToolCall
from safwa.bootstrap.modules import PROPOSALS
from safwa.constants import WEEKDAY_NAMES
from safwa.features.checks.model import Check
from safwa.features.checks.telegram import CheckProposalPresenter
from safwa.features.checks.use_cases import check_value_ids
from safwa.features.reminders.api import describe, resolve, schedule_payload
from safwa.features.reminders.model import Reminder
from safwa.features.reminders.tracker import ALSO_TRACK, SET_TRACKER_PROMPT, tracked
from safwa.features.schedules.api import entity_reminder
from safwa.features.values.api import CHECK_VALUE_LINKED
from safwa.features.values.use_cases import create_value
from safwa.foundation.workspace import Workspace
from tg_agent_shell.ai.contracts import AgentChange
from tg_agent_shell.cues.initiatives import CommittedSink, bind_committed
from tg_agent_shell.foundation.changes import Committed
from tg_agent_shell.foundation.errors import DomainError
from tg_agent_shell.hooks.registry import HookRegistry
from tg_agent_shell.proposals.api import ChangeAction, ProposalChange
from tg_agent_shell.proposals.prepare import ChangePreparer
from tg_agent_shell.proposals.store import ProposalStore
from tg_agent_shell.proposals.use_cases import approve_proposal, switch_proposal

TZ = ZoneInfo("Europe/Istanbul")  # the test workspace's own zone
WORDS = "Remind the user to drink water"


class TrackerModel:
    """The tracker session, one answer per request; a request past them is a failure."""

    def __init__(self, *answers: dict[str, object]) -> None:
        self.answers = list(answers)
        self.requests: list[object] = []

    async def complete(self, request):
        self.requests.append(request)
        call = ToolCall(
            id=f"tracker-{len(self.requests)}",
            name="set_tracker",
            arguments_json=json.dumps(self.answers.pop(0)),
        )
        return CompletionTurn(content="", tool_calls=(call,))


def _reminder(schedule, action: ChangeAction = ChangeAction.CREATE) -> ProposalChange:
    """A prepared Reminder proposal, as its handler leaves it."""
    return ProposalChange(
        entity="reminder",
        action=action,
        values={
            "remind_text": WORDS,
            "schedule": schedule_payload(schedule),
            "schedule_text": describe(schedule, tz=TZ),
        },
        source=AgentChange(entity="reminder", action=action, values={"remind_text": WORDS}),
    )


def _every_morning(**bounds):
    return resolve(days=WEEKDAY_NAMES, clock="07:00", now=datetime.now(UTC), tz=TZ, **bounds)


async def _switched(session, model: TrackerModel, change: ProposalChange):
    store = ProposalStore()
    revision = (await session.get(Workspace, 1)).revision
    proposal = store.open_proposal(workspace_revision=revision, changes=[change])
    preparer = ChangePreparer(model, None, PROPOSALS)  # type: ignore[arg-type]
    await switch_proposal(session, store, PROPOSALS, preparer, proposal.id, 0)
    return store, proposal


async def _values(session, *names: str) -> dict[str, int]:
    ids = {name: (await create_value(session, name, "What I do for it.")).id for name in names}
    await session.commit()
    return ids


def test_only_a_new_reminder_at_a_time_of_day_offers_it():
    """RM-TRACK-039 — tests/brd/reminders.feature"""
    now = datetime.now(UTC)
    offered = [
        switch.label for _place, switch in PROPOSALS.offered_switches([_reminder(_every_morning())])
    ]
    assert offered == [ALSO_TRACK]
    assert tracked(_reminder(resolve(days=["Mon"], clock="09:00", now=now, tz=TZ)))
    assert tracked(_reminder(resolve(month_days=[1], clock="09:00", now=now, tz=TZ)))
    assert not tracked(_reminder(resolve(in_minutes=30, now=now, tz=TZ)))
    assert not tracked(_reminder(resolve(interval_minutes=120, now=now, tz=TZ)))
    assert not tracked(_reminder(_every_morning(), ChangeAction.UPDATE))
    condition = ProposalChange(
        entity="reminder", action=ChangeAction.CREATE, values={"remind_text": WORDS}
    )
    assert not tracked(condition)


async def test_the_reminder_becomes_a_check_with_its_own_timing(sessions):
    """RM-TRACK-039 — tests/brd/reminders.feature"""
    schedule = _every_morning(until="30 days", limit=10)
    model = TrackerModel({"title": "Drank water?", "values": ["health"]})
    async with sessions() as session:
        await _values(session, "Health", "Family")
        _store, proposal = await _switched(session, model, _reminder(schedule))

        [change] = proposal.changes
        assert (change.entity, change.action) == ("check", ChangeAction.CREATE)
        assert change.values["title"] == "Drank water?"
        assert change.values["schedule"] == describe(schedule, tz=TZ)
        assert change.values["schedule_rule"] == {
            "kind": "fixed",
            "timing": schedule_payload(_every_morning()),
            "until": schedule.until.isoformat(),
            "limit": 10,
        }
        assert (change.values["remind"], change.values["values"]) == (0, ["Health"])
        # The session read the Reminder's words and every Value, and nothing read the timing.
        [request] = model.requests
        assert request.messages[0]["content"] == SET_TRACKER_PROMPT
        assert request.messages[1]["content"] == f"Reminder: {WORDS}\nValues: Health, Family"


async def test_a_value_not_in_the_list_goes_back_to_the_session(sessions):
    """RM-TRACK-039 — tests/brd/reminders.feature"""
    model = TrackerModel(
        {"title": "Drank water?", "values": ["Hydration"]},
        {"title": "Drank water?", "values": []},
    )
    async with sessions() as session:
        await _values(session, "Health")
        _store, proposal = await _switched(session, model, _reminder(_every_morning()))
    assert len(model.requests) == 2
    assert "Hydration is not in the Values list" in json.dumps(model.requests[1].messages)
    assert proposal.changes[0].values["values"] == []


async def test_a_session_that_cannot_answer_leaves_the_reminder(sessions):
    """RM-TRACK-039 — tests/brd/reminders.feature"""
    model = TrackerModel(*[{"title": "Drank water?", "values": ["Hydration"]}] * 8)
    change = _reminder(_every_morning())
    async with sessions() as session:
        with pytest.raises(DomainError, match="Could not turn this Reminder into a tracker"):
            await _switched(session, model, change)
    assert change.entity == "reminder"


async def test_the_screen_proposes_the_check_and_edit_offers_its_values(sessions):
    """RM-TRACK-039 — tests/brd/reminders.feature"""
    model = TrackerModel({"title": "Drank water?", "values": ["Health"]})
    async with sessions() as session:
        await _values(session, "Health")
        _store, proposal = await _switched(session, model, _reminder(_every_morning(limit=10)))
        screen = await CheckProposalPresenter().screen(session, proposal.changes)

    shown = {field.label: field.after for field in screen.fields}
    assert screen.item == "Check"
    assert (shown["Title"], shown["Schedule"]) == ("Drank water?", "every day at 07:00, 10 times")
    assert (shown["Remind"], shown["Values"]) == ("At the time", ("Health",))
    assert "10 times in total" in screen.blocks[0]
    assert "values" in {key for _index, key, _field in PROPOSALS.edit_fields(proposal.changes)}


async def test_save_writes_the_check_its_remind_and_its_value_together(sessions, monkeypatch):
    """RM-TRACK-039 — tests/brd/reminders.feature"""
    model = TrackerModel({"title": "Drank water?", "values": ["Health"]})
    committed: list[Committed] = []
    monkeypatch.setattr(CommittedSink, "publish", lambda _sink, changes: committed.extend(changes))
    sink = bind_committed(sessions, HookRegistry.of(()))
    async with sessions() as session:
        health = (await _values(session, "Health"))["Health"]
        store, proposal = await _switched(session, model, _reminder(_every_morning()))
        [check_id] = await approve_proposal(session, store, PROPOSALS, proposal.id)

        check = await session.get(Check, check_id)
        assert (check.title, check.schedule) == ("Drank water?", "every day at 07:00")
        assert await check_value_ids(session, check_id) == [health]
        remind = await entity_reminder(session, check)
        assert remind is not None and remind.lead_minutes == 0
        assert list(await session.scalars(select(Reminder))) == [remind]
    await sink.close()
    assert Committed(CHECK_VALUE_LINKED, check_id, {"value_id": health}) in committed
