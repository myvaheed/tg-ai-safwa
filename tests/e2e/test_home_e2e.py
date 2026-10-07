"""A quiet chat cleared through the last user message, with a new Home dashboard.

The real database, the real notes, the real tick poll and the real hook; Telegram is the
queue fake and the model a script, each at its network boundary.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from telegram_fakes import QueueTestMessage, spawn_timer

from llm_gateway import CompletionRequest, CompletionTurn, ToolCall
from safwa.bootstrap.modules import FEATURE_COMMANDS, SCREENS
from safwa.features.home import telegram as home_telegram
from safwa.features.home.api import menu_markup
from safwa.features.home.dashboard import dashboard_text
from safwa.features.home.hooks import HOME_HOOK, HOME_LOOK_EVERY
from safwa.features.home.motivation import Motivator
from safwa.features.home.telegram import command_clear, render_home
from safwa.features.profile.model import HOME_AFTER_MINUTES_DEFAULT
from safwa.features.values.use_cases import create_value
from telegram_llm import ChatHost, Note
from tg_agent_shell.cues.initiatives import TickPoll
from tg_agent_shell.cues.runtime import tick_chat
from tg_agent_shell.foundation.clock import utcnow
from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.history import TelegramNotes
from tg_agent_shell.hooks.registry import HookRegistry
from tg_agent_shell.proposals.store import ProposalStore
from tg_agent_shell.telegram import commands as commands_module
from tg_agent_shell.telegram import dismiss_prior_ui
from tg_agent_shell.telegram.commands import navigation
from tg_agent_shell.turn import TurnManager

pytestmark = pytest.mark.e2e

CHAT_ID = 700
WORDS = "A walk today keeps it going."


class Model:
    """Words for every Value; `hold` keeps the first call open until the test lets it go."""

    def __init__(self, *, hold: bool = False) -> None:
        self.hold = hold
        self.reached = asyncio.Event()
        self.go = asyncio.Event()
        self.calls = 0

    async def complete(self, request: CompletionRequest) -> CompletionTurn:
        self.calls += 1
        if self.hold:
            self.reached.set()
            await self.go.wait()
        call = ToolCall(
            "c1", "motivate", json.dumps({"words": [{"value": 1, "text": WORDS}]})
        )
        return CompletionTurn(content=None, tool_calls=(call,))


def _services(harness, model: Model, *, busy: bool = False) -> SimpleNamespace:
    reviews = SimpleNamespace(busy=True) if busy else ProposalStore()
    spawned: list[asyncio.Task[None]] = []

    def spawn(work, name: str) -> asyncio.Task[None]:
        task = spawn_timer(work, name)
        spawned.append(task)
        return task

    return SimpleNamespace(
        sessions=harness.sessions,
        owner_id=42,
        turn=TurnManager(),
        chat=ChatHost(TelegramNotes(harness.sessions), spawn=spawn),
        spawned=spawned,
        root=SimpleNamespace(reviews=reviews),
        screens=SCREENS,
        commands=FEATURE_COMMANDS,
        bot_username="safwa_ai_bot",
        features=SimpleNamespace(motivator=Motivator(model, spawn=spawn_timer)),
        owner_acted_at=utcnow() - timedelta(minutes=HOME_AFTER_MINUTES_DEFAULT + 1),
    )


async def _settled(services: SimpleNamespace) -> None:
    """Every task the chat started, done."""
    await asyncio.wait_for(asyncio.gather(*services.spawned), timeout=5)


class Looks:
    """The tick poll with the Home hook alone, looking once per interval."""

    def __init__(self, services: SimpleNamespace, anchor: QueueTestMessage) -> None:
        self.moment = utcnow()
        self.poll = TickPoll(
            HookRegistry.of((HOME_HOOK,), owners=frozenset({"home"})),
            services.sessions,
            resources=services.features,
            timezone="UTC",
            now=self.moment,
            chat=tick_chat(services, anchor),  # type: ignore[arg-type]
        )

    async def next(self) -> None:
        self.moment += HOME_LOOK_EVERY
        await self.poll.look(self.moment)


async def _keep(harness, message_id: int, kind: MessageKind, text: str, at=None) -> None:
    direction = "in" if kind is MessageKind.DIALOGUE_USER else "out"
    await TelegramNotes(harness.sessions).write(
        Note(CHAT_ID, message_id, direction, kind.value, text=text, at=at or utcnow())
    )


class _Press:
    """A press of a button that only takes the owner somewhere, on one message."""

    def __init__(self, nav: str, message: QueueTestMessage) -> None:
        self.data = f"nav:{nav}"
        self.message = message

    async def answer(self, *_args, **_kwargs) -> None:
        return None


def _labels(markup) -> list[str]:
    return [button.text for row in markup.inline_keyboard for button in row]


MENU = _labels(menu_markup(FEATURE_COMMANDS))


async def _kinds(harness) -> dict[int, str]:
    notes = TelegramNotes(harness.sessions)
    return {
        message_id: note.kind
        for message_id in range(1, 2_000)
        if (note := await notes.note(CHAT_ID, message_id)) is not None
    }


async def _a_chat(harness) -> None:
    """What a morning leaves: a screen, the owner's words, and an answer."""
    async with harness.sessions() as session:
        await create_value(session, "Health", active=True)
        await session.commit()
    # Three days old: past what Telegram lets a bot delete.
    await _keep(
        harness, 1000, MessageKind.DIALOGUE_USER, "Hello", at=utcnow() - timedelta(days=3)
    )
    await _keep(harness, 1100, MessageKind.DIALOGUE_USER, "What is next?")
    await _keep(harness, 1101, MessageKind.DIALOGUE_ASSISTANT, "Pay the rent.")
    await _keep(harness, 1099, MessageKind.DASHBOARD, "<b>Today</b>")


