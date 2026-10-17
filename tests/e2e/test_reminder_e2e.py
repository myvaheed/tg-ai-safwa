from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from advisor_e2e_helpers import PLAN, route_turn
from schedule_helpers import create_check
from sqlalchemy import select
from ui_harness import spawn_timer

from llm_gateway import CompletionTurn as ProviderTurn
from llm_gateway import ToolCall as ProviderToolCall
from safwa.bootstrap.modules import (
    FEATURE_CALLBACK_ACTIONS,
    FEATURE_TEXT_INPUTS,
    PROPOSALS,
    SCREENS,
)
from safwa.features.checks.model import CheckOutcome
from safwa.features.checks.use_cases import resolve_check
from safwa.features.reminders.agent import (
    REMINDER_ANSWER_FORMS,
    REMINDER_ITEMS_PROMPT,
    REMINDER_TIME_FORMS,
    UNREADABLE_ITEMS,
    UNREADABLE_WHEN,
)
from safwa.features.reminders.api import schedule_of
from safwa.features.reminders.model import Reminder
from telegram_llm import ChatHost, DialogueMessage
from tg_agent_shell.ai.mini import MINI_SESSION_REPAIR_ROUNDS
from tg_agent_shell.ai.outcome import AIOutcomeKind
from tg_agent_shell.history import TelegramNotes
from tg_agent_shell.proposals.telegram import render_proposal
from tg_agent_shell.proposals.use_cases import approve_proposal
from tg_agent_shell.telegram import callback_token_handler
from tg_agent_shell.telegram.model import CallbackToken
from tg_agent_shell.turn import TurnManager

TZ = ZoneInfo("Europe/Istanbul")


