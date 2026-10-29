from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from advisor_e2e_helpers import PLAN, route_turn, save
from schedule_helpers import create_card, create_check
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
from safwa.features.cards.model import Card
from safwa.features.checks.model import CheckOutcome
from safwa.features.checks.use_cases import resolve_check
from safwa.features.reminders.agent import (
    COUNTS_ANSWERS,
    NO_MISSED,
    REMINDER_CONDITION_PROMPT,
    REMINDER_TIME_FORMS,
    REMINDER_WHEN_PROMPT,
    UNREADABLE_WHEN,
)
from safwa.features.reminders.api import schedule_from_payload, schedule_of
from safwa.features.reminders.conditions import NAME_BY_ID, ONE_KIND
from safwa.features.reminders.items import OPEN_ITEMS, SCHEDULED
from safwa.features.reminders.model import Reminder
from safwa.features.schedules.api import entity_reminder
from telegram_llm import ChatHost, DialogueMessage
from tg_agent_shell.ai.mini import MINI_SESSION_REPAIR_ROUNDS, PLAN_FIRST
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


def reminder_script(config: dict[str, object], *, remind_text: str, when: str):
    """The provider calls in the order the advisor makes them.

    The setup mini-session runs during materialization, after the agent loop has already
    finished, so its answer is the last response in the queue.
    """
    return [
        route_turn("reminder"),
        turn(("reminder", {"mode": "create", "remind_text": remind_text, "when": when})),
        "I set that up for you.",
        turn(("set_reminder_config", config), prefix="setup"),
    ]