async def test_a_quiet_chat_keeps_only_what_safwa_said_unasked_since_the_owner_wrote(
    e2e_harness,
) -> None:
    """HM-QUIET-003 — tests/brd/home.feature"""
    await _a_chat(e2e_harness)
    services = _services(e2e_harness, Model())
    anchor = QueueTestMessage(message_id=150, is_bot=False, answer_as_new=True)
    looks = Looks(services, anchor)

    await looks.next()

    [dashboard] = anchor.sent
    assert dashboard.message_id == 1150
    assert anchor.bot.silent == [dashboard.text]
    assert "<b>💎 Values in focus</b>" in dashboard.text and WORDS in dashboard.text
    # Everything before it goes, the Advisor's reply included; three days old is too old.
    assert anchor.bot.deleted == list(range(1099, 1150))
    # What was said is still kept; the screen is gone with its message.
    assert await _kinds(e2e_harness) == {
        1000: MessageKind.DIALOGUE_USER.value,
        1100: MessageKind.DIALOGUE_USER.value,
        1101: MessageKind.DIALOGUE_ASSISTANT.value,
        1150: MessageKind.HOME.value,
    }

    # Nothing came and the owner did nothing: the dashboard is left as it is.
    await looks.next()
    assert len(anchor.sent) == 1

    # A Reminder said while the owner was away stays through repeated Home draws.
    await _keep(e2e_harness, 1151, MessageKind.CUE, "Time to stretch.")
    anchor.message_id = 160
    deleted = len(anchor.bot.deleted)
    await looks.next()
    assert len(anchor.sent) == 2
    assert 1150 in anchor.bot.deleted[deleted:] and 1151 not in anchor.bot.deleted
    assert (await _kinds(e2e_harness))[1151] == MessageKind.CUE.value
    await looks.next()
    assert len(anchor.sent) == 2