def turn(*calls: tuple[str, dict[str, object]], prefix: str = "t") -> ProviderTurn:
    return ProviderTurn(
        content=PLAN,
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
        route_turn("workspace_mutator"),
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
            route_turn("workspace_mutator"),
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


def unclear_errors(provider) -> list[str]:
    """What each refused `reminder` call told the model was unclear."""
    errors = []
    for call in provider.calls:
        for message in call:
            if message.get("role") != "tool":
                continue
            body = json.loads(message["content"])
            if isinstance(body, dict) and body.get("code") == "schedule_unclear":
                errors.append(body["error"])
    return errors


def unclear_script(*setup, when: str):
    """A Reminder the setup session answers with `setup`, refused, then asked about."""
    return [
        route_turn("workspace_mutator"),
        turn(("reminder", {"mode": "create", "instruction": "Cheer me on.", "when": when})),
        "Sure.",
        *setup,
        "When should it fire?",
        "When should it fire?",
    ]


def series_select(check_id: int) -> str:
    return (
        "SELECT id FROM ai_checks WHERE series_id = "
        f"(SELECT series_id FROM ai_checks WHERE id = {check_id})"
    )


def condition_turns(items: str, sql: str, answer: str = "missed", count: int = 3, **extra):
    """The two short sessions' answers: the condition in words, then its SELECT."""
    return [
        turn(
            ("set_condition", {"items": items, "answer": answer, "count": count, **extra}),
            prefix="setup",
        ),
        turn(("set_items", {"sql": sql}), prefix="items"),
    ]


async def test_rm_cond_030_a_condition_is_proposed_with_its_select_and_what_it_finds(
    e2e_harness,
):
    """RM-COND-030 — tests/brd/reminders.feature"""
    async with e2e_harness.sessions() as session:
        live = await create_check(session, title="Sat straight?", schedule="after completion")
        _, live = await resolve_check(session, live.id, CheckOutcome.MISSED)
        await session.commit()
        named = live.id
    advisor, provider = e2e_harness.advisor(
        [
            route_turn("workspace_mutator"),
            turn(
                (
                    "reminder",
                    {
                        "mode": "create",
                        "instruction": "Cheer me on.",
                        "when": f"every 3 Missed in a row of Check #{named}",
                    },
                )
            ),
            "I set that up for you.",
            *condition_turns(f"Check #{named}", series_select(named), in_row=True),
        ]
    )

    outcome = await advisor.handle("cheer me on when I miss posture", source_message_id=1)

    assert outcome.kind is AIOutcomeKind.PROPOSAL
    # The second session reads only the items, the answer and the view to read.
    system, context = provider.calls[-1][:2]
    assert system["content"] == REMINDER_ITEMS_PROMPT
    assert context["content"] == (
        f"Items: Check #{named}\nAnswer: missed, so read ai_checks\nTags: none\nValues: none"
    )
    values = dict(e2e_harness.reviews.proposal(outcome.proposal_id).changes[0].values)
    assert values["schedule_text"] == f"every 3 Missed in a row of Check #{named}"
    assert values["finds"] == "«Sat straight?»."
    async with e2e_harness.sessions() as session:
        (reminder_id,) = await approve_proposal(
            session, e2e_harness.reviews, PROPOSALS, outcome.proposal_id
        )
        await session.commit()
        reminder = await session.get(Reminder, reminder_id)
        assert reminder.condition == {
            "items": f"Check #{named}",
            "sql": series_select(named),
            "answer": "missed",
            "count": 3,
            "in_row": True,
        }
        assert reminder.next_fire_at is None


async def test_rm_cond_031_a_select_over_the_wrong_view_goes_back_to_repair(e2e_harness):
    """RM-COND-031 — tests/brd/reminders.feature"""
    async with e2e_harness.sessions() as session:
        check = await create_check(session, title="Sat straight?", schedule="after completion")
        await session.commit()
    advisor, provider = e2e_harness.advisor(
        [
            route_turn("workspace_mutator"),
            turn(
                (
                    "reminder",
                    {
                        "mode": "create",
                        "instruction": "Cheer me on.",
                        "when": f"every 3 Missed in a row of Check #{check.id}",
                    },
                )
            ),
            "I set that up for you.",
            *condition_turns(f"Check #{check.id}", "SELECT id FROM ai_cards", in_row=True),
            turn(("set_items", {"sql": series_select(check.id)}), prefix="repaired"),
        ]
    )

    outcome = await advisor.handle("cheer me on when I miss posture", source_message_id=1)

    assert outcome.kind is AIOutcomeKind.PROPOSAL
    repair = json.loads(provider.calls[-1][-1]["content"])
    assert repair["retryable"] is True and "The SELECT must read ai_checks" in repair["error"]


async def test_rm_cond_031_a_select_that_returns_no_ids_comes_back_with_the_answer_forms(
    e2e_harness,
):
    """RM-COND-031 — tests/brd/reminders.feature"""
    advisor, provider = e2e_harness.advisor(
        unclear_script(
            *condition_turns("any Check", "SELECT title FROM ai_checks"),
            when="every 3 Missed of any Check",
        )
    )

    await advisor.handle("cheer me on after 3 misses", source_message_id=1)

    (error,) = unclear_errors(provider)
    assert error.splitlines()[0].startswith(UNREADABLE_ITEMS)
    assert "a column named id" in error.splitlines()[0]
    assert all(form in error for form in REMINDER_ANSWER_FORMS)
    assert not any(form in error for form in REMINDER_TIME_FORMS)
    async with e2e_harness.sessions() as session:
        assert list(await session.scalars(select(Reminder))) == []


async def test_rm_cond_031_a_select_the_session_cannot_write_gets_the_answer_forms(e2e_harness):
    """RM-COND-031 — tests/brd/reminders.feature"""
    advisor, provider = e2e_harness.advisor(
        unclear_script(
            turn(
                ("set_condition", {"items": "any Check", "answer": "missed", "count": 3}),
                prefix="setup",
            ),
            *(["I am not sure."] * (MINI_SESSION_REPAIR_ROUNDS + 1)),
            when="every 3 Missed of any Check",
        )
    )

    await advisor.handle("cheer me on after 3 misses", source_message_id=1)

    (error,) = unclear_errors(provider)
    assert error.splitlines()[0] == UNREADABLE_ITEMS
    assert all(form in error for form in REMINDER_ANSWER_FORMS)
    assert not any(form in error for form in REMINDER_TIME_FORMS)


async def test_rm_cond_034_a_count_one_series_never_reaches_is_refused(e2e_harness):
    """RM-COND-034 — tests/brd/reminders.feature"""
    async with e2e_harness.sessions() as session:
        once = await create_check(session, title="Slept 8 hours?")
        await session.commit()
    advisor, provider = e2e_harness.advisor(
        unclear_script(
            *condition_turns(f"Check #{once.id}", series_select(once.id), in_row=True),
            when=f"every 3 Missed in a row of Check #{once.id}",
        )
    )

    await advisor.handle("cheer me on after 3 misses", source_message_id=1)

    (error,) = unclear_errors(provider)
    assert error.splitlines()[0] == (
        f"Check #{once.id} runs once in all, so a count of 3 never comes."
    )
    assert all(form in error for form in REMINDER_ANSWER_FORMS)
    # A count it can reach is kept.
    each_time, _provider = e2e_harness.advisor(
        [
            route_turn("workspace_mutator"),
            turn(
                (
                    "reminder",
                    {
                        "mode": "create",
                        "instruction": "Cheer me on.",
                        "when": f"each Missed of Check #{once.id}",
                    },
                )
            ),
            "I set that up for you.",
            *condition_turns(f"Check #{once.id}", series_select(once.id), count=1),
        ]
    )
    outcome = await each_time.handle("cheer me on when I miss it", source_message_id=2)
    assert outcome.kind is AIOutcomeKind.PROPOSAL


async def test_rm_unclear_028_words_that_meant_a_time_get_the_time_forms(e2e_harness):
    """RM-UNCLEAR-028 — tests/brd/reminders.feature"""
    advisor, provider = e2e_harness.advisor(
        unclear_script(
            turn(
                ("not_clear_enough", {"reason": "No hour was given.", "about": "time"}),
                prefix="setup",
            ),
            when="next Thursday",
        )
    )

    await advisor.handle("remind me next Thursday to see the dentist", source_message_id=1)

    (error,) = unclear_errors(provider)
    assert error.splitlines()[0] == "No hour was given."
    assert all(form in error for form in REMINDER_TIME_FORMS)
    assert not any(form in error for form in REMINDER_ANSWER_FORMS)
    async with e2e_harness.sessions() as session:
        assert list(await session.scalars(select(Reminder))) == []


async def test_rm_unclear_028_a_time_no_schedule_can_keep_gets_the_time_forms(e2e_harness):
    """RM-UNCLEAR-028 — tests/brd/reminders.feature"""
    advisor, provider = e2e_harness.advisor(
        unclear_script(
            turn(("set_reminder_config", {"date": "20.10.2099"}), prefix="setup"),
            when="on 20 October 2099",
        )
    )

    await advisor.handle("remind me on 20 October 2099", source_message_id=1)

    (error,) = unclear_errors(provider)
    assert "needs an hour" in error.splitlines()[0]
    assert all(form in error for form in REMINDER_TIME_FORMS)
    assert not any(form in error for form in REMINDER_ANSWER_FORMS)


async def test_rm_unclear_028_words_the_session_cannot_read_get_both_forms(e2e_harness):
    """RM-UNCLEAR-028 — tests/brd/reminders.feature"""
    advisor, provider = e2e_harness.advisor(
        unclear_script(
            *(["I am not sure."] * (MINI_SESSION_REPAIR_ROUNDS + 1)),
            when="whenever it feels right",
        )
    )

    outcome = await advisor.handle("remind me whenever it feels right", source_message_id=1)

    # The mutator lived to ask: its call was refused, not its session ended.
    assert outcome.message == "When should it fire?"
    (error,) = unclear_errors(provider)
    assert error.splitlines()[0] == UNREADABLE_WHEN
    assert all(form in error for form in REMINDER_TIME_FORMS + REMINDER_ANSWER_FORMS)


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
            route_turn("workspace_mutator"),
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
        self.date = datetime.now(UTC)
        self.text = ""
        self.rendered: list[str] = []
        self.markups: list[object] = []

    async def edit_text(self, text, *, reply_markup=None, parse_mode=None):
        del parse_mode
        self.rendered.append(text)
        self.markups.append(reply_markup)
        return self

    async def answer(
        self, text, *, reply_markup=None, parse_mode=None, disable_notification=False
    ):
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
        root=advisor,
        history=_TestHistory(),
        owner_id=42,
        turn=TurnManager(),
        screens=SCREENS,
        search=None,
        chat=ChatHost(TelegramNotes(harness.sessions), spawn=spawn_timer),
        callback_actions=FEATURE_CALLBACK_ACTIONS,
        text_inputs=FEATURE_TEXT_INPUTS,
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
            route_turn("workspace_mutator"),
            turn(("remove", {"entity": "reminder", "id": reminder_id})),
            "That one is gone.",
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