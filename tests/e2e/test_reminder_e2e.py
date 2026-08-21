from __future__ import annotations

import json
from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import select

from llm_gateway import CompletionTurn as ProviderTurn
from llm_gateway import ToolCall as ProviderToolCall
from safwa.ai.service import ProposalService
from safwa.bootstrap.modules import PROPOSALS
from safwa.domain import update_profile
from safwa.enums import ProposalStatus
from safwa.models import ChangeProposal, ProposalChange, Reminder
from safwa.reminders import schedule_of

TZ = ZoneInfo("Europe/Istanbul")


def turn(*calls: tuple[str, dict[str, object]], prefix: str = "t") -> ProviderTurn:
    return ProviderTurn(
        content="",
        tool_calls=tuple(
            ProviderToolCall(id=f"{prefix}-{index}", name=name, arguments_json=json.dumps(arguments))
            for index, (name, arguments) in enumerate(calls, start=1)
        ),
    )


def reminder_script(config: dict[str, object], *, instruction: str, when: str):
    """The provider calls in the order the advisor makes them.

    The setup mini-session runs during materialization, after the agent loop has already
    finished, so its answer is the last response in the queue.
    """
    return [
        turn(("reminder", {"mode": "create", "instruction": instruction, "when": when})),
        "I set that up for you.",
        turn(("set_reminder_config", config), prefix="setup"),
    ]


async def test_a_reminder_reaches_a_proposal_and_save_creates_the_row(e2e_harness):
    advisor, _provider = e2e_harness.advisor(
        reminder_script(
            {"days": ["Mon", "Tue", "Wed", "Thu", "Fri"], "time": "08:30"},
            instruction="Ask me what to start with today.",
            when="every weekday at 8:30am",
        )
    )

    outcome = await advisor.handle("remind me each weekday morning", source_message_id=1)

    assert outcome.kind == "proposal"
    async with e2e_harness.sessions() as session:
        affected = await ProposalService(session, PROPOSALS).apply(outcome.proposal_id)
        await session.commit()

    async with e2e_harness.sessions() as session:
        reminder = await session.get(Reminder, affected[0])
        assert reminder.instruction == "Ask me what to start with today."
        schedule = schedule_of(reminder)
        assert schedule.weekdays == ("Mon", "Tue", "Wed", "Thu", "Fri")
        assert f"{schedule.at_time:%H:%M}" == "08:30"
        assert reminder.next_fire_at > datetime.now(UTC)
        assert reminder.next_fire_at.astimezone(TZ).strftime("%H:%M") == "08:30"


async def test_discarding_the_proposal_leaves_no_reminder(e2e_harness):
    advisor, _provider = e2e_harness.advisor(
        reminder_script(
            {"interval_minutes": 120},
            instruction="Check my posture — Check #5.",
            when="every two hours",
        )
    )

    outcome = await advisor.handle("nudge me about posture", source_message_id=1)

    async with e2e_harness.sessions() as session:
        proposal = await session.get(ChangeProposal, outcome.proposal_id)
        proposal.status = ProposalStatus.REJECTED.value
        await session.commit()
        assert list(await session.scalars(select(Reminder))) == []


async def test_the_proposal_carries_the_resolved_schedule_not_the_words(e2e_harness):
    advisor, _provider = e2e_harness.advisor(
        reminder_script(
            {"interval_minutes": 120, "quiet_windows": ["22:00-09:00"]},
            instruction="Check my posture — Check #5.",
            when="every couple of hours during the day",
        )
    )

    outcome = await advisor.handle("nudge me about posture", source_message_id=1)

    async with e2e_harness.sessions() as session:
        change = await session.scalar(
            select(ProposalChange).where(ProposalChange.proposal_id == outcome.proposal_id)
        )
        values = dict(change.values)
        assert "when" not in values  # the free text does not survive into the proposal
        assert values["schedule"]["interval_minutes"] == 120
        assert values["schedule_text"] == "every 2 hours except 22:00–09:00"