async def test_a_waiting_review_or_a_short_quiet_clears_nothing(e2e_harness) -> None:
    """HM-QUIET-003 — tests/brd/home.feature"""
    await _a_chat(e2e_harness)
    model = Model()
    anchor = QueueTestMessage(message_id=150, is_bot=False, answer_as_new=True)

    await Looks(_services(e2e_harness, model, busy=True), anchor).next()
    recent = _services(e2e_harness, model)
    recent.owner_acted_at = utcnow() - timedelta(minutes=HOME_AFTER_MINUTES_DEFAULT - 1)
    await Looks(recent, anchor).next()

    assert anchor.sent == [] and anchor.bot.deleted == []
    # Nothing was going to be drawn, so nothing was written for it.
    assert model.calls == 0


async def test_the_owner_acting_stops_the_clearing(e2e_harness) -> None:
    """HM-QUIET-003 — tests/brd/home.feature"""
    await _a_chat(e2e_harness)
    model = Model(hold=True)
    services = _services(e2e_harness, model)
    anchor = QueueTestMessage(message_id=150, is_bot=False, answer_as_new=True)

    clearing = asyncio.create_task(Looks(services, anchor).next())
    await asyncio.wait_for(model.reached.wait(), timeout=5)
    # What the middleware does when the owner's message or press arrives.
    services.owner_acted_at = utcnow()
    services.turn.cancel()
    model.go.set()
    await asyncio.wait_for(clearing, timeout=5)

    assert anchor.sent == [] and anchor.bot.deleted == []
    assert services.turn.active is False


async def test_a_dashboard_from_an_earlier_day_is_drawn_again(e2e_harness) -> None:
    """HM-QUIET-004 — tests/brd/home.feature"""
    yesterday = datetime.now(UTC) - timedelta(days=1, hours=1)
    await _keep(e2e_harness, 1140, MessageKind.HOME, "<b>🏠 Yesterday</b>", at=yesterday)
    services = _services(e2e_harness, Model())
    services.owner_acted_at = yesterday - timedelta(hours=1)
    anchor = QueueTestMessage(message_id=150, is_bot=False, answer_as_new=True)

    await Looks(services, anchor).next()

    assert [message.message_id for message in anchor.sent] == [1150]
    assert 1140 in anchor.bot.deleted
    assert (await _kinds(e2e_harness)) == {1150: MessageKind.HOME.value}


async def test_the_dashboard_stays_until_the_next_clear(e2e_harness, monkeypatch) -> None:
    """HM-STAYS-005 — tests/brd/home.feature"""
    await _a_chat(e2e_harness)
    services = _services(e2e_harness, Model())
    anchor = QueueTestMessage(message_id=150, is_bot=False, answer_as_new=True)
    await Looks(services, anchor).next()
    [dashboard] = anchor.sent
    assert _labels(anchor.markups[-1]) == ["☰ Menu"]
    # A screen the owner opened after it.
    await _keep(e2e_harness, 1170, MessageKind.DASHBOARD, "<b>Today</b>")

    # ☰ Menu unfolds under the same words, and the screen below stays.
    await navigation(_Press("home", dashboard), services)
    assert anchor.bot.keyboards[-1][0] == dashboard.message_id
    assert _labels(anchor.bot.keyboards[-1][1]) == MENU
    assert len(anchor.sent) == 1 and 1170 not in anchor.bot.deleted
    assert (await _kinds(e2e_harness))[1170] == MessageKind.DASHBOARD.value

    # A screen opened from it arrives below, and the dashboard folds back.
    below = QueueTestMessage(message_id=1180, is_bot=False, answer_as_new=True, parent=anchor)
    monkeypatch.setattr(commands_module, "owner_anchor", lambda _bot, _owner_id: below)
    await navigation(_Press("tags", dashboard), services)
    assert anchor.bot.keyboards[-1][0] == dashboard.message_id
    assert _labels(anchor.bot.keyboards[-1][1]) == ["☰ Menu"]
    assert anchor.sent[-1].text.startswith("<b>Tags</b>")
    assert dashboard.message_id not in anchor.bot.deleted

    # The owner writes, and then opens Home with /start.
    deleted = len(anchor.bot.deleted)
    owner = QueueTestMessage(message_id=1160, is_bot=False, answer_as_new=True, parent=anchor)
    await dismiss_prior_ui(owner, services)
    await render_home(owner, services)

    assert dashboard.message_id not in anchor.bot.deleted[deleted:]
    assert (await _kinds(e2e_harness))[dashboard.message_id] == MessageKind.HOME.value
    assert _labels(anchor.markups[-1]) == MENU


