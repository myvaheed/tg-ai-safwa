"""A quiet chat is empty until /start explicitly opens Home.

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
from ui_harness import history_source

from llm_gateway import CompletionRequest, CompletionTurn, ToolCall
from safwa.bootstrap.modules import FEATURE_CALLBACK_ACTIONS, FEATURE_COMMANDS, SCREENS
from safwa.features.home.api import menu_markup
from safwa.features.home.hooks import HOME_HOOK, HOME_LOOK_EVERY
from safwa.features.home.motivation import Motivator
from safwa.features.home.telegram import command_clear, render_home
from safwa.features.profile.api import secret_word_verifier
from safwa.features.profile.model import HOME_AFTER_MINUTES_DEFAULT
from safwa.features.values.use_cases import create_value
from telegram_llm import ChatHost, Note
from tg_agent_shell.access.manager import AccessManager
from tg_agent_shell.cues.initiatives import TickPoll
from tg_agent_shell.cues.runtime import tick_chat
from tg_agent_shell.foundation.clock import utcnow
from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.history import TelegramNotes
from tg_agent_shell.hooks.registry import HookRegistry
from tg_agent_shell.proposals.store import ProposalStore
from tg_agent_shell.telegram import commands as commands_module
from tg_agent_shell.telegram import dismiss_prior_ui
from tg_agent_shell.telegram.chat import SCREEN_KINDS, mint_token
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

    services = SimpleNamespace(
        sessions=harness.sessions,
        owner_id=42,
        turn=TurnManager(),
        chat=ChatHost(TelegramNotes(harness.sessions), spawn=spawn),
        spawned=spawned,
        root=SimpleNamespace(reviews=reviews),
        screens=SCREENS,
        commands=FEATURE_COMMANDS,
        callback_actions=FEATURE_CALLBACK_ACTIONS,
        start_links=(),
        bot_username="safwa_ai_bot",
        features=SimpleNamespace(motivator=Motivator(model, spawn=spawn_timer)),
        owner_acted_at=utcnow() - timedelta(minutes=HOME_AFTER_MINUTES_DEFAULT + 1),
    )
    services.history = history_source(harness.sessions)
    services.history.access_boundaries = True
    services.access = AccessManager(services, secret_word_verifier)
    return services


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


async def test_a_quiet_chat_is_empty_and_unanswered_hooks_wait_for_start(
    e2e_harness,
) -> None:
    """HM-QUIET-003 — tests/brd/home.feature"""
    await _a_chat(e2e_harness)
    services = _services(e2e_harness, Model())
    anchor = QueueTestMessage(message_id=150, is_bot=False, answer_as_new=True)
    looks = Looks(services, anchor)

    await looks.next()

    assert anchor.sent == [] and services.features.motivator.fresh() is None
    # The physical chat is cleared while its conversation stays available to the Diary.
    assert anchor.bot.deleted == list(range(1099, 1102))
    # What was said is still kept; the screen is gone with its message.
    assert await _kinds(e2e_harness) == {
        1000: MessageKind.DIALOGUE_USER.value,
        1100: MessageKind.DIALOGUE_USER.value,
        1101: MessageKind.DIALOGUE_ASSISTANT.value,
    }

    # Nothing new has come into the empty chat.
    deleted = list(anchor.bot.deleted)
    await looks.next()
    assert anchor.sent == [] and anchor.bot.deleted == deleted

    # An unanswered hook is hidden by the next clear and restored before /start's Home.
    await _keep(e2e_harness, 1151, MessageKind.CUE, "Time to stretch.")
    anchor.message_id = 160
    deleted = len(anchor.bot.deleted)
    await looks.next()
    assert anchor.sent == [] and 1151 in anchor.bot.deleted[deleted:]
    assert (await _kinds(e2e_harness))[1151] == MessageKind.CUE.value
    await looks.next()
    assert anchor.sent == []
    start = QueueTestMessage(message_id=2000, text="/start", is_bot=False, answer_as_new=True, parent=anchor)
    await render_home(start, services)
    await _settled(services)
    assert anchor.rendered[0] == "Time to stretch." and "🏠" in anchor.rendered[-1]


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


async def test_the_owner_acting_stops_the_clearing(e2e_harness, monkeypatch) -> None:
    """HM-QUIET-003 — tests/brd/home.feature"""
    await _a_chat(e2e_harness)
    model = Model()
    services = _services(e2e_harness, model)
    anchor = QueueTestMessage(message_id=150, is_bot=False, answer_as_new=True)

    reached, go = asyncio.Event(), asyncio.Event()
    capture = services.access._capture_unanswered

    async def paused_capture(chat_id):
        reached.set()
        await go.wait()
        await capture(chat_id)

    monkeypatch.setattr(services.access, "_capture_unanswered", paused_capture)
    clearing = asyncio.create_task(Looks(services, anchor).next())
    await asyncio.wait_for(reached.wait(), timeout=5)
    # What the middleware does when the owner's message or press arrives.
    services.owner_acted_at = utcnow()
    services.turn.cancel()
    go.set()
    await asyncio.wait_for(clearing, timeout=5)

    assert anchor.sent == [] and anchor.bot.deleted == []
    assert services.turn.active is False


async def test_a_dashboard_from_an_earlier_day_is_removed_without_drawing_another(e2e_harness) -> None:
    """HM-QUIET-004 — tests/brd/home.feature"""
    yesterday = datetime.now(UTC) - timedelta(days=1, hours=1)
    await _keep(e2e_harness, 1140, MessageKind.HOME, "<b>🏠 Yesterday</b>", at=yesterday)
    services = _services(e2e_harness, Model())
    services.owner_acted_at = yesterday - timedelta(hours=1)
    anchor = QueueTestMessage(message_id=150, is_bot=False, answer_as_new=True)

    await Looks(services, anchor).next()

    assert anchor.sent == []
    assert 1140 in anchor.bot.deleted
    assert await _kinds(e2e_harness) == {}


@pytest.mark.parametrize("opened", ("/start", "/tags", "/start value-1", "place", "home", "tags"))
async def test_the_dashboard_is_the_only_ui_until_the_owner_opens_another(
    e2e_harness, monkeypatch, opened,
) -> None:
    """HM-STAYS-005 — tests/brd/home.feature"""
    await _a_chat(e2e_harness)
    services = _services(e2e_harness, Model())
    anchor = QueueTestMessage(message_id=150, is_bot=False, answer_as_new=True)
    await Looks(services, anchor).next()
    await render_home(QueueTestMessage(message_id=170, text="/start", is_bot=False, answer_as_new=True, parent=anchor), services)
    await _settled(services)
    [dashboard] = anchor.sent
    assert _labels(anchor.markups[-1]) == ["☰ Menu"]
    if opened == "place":
        async with e2e_harness.sessions() as session:
            token = await mint_token(session, services.owner_id, "value_view", {"id": 1})
            await session.commit()
        opened = f"/start go-{token}"
    below = QueueTestMessage(
        message_id=180, text=opened, is_bot=False, answer_as_new=True, parent=anchor
    )
    monkeypatch.setattr(commands_module, "owner_anchor", lambda _bot, _owner_id: below)
    if opened.startswith("/"):
        handler = next(
            screen.handler for screen in FEATURE_COMMANDS
            if screen.command == opened.split()[0][1:]
        )
        await commands_module.dismiss_screens_before_a_command(
            lambda message, data: handler(message, data["services"]), below, {"services": services}
        )
    else:
        await navigation(_Press(opened, dashboard), services)

    replaced_in_place = opened in ("home", "tags") or opened.startswith("/start go-")
    assert (dashboard.message_id in anchor.bot.deleted) is not replaced_in_place
    live = await services.chat.notes.outgoing(CHAT_ID, kinds=SCREEN_KINDS)
    assert [note.message_id for note in live] == [anchor.sent[-1].message_id]
    assert len(anchor.sent) == (1 if replaced_in_place else 2)
    if opened in ("/start", "home"):
        assert _labels(anchor.markups[-1]) == (["☰ Menu"] if opened == "/start" else MENU)
    # Removing a screen must not restore the conversation the clear ended.
    source = services.history
    assert await source.recent(CHAT_ID) == []
    day = await source.day_transcript(
        CHAT_ID, start=utcnow() - timedelta(hours=1), end=utcnow() + timedelta(hours=1),
        token_budget=10_000,
    )
    assert "What is next?" in day and "Pay the rent." in day


async def test_writing_to_the_advisor_removes_home_without_restoring_old_history(e2e_harness):
    """HM-STAYS-005 — tests/brd/home.feature"""
    await _a_chat(e2e_harness)
    services = _services(e2e_harness, Model())
    anchor = QueueTestMessage(message_id=150, is_bot=False, answer_as_new=True)
    await Looks(services, anchor).next()
    await render_home(QueueTestMessage(message_id=170, text="/start", is_bot=False, answer_as_new=True, parent=anchor), services)
    await _settled(services)
    [dashboard] = anchor.sent
    owner = QueueTestMessage(message_id=1160, text="Hello again", is_bot=False, parent=anchor)

    await dismiss_prior_ui(owner, services)
    await _keep(e2e_harness, owner.message_id, MessageKind.DIALOGUE_USER, owner.text)

    assert dashboard.message_id in anchor.bot.deleted
    assert [entry.text for entry in await services.history.recent(CHAT_ID)] == [
        "Hello again"
    ]
    deleted = list(anchor.bot.deleted)
    await dismiss_prior_ui(owner, services)
    assert anchor.bot.deleted == deleted


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
    assert _labels(owner.markups[-1]) == ["☰ Menu"]
    assert owner.bot.deleted == [] and owner.bot.silent == []
    kinds = await _kinds(e2e_harness)
    assert kinds[home.message_id] == MessageKind.DASHBOARD.value
    assert kinds[1100] == MessageKind.DIALOGUE_USER.value

    model.go.set()
    await _settled(services)
    [(edited, markup)] = owner.bot.edited
    assert edited == home.message_id and _labels(markup) == ["☰ Menu"]
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


async def test_clear_empties_the_chat_without_drawing_or_requesting_words(e2e_harness) -> None:
    """HM-CLEAR-012 — tests/brd/home.feature"""
    await _a_chat(e2e_harness)
    model = Model()
    services = _services(e2e_harness, model)
    # The /clear itself: no quiet time has passed.
    services.owner_acted_at = utcnow()
    owner = QueueTestMessage(message_id=150, text="/clear", is_bot=False, answer_as_new=True)

    await command_clear(owner, services)
    assert owner.sent == [] and model.calls == 0 and services.spawned == []
    assert owner.bot.deleted == list(range(1099, 1102))
    assert await _kinds(e2e_harness) == {
        1000: MessageKind.DIALOGUE_USER.value,
        1100: MessageKind.DIALOGUE_USER.value,
        1101: MessageKind.DIALOGUE_ASSISTANT.value,
    }

    assert await services.history.dialogue(CHAT_ID) == []


async def test_words_that_come_after_the_owner_acted_wait_for_the_next_home(
    e2e_harness,
) -> None:
    """HM-START-011 — tests/brd/home.feature"""
    await _a_chat(e2e_harness)
    model = Model(hold=True)
    services = _services(e2e_harness, model)
    owner = QueueTestMessage(message_id=150, text="/start", is_bot=False, answer_as_new=True)
    await render_home(owner, services)
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

    capture = services.access._capture_unanswered

    async def paused_capture(chat_id):
        reached.set()
        await go.wait()
        await capture(chat_id)

    monkeypatch.setattr(services.access, "_capture_unanswered", paused_capture)
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