async def test_an_unresolvable_phrase_becomes_a_retryable_tool_error(e2e_harness):
    """`not_clear_enough` must reach the model as a question, never as a guessed hour."""
    advisor, provider = e2e_harness.advisor(
        [
            turn(
                (
                    "reminder",
                    {"mode": "create", "instruction": "Ask about Card #4.", "when": "sometimes"},
                )
            ),
            "Sure.",
            turn(("not_clear_enough", {"reason": "How often, and at what time?"}), prefix="setup"),
            # The preparation error is retryable, so the model gets a repair round.
            "How often should I remind you, and at what time?",
        ]
    )

    outcome = await advisor.handle("remind me sometimes", source_message_id=1)

    assert outcome.kind != "proposal"
    async with e2e_harness.sessions() as session:
        assert list(await session.scalars(select(Reminder))) == []
    # The reason reaches the model verbatim so it can ask the owner that exact question.
    last_tool_message = [
        message
        for call in provider.calls
        for message in call
        if message.get("role") == "tool"
    ]
    assert any("How often, and at what time?" in str(message) for message in last_tool_message)


async def test_editing_a_reminder_without_when_never_touches_the_schedule(e2e_harness):
    setup, _provider = e2e_harness.advisor(
        reminder_script(
            {"interval_minutes": 120},
            instruction="Check my posture — Check #5.",
            when="every two hours",
        )
    )
    outcome = await setup.handle("nudge me", source_message_id=1)
    async with e2e_harness.sessions() as session:
        affected = await ProposalService(session, PROPOSALS).apply(outcome.proposal_id)
        await session.commit()
    reminder_id = affected[0]
    async with e2e_harness.sessions() as session:
        before = (await session.get(Reminder, reminder_id)).next_fire_at

    advisor, _provider = e2e_harness.advisor(
        [
            turn(
                (
                    "reminder",
                    {
                        "mode": "update",
                        "id": reminder_id,
                        "instruction": "Check my posture properly — Check #5.",
                    },
                )
            ),
            "Updated.",
        ]
    )
    outcome = await advisor.handle("reword that reminder", source_message_id=2)
    async with e2e_harness.sessions() as session:
        await ProposalService(session, PROPOSALS).apply(outcome.proposal_id)
        await session.commit()

    async with e2e_harness.sessions() as session:
        reminder = await session.get(Reminder, reminder_id)
        assert reminder.instruction == "Check my posture properly — Check #5."
        assert reminder.next_fire_at == before
        assert schedule_of(reminder).interval_minutes == 120


async def test_ai_reminders_view_is_readable(e2e_harness):
    advisor, _provider = e2e_harness.advisor(
        reminder_script(
            {"interval_minutes": 120},
            instruction="Check my posture — Check #5.",
            when="every two hours",
        )
    )
    outcome = await advisor.handle("nudge me", source_message_id=1)
    async with e2e_harness.sessions() as session:
        await ProposalService(session, PROPOSALS).apply(outcome.proposal_id)
        await session.commit()

    reader = advisor.query_runner
    result = await reader.run("SELECT id, instruction, schedule_kind FROM ai_reminders")
    rows = result.as_tool_result()
    assert rows and rows[0]["schedule_kind"] == "interval"


async def test_the_diary_reminder_is_invisible_to_the_model(e2e_harness):
    """Unnameable is unmutatable: the model cannot ask to change an id it never reads."""
    async with e2e_harness.sessions() as session:
        await update_profile(session, diary_time=time(22, 0))
        await session.commit()
    advisor, _provider = e2e_harness.advisor([])

    result = await advisor.query_runner.run("SELECT id FROM ai_reminders")

    assert result.as_tool_result() == []
    async with e2e_harness.sessions() as session:
        assert await session.scalar(select(Reminder).where(Reminder.system.is_(True))) is not None