async def test_start_opens_home_at_once_and_puts_the_words_in_when_they_come(
    e2e_harness,
) -> None:
    """HM-START-011 — tests/brd/home.feature"""
    await _a_chat(e2e_harness)
    model = Model(hold=True)
    services = _services(e2e_harness, model)
    owner = QueueTestMessage(message_id=170, text="/start", is_bot=False, answer_as_new=True)

    # Home is drawn while the words are still being written.
    await render_home(owner, services)
    await asyncio.wait_for(model.reached.wait(), timeout=5)

    [home] = owner.sent
    assert home.text.startswith("<b>🏠 ")
    assert "<b>💎 Values in focus</b>" in home.text and WORDS not in home.text
    assert _labels(owner.markups[-1]) == MENU
    assert owner.bot.deleted == [] and owner.bot.silent == []
    kinds = await _kinds(e2e_harness)
    assert kinds[home.message_id] == MessageKind.DASHBOARD.value
    assert kinds[1100] == MessageKind.DIALOGUE_USER.value

    model.go.set()
    await _settled(services)
    [(edited, markup)] = owner.bot.edited
    assert edited == home.message_id and _labels(markup) == MENU
    assert WORDS in owner.bot.edits[-1]
    note = await TelegramNotes(e2e_harness.sessions).note(CHAT_ID, home.message_id)
    assert note.kind == MessageKind.DASHBOARD.value and WORDS in note.text

    # A Home drawn soon after carries the words at once, and asks for none.
    services.owner_acted_at = utcnow()
    again = QueueTestMessage(
        message_id=180, text="/start", is_bot=False, answer_as_new=True, parent=owner
    )
    await render_home(again, services)
    assert WORDS in owner.sent[-1].text
    assert model.calls == 1 and len(services.spawned) == 1

    # ↩️ Menu on a screen redraws that screen as Home in place.
    screen = QueueTestMessage(message_id=1099, is_bot=True, parent=owner)
    await navigation(_Press("home", screen), services)
    assert owner.rendered[-1].startswith("<b>🏠 ") and _labels(owner.markups[-1]) == MENU
    assert len(owner.sent) == 2
    assert (await _kinds(e2e_harness))[1099] == MessageKind.DASHBOARD.value


async def test_starts_in_a_row_make_one_request_and_only_the_last_home_gets_the_words(
    e2e_harness,
) -> None:
    """HM-START-011 — tests/brd/home.feature"""
    await _a_chat(e2e_harness)
    model = Model(hold=True)
    services = _services(e2e_harness, model)
    first = QueueTestMessage(message_id=170, text="/start", is_bot=False, answer_as_new=True)
    await render_home(first, services)
    await asyncio.wait_for(model.reached.wait(), timeout=5)

    # What the middleware does when the next /start arrives.
    services.owner_acted_at = utcnow()
    second = QueueTestMessage(
        message_id=180, text="/start", is_bot=False, answer_as_new=True, parent=first
    )
    await render_home(second, services)
    model.go.set()
    await _settled(services)

    assert model.calls == 1
    _, last = first.sent
    assert [message_id for message_id, _ in first.bot.edited] == [last.message_id]
    assert WORDS in first.bot.edits[-1]


