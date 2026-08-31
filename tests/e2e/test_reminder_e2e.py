from __future__ import annotations

import json
from datetime import UTC, datetime, time
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from sqlalchemy import select

from llm_gateway import CompletionTurn as ProviderTurn
from llm_gateway import ToolCall as ProviderToolCall
from safwa.ai.outcome import AIOutcomeKind
from safwa.bootstrap.modules import PROPOSALS, SCREENS
from safwa.features.profile.model import ProfileField
from safwa.features.profile.use_cases import set_profile_field
from safwa.features.proposals.use_cases import approve_proposal
from safwa.features.reminders.schedule import schedule_of
from safwa.foundation.clock import SystemClock
from safwa.models import CallbackToken, Reminder
from safwa.telegram import callback_token_handler, render_proposal
from safwa.turn import TurnManager
from telegram_llm import DialogueMessage

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
    """RM-WRITE-008 — tests/brd/reminders.feature"""
    advisor, _provider = e2e_harness.advisor(
        reminder_script(
            {"days": ["Mon", "Tue", "Wed", "Thu", "Fri"], "time": "08:30"},
            instruction="Ask me what to start with today.",
            when="every weekday at 8:30am",
        )
    )

    outcome = await advisor.handle("remind me each weekday morning", source_message_id=1)

    assert outcome.kind is AIOutcomeKind.PROPOSAL
    async with e2e_harness.sessions() as session:
        affected = await approve_proposal(session, e2e_harness.reviews, PROPOSALS, outcome.proposal_id)
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
    """RM-WRITE-008 — tests/brd/reminders.feature"""
    advisor, _provider = e2e_harness.advisor(
        reminder_script(
            {"interval_minutes": 120},
            instruction="Check my posture — Check #5.",
            when="every two hours",
        )
    )

    outcome = await advisor.handle("nudge me about posture", source_message_id=1)

    async with e2e_harness.sessions() as session:
        advisor.reviews.end_proposal(outcome.proposal_id)
        await session.commit()
        assert list(await session.scalars(select(Reminder))) == []


async def test_the_proposal_carries_the_resolved_schedule_not_the_words(e2e_harness):
    """RM-SCHEDULE-001 — tests/brd/reminders.feature"""
    advisor, _provider = e2e_harness.advisor(
        reminder_script(
            {"interval_minutes": 120, "quiet_windows": ["22:00-09:00"]},
            instruction="Check my posture — Check #5.",
            when="every couple of hours during the day",
        )
    )

    outcome = await advisor.handle("nudge me about posture", source_message_id=1)

    change = e2e_harness.reviews.proposal(outcome.proposal_id).changes[0]
    values = dict(change.values)
    assert "when" not in values  # the free text does not survive into the proposal
    assert values["schedule"]["interval_minutes"] == 120
    assert values["schedule_text"] == "every 2 hours except 22:00–09:00"


async def test_an_unresolvable_phrase_becomes_a_retryable_tool_error(e2e_harness):
    """RM-SCHEDULE-002 — tests/brd/reminders.feature"""
    # `not_clear_enough` must reach the model as a question, never as a guessed hour.
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
    """RM-WRITE-009 — tests/brd/reminders.feature"""
    setup, _provider = e2e_harness.advisor(
        reminder_script(
            {"interval_minutes": 120},
            instruction="Check my posture — Check #5.",
            when="every two hours",
        )
    )
    outcome = await setup.handle("nudge me", source_message_id=1)
    async with e2e_harness.sessions() as session:
        affected = await approve_proposal(session, e2e_harness.reviews, PROPOSALS, outcome.proposal_id)
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
        await approve_proposal(session, e2e_harness.reviews, PROPOSALS, outcome.proposal_id)
        await session.commit()

    async with e2e_harness.sessions() as session:
        reminder = await session.get(Reminder, reminder_id)
        assert reminder.instruction == "Check my posture properly — Check #5."
        assert reminder.next_fire_at == before
        assert schedule_of(reminder).interval_minutes == 120


async def test_ai_reminders_view_is_readable(e2e_harness):
    """RM-READ-024 — tests/brd/reminders.feature"""
    advisor, _provider = e2e_harness.advisor(
        reminder_script(
            {"interval_minutes": 120},
            instruction="Check my posture — Check #5.",
            when="every two hours",
        )
    )
    outcome = await advisor.handle("nudge me", source_message_id=1)
    async with e2e_harness.sessions() as session:
        await approve_proposal(session, e2e_harness.reviews, PROPOSALS, outcome.proposal_id)
        await session.commit()

    reader = advisor.adapters.query_runner
    result = await reader.run(
        "SELECT id, instruction, schedule_kind, next_fire_at_local FROM ai_reminders"
    )
    rows = result.as_tool_result()
    assert rows and rows[0]["schedule_kind"] == "interval"

    async with e2e_harness.sessions() as session:
        stored = await session.scalar(select(Reminder))
    # The model quotes this back to the owner, so it reads in the owner's clock rather
    # than the UTC instant the row stores.
    assert rows[0]["next_fire_at_local"] == (
        f"{stored.next_fire_at.astimezone(reader.tz):%Y-%m-%d %H:%M}"
    )
    assert rows[0]["next_fire_at_local"] != f"{stored.next_fire_at:%Y-%m-%d %H:%M}"


