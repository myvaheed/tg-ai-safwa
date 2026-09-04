"""One turn at a time: the notice it stands under, the lease it holds, and what it leaves."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramAPIError
from marks import read_kind_mark
from sqlalchemy import select
from ui_harness import (
    FakeBot,
    FakeMessage,
    ScriptedTranscriber,
    StubAdvisor,
    capture_dialogue_turns,
    services_for,
    voice_message_for,
)

from safwa.bootstrap.modules import (
    PROPOSALS,
)
from safwa.features.planning.telegram import render_sprint
from safwa.features.planning.use_cases import set_sprint_success_criteria
from safwa.foundation.workspace import Workspace
from telegram_llm import (
    HistoryEntry,
)
from tg_agent_shell.adapters.kinds import MessageKind
from tg_agent_shell.adapters.telegram_history import TelegramMessage
from tg_agent_shell.ai.outcome import AIOutcome, AIOutcomeKind
from tg_agent_shell.proposals.store import ProposalStore
from tg_agent_shell.telegram import (
    dismiss_prior_ui,
)
from tg_agent_shell.telegram.chat import (
    TURN_NOTICE,
    edit_registered_message,
    remove_turn_notice,
    send_owner_turn,
)
from tg_agent_shell.turn.dialogue import run_dialogue_turn, voice_message


class TurnAdvisor:
    """One answer, produced by a turn that saved a change the way autoapproval does."""

    def __init__(self, sessions) -> None:
        self.sessions = sessions

    async def handle(self, request, *, source_message_id=None, dialogue=None):
        del request, source_message_id, dialogue
        async with self.sessions() as session:
            await set_sprint_success_criteria(session, "Ship the release")
            await session.commit()
        return AIOutcome(AIOutcomeKind.ANSWER, "⚡ Auto-saved the proposed change.")


def turn_services(sessions):
    services = services_for(sessions, root=None)
    services.root = TurnAdvisor(sessions)
    services.history = SimpleNamespace(dialogue=_empty_dialogue)
    services.continuity = SimpleNamespace(close_window=_no_summary)
    return services


async def _empty_dialogue(_chat_id, *, source_message=None):
    del source_message
    return []


async def _no_summary(_chat_id, _send, *, still_current=None):
    del still_current


async def test_an_autoapproved_change_still_reaches_the_chat(sessions) -> None:
    """The workspace revision moves inside the turn, so it cannot invalidate the answer."""
    services = turn_services(sessions)
    message = FakeMessage(960, text="Save it", bot_message=False, answer_as_new=True)
    source = HistoryEntry(
        message_id=960,
        sender_id=42,
        role="user",
        text="Save it",
        created_at=datetime.now(UTC),
        kind=MessageKind.DIALOGUE_USER.value,
    )

    await run_dialogue_turn(message, services, "Save it", source)

    assert any("Auto-saved" in item.text for item in message.sent_messages)
    async with sessions() as session:
        assert (await session.get(Workspace, 1)).revision > 0


async def test_ag_turn_022_the_notice_stands_while_the_answer_is_written(sessions) -> None:
    """AG-TURN-022 — tests/brd/agents.feature"""
    services = turn_services(sessions)
    message = FakeMessage(966, text="Save it", bot_message=False, answer_as_new=True)
    source = HistoryEntry(
        message_id=966,
        sender_id=42,
        role="user",
        text="Save it",
        created_at=datetime.now(UTC),
        kind=MessageKind.DIALOGUE_USER.value,
    )

    await run_dialogue_turn(message, services, "Save it", source)

    notice = message.sent_messages[0]
    assert read_kind_mark(notice.text) == (MessageKind.UI_INPUT.value, TURN_NOTICE)
    assert notice.message_id in message.bot.deleted
    assert any("Auto-saved" in item.text for item in message.sent_messages)


async def test_ag_turn_022_a_cancelled_turn_leaves_no_notice_and_no_answer(sessions) -> None:
    """AG-TURN-022 — tests/brd/agents.feature"""
    services = turn_services(sessions)
    original_handle = services.root.handle

    async def cancel_then_answer(request, *, source_message_id=None, dialogue=None):
        await remove_turn_notice(message, services, services.turn.cancel())
        return await original_handle(
            request, source_message_id=source_message_id, dialogue=dialogue
        )

    services.root.handle = cancel_then_answer
    message = FakeMessage(961, text="Save it", bot_message=False, answer_as_new=True)
    source = HistoryEntry(
        message_id=961,
        sender_id=42,
        role="user",
        text="Save it",
        created_at=datetime.now(UTC),
        kind=MessageKind.DIALOGUE_USER.value,
    )

    await run_dialogue_turn(message, services, "Save it", source)

    notice = message.sent_messages[0]
    assert read_kind_mark(notice.text)[1] == TURN_NOTICE
    assert notice.message_id in message.bot.deleted
    assert [item for item in message.sent_messages if "Auto-saved" in item.text] == []


async def test_a_review_that_could_not_be_drawn_ends_and_the_owner_is_told(sessions) -> None:
    """SC-FAIL-005 — tests/brd/screens.feature"""
    cancelled: list[int] = []

    class ProposalAdvisor:
        # An empty store, so drawing proposal #77 fails the way a refused send does.
        reviews = ProposalStore()
        proposals = PROPOSALS

        async def handle(self, request, *, source_message_id=None, dialogue=None):
            del request, source_message_id, dialogue
            return AIOutcome(AIOutcomeKind.PROPOSAL, "Review this", proposal_id=77)

        async def cancel_approval_for_proposal(self, proposal_id: int) -> None:
            cancelled.append(proposal_id)

    services = turn_services(sessions)
    services.root = ProposalAdvisor()
    message = FakeMessage(965, text="Save it", bot_message=False, answer_as_new=True)
    source = HistoryEntry(
        message_id=965,
        sender_id=42,
        role="user",
        text="Save it",
        created_at=datetime.now(UTC),
        kind=MessageKind.DIALOGUE_USER.value,
    )

    await run_dialogue_turn(message, services, "Save it", source)

    assert cancelled == [77]
    assert any(
        "Your planning data was not changed" in item.text for item in message.sent_messages
    )


async def test_the_owner_turn_is_headed_by_the_telegram_name(sessions, monkeypatch) -> None:
    capture_dialogue_turns(monkeypatch)
    services = services_for(sessions, transcriber=ScriptedTranscriber("Plan my week."))
    message = voice_message_for(962)

    await voice_message(message, services)

    assert "<b>User Name Surname:</b>" in message.sent_messages[0].text


async def test_a_turn_with_no_owner_message_is_headed_by_the_bare_role(sessions) -> None:
    """A Reminder anchor carries no real `from_user`, so only the role is left."""
    services = services_for(sessions)
    anchor = FakeMessage(964, bot_message=True, answer_as_new=True)
    anchor.from_user = SimpleNamespace(id=1, is_bot=True, full_name="Safwa")

    await send_owner_turn(anchor, services, "Later, then.")

    assert read_kind_mark(anchor.sent_messages[-1].text)[1].startswith("<b>User:</b>")


async def test_a_screen_deleted_outside_the_bot_is_redrawn_instead_of_failing(sessions) -> None:
    """Clearing the chat leaves the registration behind, and every later render aims at it."""
    async with sessions() as session:
        session.add(
            TelegramMessage(
                chat_id=700,
                message_id=500,
                direction="out",
                kind=MessageKind.DASHBOARD.value,
            )
        )
        await session.commit()

    bot = FakeBot()

    async def gone(*_args, **_kwargs):
        raise TelegramAPIError(method=SimpleNamespace(), message="message to edit not found")

    bot.edit_message_text = gone
    message = FakeMessage(1, text="/start card-1", bot_message=False, bot=bot, answer_as_new=True)
    services = services_for(sessions)

    await edit_registered_message(
        message, services, 500, "Sprint plan", kind=MessageKind.DASHBOARD
    )

    assert message.answers, "the screen was not drawn again"
    async with sessions() as session:
        registered = list(
            await session.scalars(
                select(TelegramMessage.message_id).where(TelegramMessage.chat_id == 700)
            )
        )
    assert 500 not in registered, "the dead registration outlived the message"
    assert registered == [message.sent_messages[0].message_id]


async def test_a_cancelled_generation_still_gives_up_its_lease(sessions, monkeypatch) -> None:
    """Giving the turn back awaits, and a cancelled await must not carry it away.

    A turn left behind is invisible: the middleware silently deletes every command after
    it, so the bot looks alive while `/start` and every deep link do nothing.
    """
    import tg_agent_shell.turn.dialogue as dialogue_module

    async def cancelled(*_args, **_kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(dialogue_module, "render_ai_outcome", cancelled)
    services = turn_services(sessions)
    message = FakeMessage(1, text="Plan my week", bot_message=False, answer_as_new=True)
    source = HistoryEntry(
        message_id=message.message_id,
        sender_id=42,
        role="user",
        text="Plan my week",
        created_at=message.date,
        kind=MessageKind.DIALOGUE_USER.value,
    )

    with pytest.raises(asyncio.CancelledError):
        await dialogue_module.run_dialogue_turn(message, services, "Plan my week", source)

    assert services.turn.active is False, "the lease outlived the generation that held it"


async def test_a_toast_leaves_the_screen_alone_and_takes_itself_back(sessions, monkeypatch):
    """SC-KEEP-002 — tests/brd/screens.feature"""
    import tg_agent_shell.telegram.chat as messaging

    monkeypatch.setattr(messaging, "TOAST_SECONDS", 0)
    services = services_for(sessions)
    screen = FakeMessage(80, bot_message=True, answer_as_new=True)
    await render_sprint(screen, services)
    drawn = len(screen.edits)

    await messaging.send_toast(screen, services, "Slow down.")
    first = screen.sent_messages[-1]
    assert "Slow down." in screen.answers[-1]
    assert len(screen.edits) == drawn
    async with sessions() as session:
        stored = await session.scalar(
            select(TelegramMessage).where(TelegramMessage.message_id == first.message_id)
        )
        assert stored.kind == MessageKind.STATUS.value

    # A second Toast replaces the first rather than stacking above the screen.
    await messaging.send_toast(screen, services, "Still too fast.")
    assert first.message_id in screen.bot.deleted

    message_id, expiry = services.chat.toasts[screen.chat.id]
    await expiry
    assert message_id in screen.bot.deleted
    async with sessions() as session:
        assert (
            await session.scalar(
                select(TelegramMessage).where(TelegramMessage.message_id == message_id)
            )
            is None
        )


async def test_what_was_said_is_never_taken_out_of_the_chat(sessions) -> None:
    """SC-KEEP-002 — tests/brd/screens.feature"""
    async with sessions() as session:
        session.add_all(
            [
                TelegramMessage(
                    chat_id=700,
                    message_id=50,
                    direction="out",
                    kind=MessageKind.DASHBOARD.value,
                ),
                TelegramMessage(
                    chat_id=700,
                    message_id=51,
                    direction="out",
                    kind=MessageKind.DIALOGUE_ASSISTANT.value,
                ),
                TelegramMessage(
                    chat_id=700,
                    message_id=52,
                    direction="out",
                    kind=MessageKind.SUMMARY.value,
                ),
            ]
        )
        await session.commit()

    bot = FakeBot()
    store = ProposalStore()
    services = services_for(sessions, reviews=store, root=StubAdvisor(store))
    message = FakeMessage(53, text="Carry on", bot_message=False, bot=bot)

    await dismiss_prior_ui(message, services)

    assert bot.deleted == [50]


async def test_a_screen_whose_freeze_fails_stops_being_walked(sessions) -> None:
    async with sessions() as session:
        session.add(
            TelegramMessage(
                chat_id=700,
                message_id=60,
                direction="out",
                kind=MessageKind.APPROVAL.value,
            )
        )
        await session.commit()

    bot = FakeBot()

    async def refused_edit(*_args, **_kwargs) -> None:
        raise TelegramAPIError(method=SimpleNamespace(), message="edit failed")

    async def freeze(_screen):
        return "Review discarded", MessageKind.DIALOGUE_ASSISTANT.value

    bot.edit_message_text = refused_edit
    services = services_for(sessions)
    message = FakeMessage(61, text="Carry on", bot_message=False, bot=bot)

    await services.chat.leave_one_screen(
        message,
        kinds={MessageKind.APPROVAL.value},
        freeze=freeze,
    )

    assert bot.cleared_markup == [60]
    async with sessions() as session:
        assert await session.scalar(
            select(TelegramMessage).where(TelegramMessage.message_id == 60)
        ) is None