async def test_clear_clears_the_chat_at_once_and_puts_the_words_in_after(e2e_harness) -> None:
    """HM-CLEAR-012 — tests/brd/home.feature"""
    await _a_chat(e2e_harness)
    model = Model(hold=True)
    services = _services(e2e_harness, model)
    # The /clear itself: no quiet time has passed.
    services.owner_acted_at = utcnow()
    owner = QueueTestMessage(message_id=150, text="/clear", is_bot=False, answer_as_new=True)

    await command_clear(owner, services)
    await asyncio.wait_for(model.reached.wait(), timeout=5)

    [dashboard] = owner.sent
    assert owner.bot.silent == [dashboard.text]
    assert WORDS not in dashboard.text and _labels(owner.markups[-1]) == ["☰ Menu"]
    assert owner.bot.deleted == list(range(1099, dashboard.message_id))
    assert await _kinds(e2e_harness) == {
        1000: MessageKind.DIALOGUE_USER.value,
        1100: MessageKind.DIALOGUE_USER.value,
        1101: MessageKind.DIALOGUE_ASSISTANT.value,
        dashboard.message_id: MessageKind.HOME.value,
    }

    model.go.set()
    await _settled(services)
    [(edited, markup)] = owner.bot.edited
    assert edited == dashboard.message_id and _labels(markup) == ["☰ Menu"]
    assert WORDS in owner.bot.edits[-1]
    assert (await _kinds(e2e_harness))[dashboard.message_id] == MessageKind.HOME.value


async def test_words_that_come_after_the_owner_acted_wait_for_the_next_home(
    e2e_harness,
) -> None:
    """HM-START-011 — tests/brd/home.feature"""
    await _a_chat(e2e_harness)
    model = Model(hold=True)
    services = _services(e2e_harness, model)
    owner = QueueTestMessage(message_id=150, text="/clear", is_bot=False, answer_as_new=True)
    await command_clear(owner, services)
    await asyncio.wait_for(model.reached.wait(), timeout=5)

    # The owner presses ☰ Menu before the words come.
    services.owner_acted_at = utcnow()
    model.go.set()
    await _settled(services)
    assert owner.bot.edited == []

    start = QueueTestMessage(
        message_id=190, text="/start", is_bot=False, answer_as_new=True, parent=owner
    )
    await render_home(start, services)
    assert WORDS in owner.sent[-1].text and model.calls == 1


async def test_words_leave_alone_a_message_that_no_longer_shows_that_home(e2e_harness) -> None:
    """HM-START-011 — tests/brd/home.feature"""
    await _a_chat(e2e_harness)
    model = Model(hold=True)
    services = _services(e2e_harness, model)
    owner = QueueTestMessage(message_id=170, text="/start", is_bot=False, answer_as_new=True)
    await render_home(owner, services)
    await asyncio.wait_for(model.reached.wait(), timeout=5)

    # The bot took the message out without the owner doing anything.
    [home] = owner.sent
    await TelegramNotes(e2e_harness.sessions).forget(CHAT_ID, home.message_id)
    model.go.set()
    await _settled(services)

    assert owner.bot.edited == [] and len(owner.sent) == 1


async def test_the_owner_acting_stops_a_clear_they_asked_for(e2e_harness, monkeypatch) -> None:
    """HM-CLEAR-012 — tests/brd/home.feature"""
    await _a_chat(e2e_harness)
    model = Model()
    services = _services(e2e_harness, model)
    owner = QueueTestMessage(message_id=150, text="/clear", is_bot=False, answer_as_new=True)
    reached, go = asyncio.Event(), asyncio.Event()

    async def drawing(session, words, **kwargs):
        reached.set()
        await go.wait()
        return await dashboard_text(session, words, **kwargs)

    monkeypatch.setattr(home_telegram, "dashboard_text", drawing)
    clearing = asyncio.create_task(command_clear(owner, services))
    await asyncio.wait_for(reached.wait(), timeout=5)
    # What the middleware does when the owner's message or press arrives.
    services.owner_acted_at = utcnow()
    services.turn.cancel()
    go.set()
    await asyncio.wait_for(clearing, timeout=5)

    assert owner.sent == [] and owner.bot.deleted == []
    assert services.spawned == [] and model.calls == 0
    assert services.turn.active is False and services.turn.background is False