class _TestMessage:
    """The Telegram surface a proposal screen renders onto."""

    def __init__(self) -> None:
        self.message_id = 900
        self.chat = SimpleNamespace(id=700, type="private")
        self.from_user = SimpleNamespace(id=42, is_bot=True)
        self.bot = _TestBot()
        self.text = ""
        self.rendered: list[str] = []
        self.markups: list[object] = []

    async def edit_text(self, text, *, reply_markup=None, parse_mode=None):
        del parse_mode
        self.rendered.append(text)
        self.markups.append(reply_markup)
        return self

    async def answer(self, text, *, reply_markup=None, parse_mode=None):
        del parse_mode
        self.rendered.append(text)
        self.markups.append(reply_markup)
        return self

    async def edit_reply_markup(self, *, reply_markup=None):
        self.markups.append(reply_markup)
        return self


class _TestBot:
    async def send_chat_action(self, *_args, **_kwargs) -> None:
        return None

    async def delete_message(self, *_args, **_kwargs) -> None:
        return None

    async def edit_message_reply_markup(self, *_args, **_kwargs) -> None:
        return None


class _TestCallback:
    def __init__(self, token: str, message: _TestMessage) -> None:
        self.data = f"cb:{token}"
        self.message = message

    async def answer(self, text=None, *, show_alert=False) -> None:
        del text, show_alert


class _TestHistory:
    async def dialogue(self, _chat_id):
        return [DialogueMessage(role="user", content="[Initial request]: drop that reminder")]


def _services(harness, advisor) -> SimpleNamespace:
    return SimpleNamespace(
        sessions=harness.sessions,
        advisor=advisor,
        history=_TestHistory(),
        owner_id=42,
        turn=TurnManager(),
        screens=SCREENS,
        bot_username="safwa_ai_bot",
    )


async def _live_actions(harness) -> set[str]:
    async with harness.sessions() as session:
        return {
            token.action
            for token in await session.scalars(
                select(CallbackToken).where(CallbackToken.consumed_at.is_(None))
            )
        }


async def _press(harness, action: str, message: _TestMessage, services) -> None:
    async with harness.sessions() as session:
        tokens = list(
            await session.scalars(
                select(CallbackToken).where(
                    CallbackToken.action == action, CallbackToken.consumed_at.is_(None)
                )
            )
        )
    assert tokens, f"no live {action} button"
    await callback_token_handler(_TestCallback(tokens[-1].token, message), services)


async def test_the_model_removes_a_reminder_with_one_save(e2e_harness):
    """RM-WRITE-010 — tests/brd/reminders.feature"""
    setup, _provider = e2e_harness.advisor(
        reminder_script(
            {"interval_minutes": 120},
            instruction="Check my posture — Check #5.",
            when="every two hours",
        )
    )
    outcome = await setup.handle("nudge me", source_message_id=1)
    async with e2e_harness.sessions() as session:
        reminder_id = (await approve_proposal(session, e2e_harness.reviews, PROPOSALS, outcome.proposal_id))[0]
        await session.commit()

    advisor, _provider = e2e_harness.advisor(
        [
            turn(("remove", {"entity": "reminder", "id": reminder_id})),
            "That one is gone.",
        ]
    )
    outcome = await advisor.handle("drop that reminder", source_message_id=2)
    assert outcome.proposal_id is not None

    message = _TestMessage()
    services = _services(e2e_harness, advisor)
    await render_proposal(message, services, outcome.proposal_id)
    # Save and Discard, and nothing else: a Reminder is not the Card tree whose deletion
    # takes its subtree and its historical contribution with it.
    assert await _live_actions(e2e_harness) == {"proposal_approve", "proposal_reject"}

    await _press(e2e_harness, "proposal_approve", message, services)

    async with e2e_harness.sessions() as session:
        assert await session.get(Reminder, reminder_id) is None
    assert not any("destructive" in text.lower() for text in message.rendered)
    # The receipt says what happened: there is no archive to read "Archive" as.
    assert any("Delete Reminder" in text for text in message.rendered)


async def test_the_diary_reminder_is_invisible_to_the_model(e2e_harness):
    """RM-SYSTEM-022 — tests/brd/reminders.feature"""
    # Unnameable is unmutatable: the model cannot ask to change an id it never reads.
    async with e2e_harness.sessions() as session:
        await set_profile_field(
            session, ProfileField.DIARY_TIME, time(22, 0), clock=SystemClock()
        )
        await session.commit()
    advisor, _provider = e2e_harness.advisor([])

    result = await advisor.adapters.query_runner.run("SELECT id FROM ai_reminders")

    assert result.as_tool_result() == []
    async with e2e_harness.sessions() as session:
        assert await session.scalar(select(Reminder).where(Reminder.system.is_(True))) is not None