async def test_a_reminder_reaches_a_proposal_and_save_creates_the_row(e2e_harness):
    """RM-WRITE-008 — tests/brd/reminders.feature"""
    advisor, _provider = e2e_harness.advisor(
        reminder_script(
            {"days": ["Mon", "Tue", "Wed", "Thu", "Fri"], "time": "08:30"},
            remind_text="Ask me what to start with today.",
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
        assert reminder.remind_text == "Ask me what to start with today."
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
            remind_text="Check my posture — Check #5.",
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
    advisor, provider = e2e_harness.advisor(
        reminder_script(
            {"interval_minutes": 120},
            remind_text="Check my posture — Check #5.",
            when="every couple of hours",
        )
    )

    outcome = await advisor.handle("nudge me about posture", source_message_id=1)

    change = e2e_harness.reviews.proposal(outcome.proposal_id).changes[0]
    values = dict(change.values)
    assert "when" not in values  # the free text does not survive into the proposal
    assert values["schedule"]["interval_minutes"] == 120
    assert values["schedule_text"] == "every 2 hours"
    prompt = f"{REMINDER_WHEN_PROMPT}\n{PLAN_FIRST}"
    read = next(call for call in provider.calls if call[0]["content"] == prompt)
    told = "\n".join(str(message["content"]) for message in read[1:])
    # The owner's own words as the reminder part read them, then the phrase that says which.
    assert told.index(">nudge me about posture</User>") < told.index(
        "Timing phrase: every couple of hours"
    )
    assert "Check my posture" not in told


def refusals(provider, code: str = "schedule_unclear") -> list[dict[str, object]]:
    """Each refused `reminder` call with that code, as the model read it."""
    bodies = []
    for call in provider.calls:
        for message in call:
            if message.get("role") != "tool":
                continue
            body = json.loads(message["content"])
            if isinstance(body, dict) and body.get("code") == code:
                bodies.append(body)
    return bodies


def unclear_errors(provider) -> list[str]:
    """What each refused `reminder` call told the model was unclear."""
    return [str(body["error"]) for body in refusals(provider)]


def unclear_script(*setup, when: str):
    """A Reminder the setup session answers with `setup`, refused, then asked about."""
    return [
        route_turn("reminder"),
        turn(("reminder", {"mode": "create", "remind_text": "Cheer me on.", "when": when})),
        "Sure.",
        *setup,
        "When should it fire?",
        "When should it fire?",
    ]


def condition_call(on_answers: str = "every miss of posture") -> ProviderTurn:
    """The subagent's `reminder` on answers, in the owner's words: the items are its own
    session's to pick."""
    return turn(
        ("reminder", {"mode": "create", "remind_text": "Cheer me on.", "on_answers": on_answers})
    )


def condition_fields(items: str, answer: str = "no", count: int = 3, **extra) -> ProviderTurn:
    """What the condition's own session picks and reads."""
    fields = {"items": items, "answer": answer, "count": count, **extra}
    return turn(("set_condition", fields), prefix="condition")


def condition_turns(items: str, answer: str = "no", count: int = 3, **extra) -> list[object]:
    return [route_turn("reminder"), condition_call(), condition_fields(items, answer, count, **extra)]


def condition_reads(provider) -> list[list[dict[str, object]]]:
    """Each request of the condition's own session."""
    prompt = f"{REMINDER_CONDITION_PROMPT}\n{PLAN_FIRST}"
    return [call for call in provider.calls if call[0]["content"] == prompt]


def refused_condition_script(*read: ProviderTurn) -> list[object]:
    """A Reminder on answers whose preparation refuses it once its session reads `read`, then
    asked about."""
    return [
        route_turn("reminder"),
        condition_call(),
        *read,
        "Which do you mean?",
        "Which do you mean?",
    ]


async def test_rm_cond_030_a_condition_is_proposed_with_its_series_and_what_it_finds(
    e2e_harness,
):
    """RM-COND-030 — tests/brd/reminders.feature"""
    async with e2e_harness.sessions() as session:
        live = await create_check(session, title="Sat straight?", schedule="after completion")
        first = live.id
        _, live = await resolve_check(session, live.id, CheckOutcome.MISSED)
        await session.commit()
        named = live.id
    # Its own session picks the items and reads the words; the subagent says on answers, in the
    # owner's words.
    advisor, provider = e2e_harness.advisor(condition_turns(f"Check #{named}", in_row=True))

    outcome = await advisor.handle("cheer me on when I miss posture", source_message_id=1)

    assert outcome.kind is AIOutcomeKind.PROPOSAL
    values = dict(e2e_harness.reviews.proposal(outcome.proposal_id).changes[0].values)
    assert "condition" not in values
    assert values["schedule_text"] == f"every 3 Missed in a row of Check #{named}"
    assert values["finds"] == "«Sat straight?». 1 Missed in a row now."
    [read] = condition_reads(provider)
    told = "\n".join(str(message["content"]) for message in read[1:])
    # The owner's own words as the reminder part read them, then the open items it read.
    words = told.index(">cheer me on when I miss posture</User>")
    listed = told.index(f"- Check #{named} «Sat straight?»; schedule: after completion")
    assert words < told.index(OPEN_ITEMS) < listed
    assert "every miss of posture" not in told
    assert "Cheer me on." not in told
    async with e2e_harness.sessions() as session:
        (reminder_id,) = await approve_proposal(
            session, e2e_harness.reviews, PROPOSALS, outcome.proposal_id
        )
        await session.commit()
        reminder = await session.get(Reminder, reminder_id)
        # The later copy named the series by the id it keeps.
        assert reminder.condition == {
            "items": f"Check #{named}",
            "series": [first],
            "kind": "check",
            "answer": "no",
            "count": 3,
            "in_row": True,
            "once": False,
        }
        assert reminder.next_fire_at is None


async def test_rm_cond_040_after_n_is_proposed_and_saved_once(e2e_harness):
    """RM-COND-040 — tests/brd/reminders.feature"""
    async with e2e_harness.sessions() as session:
        check = await create_check(session, title="Meditated?", schedule="after completion")
        await session.commit()
        named = check.id
    advisor, _provider = e2e_harness.advisor(
        condition_turns(f"Check #{named}", answer="yes", in_row=True, once=True)
    )

    outcome = await advisor.handle("tell me when I meditate 3 days in a row", source_message_id=1)

    assert outcome.kind is AIOutcomeKind.PROPOSAL
    values = dict(e2e_harness.reviews.proposal(outcome.proposal_id).changes[0].values)
    assert values["schedule_text"] == f"after 3 Passed in a row of Check #{named}"
    assert values["finds"] == "«Meditated?». 0 Passed in a row now."
    async with e2e_harness.sessions() as session:
        (reminder_id,) = await approve_proposal(
            session, e2e_harness.reviews, PROPOSALS, outcome.proposal_id
        )
        await session.commit()
        assert (await session.get(Reminder, reminder_id)).condition["once"] is True


async def test_rm_cond_030_several_items_by_id_are_one_condition(e2e_harness):
    """RM-COND-030 — tests/brd/reminders.feature"""
    async with e2e_harness.sessions() as session:
        posture = await create_check(session, title="Sat straight?", schedule="after completion")
        water = await create_check(session, title="Drank water?", schedule="after completion")
        await session.commit()
        ids = (posture.id, water.id)
    advisor, _provider = e2e_harness.advisor(condition_turns(f"Check #{ids[0]}, Check #{ids[1]}"))

    outcome = await advisor.handle("cheer me on after 3 misses", source_message_id=1)

    assert outcome.kind is AIOutcomeKind.PROPOSAL
    values = dict(e2e_harness.reviews.proposal(outcome.proposal_id).changes[0].values)
    assert schedule_from_payload(values["schedule"]).condition.series == ids
    assert values["finds"] == "«Drank water?», «Sat straight?». 0 Missed now."


@pytest.mark.parametrize(
    ("items", "error"),
    [
        ("any Check", NAME_BY_ID),
        ("Check #{check}, Action #{action}", ONE_KIND),
        ("Check #999", "Check #999 is not in Open items."),
        ("Check #{unscheduled}", "Check #{unscheduled} is not in Open items."),
    ],
)
async def test_rm_cond_031_items_not_from_open_items_go_back_to_the_session(
    e2e_harness, items, error
):
    """RM-COND-031 — tests/brd/reminders.feature"""
    async with e2e_harness.sessions() as session:
        check = await create_check(session, title="Sat straight?", schedule="after completion")
        unscheduled = await create_check(session, title="Slept 8 hours?")
        action = await create_card(
            session, kind="action", title="Run", stage="today", schedule="Mon Wed Fri 07:00"
        )
        await session.commit()
        ids = {"check": check.id, "action": action.id, "unscheduled": unscheduled.id}
    advisor, provider = e2e_harness.advisor(
        [
            route_turn("reminder"),
            condition_call(),
            condition_fields(items.format(**ids)),
            condition_fields(f"Check #{check.id}"),
        ]
    )

    outcome = await advisor.handle("cheer me on after 3 misses", source_message_id=1)

    # The session is told, and names them again: nothing reaches Safwa.
    assert outcome.kind is AIOutcomeKind.PROPOSAL
    repaired = json.loads(str(condition_reads(provider)[1][-1]["content"]))
    assert repaired["code"] == "invalid_arguments"
    assert error.format(**ids) in repaired["error"]


async def test_rm_cond_034_a_count_one_series_never_reaches_is_refused(e2e_harness):
    """RM-COND-034 — tests/brd/reminders.feature"""
    async with e2e_harness.sessions() as session:
        once = await create_check(session, title="Slept 8 hours?", schedule="20.12.2099 10:00")
        await session.commit()
    advisor, provider = e2e_harness.advisor(
        refused_condition_script(condition_fields(f"Check #{once.id}", in_row=True))
    )

    await advisor.handle("cheer me on after 3 misses", source_message_id=1)

    (told,) = refusals(provider, "count_never_reached")
    assert told["error"] == f"Check #{once.id} does not repeat, so a count of 3 never comes."
    assert "Tell the user" in told["hint"]
    # A count it can reach is kept.
    each_time, _provider = e2e_harness.advisor(condition_turns(f"Check #{once.id}", count=1))
    outcome = await each_time.handle("cheer me on when I miss it", source_message_id=2)
    assert outcome.kind is AIOutcomeKind.PROPOSAL


async def test_rm_cond_030_missed_of_actions_goes_back_to_safwa(e2e_harness):
    """RM-COND-030 — tests/brd/reminders.feature"""
    async with e2e_harness.sessions() as session:
        run = await create_card(
            session, kind="action", title="Run", stage="today", schedule="Mon Wed Fri 07:00"
        )
        await session.commit()
        named = run.id
    advisor, provider = e2e_harness.advisor(
        [
            route_turn("reminder"),
            condition_call(),
            condition_fields(f"Action #{named}"),
            condition_call(),
            condition_fields(f"Action #{named}", answer="yes"),
        ]
    )

    outcome = await advisor.handle("cheer me on when I skip my run 3 times", source_message_id=1)

    assert outcome.kind is AIOutcomeKind.PROPOSAL
    (told,) = refusals(provider, "condition_unclear")
    assert told["error"] == NO_MISSED


async def test_rm_cond_030_words_without_a_count_go_back_to_ask_the_owner(e2e_harness):
    """RM-COND-030 — tests/brd/reminders.feature"""
    async with e2e_harness.sessions() as session:
        await create_check(session, title="Sat straight?", schedule="after completion")
        await session.commit()
    unclear = turn(
        ("not_clear_enough", {"reason": "The words give no number."}), prefix="condition"
    )
    advisor, provider = e2e_harness.advisor(
        refused_condition_script(unclear)
    )

    await advisor.handle("cheer me on after a decent run", source_message_id=1)

    (told,) = refusals(provider, "condition_unclear")
    assert told["error"] == "The words give no number."
    assert "Ask the user" in told["hint"]
    async with e2e_harness.sessions() as session:
        assert list(await session.scalars(select(Reminder))) == []


async def test_rm_unclear_028_words_that_count_answers_go_back_to_be_a_condition(e2e_harness):
    """RM-UNCLEAR-028 — tests/brd/reminders.feature"""
    async with e2e_harness.sessions() as session:
        check = await create_check(session, title="Sat straight?", schedule="after completion")
        await session.commit()
        named = check.id
    advisor, provider = e2e_harness.advisor(
        [
            route_turn("reminder"),
            turn(
                (
                    "reminder",
                    {
                        "mode": "create",
                        "remind_text": "Cheer me on.",
                        "when": f"every 3 Missed of Check #{named}",
                    },
                )
            ),
            "Sure.",
            turn(
                ("not_clear_enough", {"reason": "It counts Missed.", "about": "answers"}),
                prefix="setup",
            ),
            condition_call(),
            condition_fields(f"Check #{named}"),
        ]
    )

    outcome = await advisor.handle("cheer me on after 3 misses", source_message_id=1)

    (told,) = refusals(provider, "when_counts_answers")
    assert told["error"] == COUNTS_ANSWERS
    assert "in on_answers instead of when" in told["hint"]
    # No forms: the owner is not asked to say it again.
    assert not any(form in told["error"] for form in REMINDER_TIME_FORMS)
    assert outcome.kind is AIOutcomeKind.PROPOSAL
    values = dict(e2e_harness.reviews.proposal(outcome.proposal_id).changes[0].values)
    assert values["schedule_text"] == f"every 3 Missed of Check #{named}"


async def test_rm_unclear_028_words_that_meant_a_time_get_the_time_forms(e2e_harness):
    """RM-SCHEDULE-002 — tests/brd/reminders.feature

    The refusal also carries the time forms required by RM-UNCLEAR-028.
    """
    advisor, provider = e2e_harness.advisor(
        unclear_script(
            turn(
                ("not_clear_enough", {"reason": "No hour was given.", "about": "time"}),
                prefix="setup",
            ),
            when="next Thursday",
        )
    )

    outcome = await advisor.handle("remind me next Thursday to see the dentist", source_message_id=1)
    assert outcome.kind is AIOutcomeKind.ANSWER
    assert outcome.message == "When should it fire?"
    assert refusals(provider)[0]["retryable"] is True

    (error,) = unclear_errors(provider)
    assert error.splitlines()[0] == "No hour was given."
    assert all(form in error for form in REMINDER_TIME_FORMS)
    async with e2e_harness.sessions() as session:
        assert list(await session.scalars(select(Reminder))) == []


async def test_rm_unclear_028_a_time_no_schedule_can_keep_gets_the_time_forms(e2e_harness):
    """RM-UNCLEAR-028 — tests/brd/reminders.feature"""
    advisor, provider = e2e_harness.advisor(
        unclear_script(
            # The reason goes back to the model each time, and to the owner once the prose
            # before them and these have spent every repair round.
            *[turn(("set_reminder_config", {"first_day": "20.10.2099"}), prefix="setup")]
            * MINI_SESSION_REPAIR_ROUNDS,
            when="on 20 October 2099",
        )
    )

    await advisor.handle("remind me on 20 October 2099", source_message_id=1)

    (error,) = unclear_errors(provider)
    assert "needs an hour" in error.splitlines()[0]
    assert all(form in error for form in REMINDER_TIME_FORMS)


async def test_rm_unclear_028_words_the_session_cannot_read_get_the_time_forms(e2e_harness):
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
    assert all(form in error for form in REMINDER_TIME_FORMS)


async def test_editing_a_reminder_without_when_never_touches_the_schedule(e2e_harness):
    """RM-WRITE-009 — tests/brd/reminders.feature"""
    setup, _provider = e2e_harness.advisor(
        reminder_script(
            {"interval_minutes": 120},
            remind_text="Check my posture — Check #5.",
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
            route_turn("reminder"),
            turn(
                (
                    "reminder",
                    {
                        "mode": "update",
                        "id": reminder_id,
                        "remind_text": "Check my posture properly — Check #5.",
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
        assert reminder.remind_text == "Check my posture properly — Check #5."
        assert reminder.next_fire_at == before
        assert schedule_of(reminder).interval_minutes == 120


async def test_ai_reminders_view_is_readable(e2e_harness):
    """RM-READ-024 — tests/brd/reminders.feature"""
    advisor, _provider = e2e_harness.advisor(
        reminder_script(
            {"interval_minutes": 120},
            remind_text="Check my posture — Check #5.",
            when="every two hours",
        )
    )
    outcome = await advisor.handle("nudge me", source_message_id=1)
    async with e2e_harness.sessions() as session:
        await approve_proposal(session, e2e_harness.reviews, PROPOSALS, outcome.proposal_id)
        await session.commit()

    reader = advisor.adapters.query_runner
    result = await reader.run(
        "SELECT id, remind_text, schedule_kind, next_fire_at_local FROM ai_reminders"
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
            remind_text="Check my posture — Check #5.",
            when="every two hours",
        )
    )
    outcome = await setup.handle("nudge me", source_message_id=1)
    async with e2e_harness.sessions() as session:
        reminder_id = (await approve_proposal(session, e2e_harness.reviews, PROPOSALS, outcome.proposal_id))[0]
        await session.commit()

    advisor, _provider = e2e_harness.advisor(
        [
            route_turn("reminder"),
            turn(("reminder", {"mode": "delete", "id": reminder_id})),
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


async def test_rm_agent_041_the_reminder_part_picks_from_its_open_items(e2e_harness):
    """RM-AGENT-041 — tests/brd/reminders.feature"""
    async with e2e_harness.sessions() as session:
        meditated = await create_check(session, title="Помедитировал?", schedule="daily 21:00")
        await session.commit()
    advisor, provider = e2e_harness.advisor(
        [
            route_turn("reminder"),
            turn(("remind", {"item": "check", "id": meditated.id, "minutes_before": 0})),
        ]
    )

    outcome = await advisor.handle("remind me to meditate")

    assert outcome.kind is AIOutcomeKind.PROPOSAL
    line = f"- Check #{meditated.id} «Помедитировал?»; schedule: daily 21:00"
    # The Advisor's request has no such block; the subagent's has it before its first step.
    assert not any(line in str(message["content"]) for message in provider.calls[0])
    assert any(
        f"{SCHEDULED}\n{line}" in str(message["content"]) for message in provider.calls[1]
    )


async def test_rm_agent_041_a_new_item_is_proposed_with_its_remind(e2e_harness):
    """RM-AGENT-041 — tests/brd/reminders.feature"""
    every_day = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    advisor, provider = e2e_harness.advisor(
        [
            route_turn("workspace_mutator"),
            turn(
                (
                    "action",
                    {"mode": "create", "title": "Stretch", "schedule": "every day at 20:00", "remind": 30},
                )
            ),
            turn(("set_schedule_config", {"days": every_day, "time": "20:00"}), prefix="schedule"),
        ]
    )

    outcome = await advisor.handle("Add Stretch every day at 20:00 and remind me 30 minutes before")
    [change] = advisor.reviews.proposal(outcome.proposal_id).changes
    assert (change.entity, change.values["remind"]) == ("card", 30)
    provider.responses.extend(["Stretch is saved, with Remind 30 minutes before.", "Done."])
    answered, _affected = await save(advisor, outcome.proposal_id)

    assert answered.kind is AIOutcomeKind.ANSWER
    async with e2e_harness.sessions() as session:
        card = await session.scalar(select(Card).where(Card.title == "Stretch"))
        assert (await entity_reminder(session, card)).lead_minutes == 30
