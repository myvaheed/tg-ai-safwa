"""A chat the owner left quiet, cleared down to the Home dashboard.

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
from safwa.features.home.hooks import HOME_HOOK, HOME_LOOK_EVERY
from safwa.features.home.motivation import Motivator
from safwa.features.home.telegram import render_home
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
from tg_agent_shell.telegram import dismiss_prior_ui
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
        call = ToolCall("c1", "motivate", json.dumps({"text": WORDS}))
        return CompletionTurn(content=None, tool_calls=(call,))


def _services(harness, model: Model, *, busy: bool = False) -> SimpleNamespace:
    reviews = SimpleNamespace(busy=True) if busy else ProposalStore()
    return SimpleNamespace(
        sessions=harness.sessions,
        owner_id=42,
        turn=TurnManager(),
        chat=ChatHost(TelegramNotes(harness.sessions), spawn=spawn_timer),
        root=SimpleNamespace(reviews=reviews),
        screens=SCREENS,
        commands=FEATURE_COMMANDS,
        bot_username="safwa_ai_bot",
        features=SimpleNamespace(motivator=Motivator(model)),
        owner_acted_at=utcnow() - timedelta(minutes=HOME_AFTER_MINUTES_DEFAULT + 1),
    )


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


async def _kinds(harness) -> dict[int, str]:
    notes = TelegramNotes(harness.sessions)
    return {
        message_id: note.kind
        for message_id in range(1, 2_000)
        if (note := await notes.note(CHAT_ID, message_id)) is not None
    }


async def _a_chat(harness) -> None:
    """What a morning leaves: the owner's words, an answer, and a screen still open."""
    async with harness.sessions() as session:
        await create_value(session, "Health", active=True)
        await session.commit()
    # Three days old: past what Telegram lets a bot delete.
    await _keep(
        harness, 1000, MessageKind.DIALOGUE_USER, "Hello", at=utcnow() - timedelta(days=3)
    )
    await _keep(harness, 1100, MessageKind.DIALOGUE_USER, "What is next?")
    await _keep(harness, 1101, MessageKind.DIALOGUE_ASSISTANT, "Pay the rent.")
    await _keep(harness, 1102, MessageKind.DASHBOARD, "<b>Today</b>")


async def test_a_quiet_chat_is_cleared_down_to_the_dashboard(e2e_harness) -> None:
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
    assert anchor.bot.deleted == list(range(1100, 1150))
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

    # A Reminder said while the owner was away goes at the next look, with the rest.
    await _keep(e2e_harness, 1151, MessageKind.CUE, "Time to stretch.")
    await looks.next()
    assert len(anchor.sent) == 2
    assert anchor.bot.deleted[50:] == list(range(1150, anchor.sent[-1].message_id))


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


async def test_the_dashboard_stays_until_the_next_clear(e2e_harness) -> None:
    """HM-STAYS-005 — tests/brd/home.feature"""
    await _a_chat(e2e_harness)
    services = _services(e2e_harness, Model())
    anchor = QueueTestMessage(message_id=150, is_bot=False, answer_as_new=True)
    await Looks(services, anchor).next()
    assert anchor.markups == [None]
    deleted = list(anchor.bot.deleted)

    # The owner writes, and then opens the menu with /start.
    owner = QueueTestMessage(message_id=1160, is_bot=False, answer_as_new=True, parent=anchor)
    await dismiss_prior_ui(owner, services)
    await render_home(owner, services)

    assert anchor.bot.deleted == deleted
    assert (await _kinds(e2e_harness))[1150] == MessageKind.HOME.value
    menu = anchor.sent[-1]
    assert "Your personal agile advisor" in menu.text
    assert anchor.markups[-1] is not None
