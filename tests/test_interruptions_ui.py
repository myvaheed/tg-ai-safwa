"""A review is never something the owner comes back to: what arrives below one ends it."""

from __future__ import annotations

from types import SimpleNamespace

from aiogram.exceptions import TelegramAPIError
from sqlalchemy import select
from ui_harness import (
    FakeBot,
    FakeMessage,
    StubAdvisor,
    services_for,
    spawn_timer,
    voice_message_for,
)

from safwa.adapters.telegram_history import MARKS, TelegramMessage, TelegramNotes
from safwa.ai.outcome import AIOutcome, AIOutcomeKind
from safwa.bootstrap.modules import (
    FEATURE_TEXT_INPUTS,
    PROPOSALS,
)
from safwa.enums import MessageKind
from safwa.features.proposals.model import ChangeAction, ProposalChange
from safwa.features.proposals.store import ProposalStore
from safwa.foundation.models import Workspace
from safwa.shell import (
    OwnerAndWritingMiddleware,
    dismiss_prior_ui,
)
from safwa.shell.model import CallbackToken
from safwa.turn import TurnManager
from safwa.turn.dialogue import ordinary_text
from telegram_llm import (
    ChatHost,
    DialogueMessage,
)


async def test_ag_turn_010_nothing_that_arrives_during_an_answer_joins_it(
    sessions, monkeypatch
) -> None:
    """AG-TURN-010 — tests/brd/agents.feature"""
    import safwa.shell.services as core_module

    monkeypatch.setattr(core_module, "Message", FakeMessage)
    middleware = OwnerAndWritingMiddleware()
    services = services_for(sessions)
    assert services.turn.try_begin(1)
    handled: list[str] = []

    async def handler(event, _data):
        handled.append(event.text)

    written = FakeMessage(2, text="Second request", bot_message=False, answer_as_new=True)
    recording = voice_message_for(3)
    await middleware(handler, written, {"services": services})
    await middleware(handler, recording, {"services": services})

    assert written.was_deleted and recording.was_deleted
    assert handled == []
    # Nothing is said about either of them, and the recording is never downloaded.
    assert written.answers == [] and recording.answers == []
    assert recording.bot.downloads == []


async def test_ag_turn_023_words_telegram_refused_to_delete_are_answered_now(
    sessions, monkeypatch
) -> None:
    """AG-TURN-023 — tests/brd/agents.feature"""
    import safwa.shell.services as core_module

    monkeypatch.setattr(core_module, "Message", FakeMessage)
    middleware = OwnerAndWritingMiddleware()
    services = services_for(sessions)
    assert services.turn.try_begin(1)
    handled: list[str] = []

    async def handler(event, _data):
        handled.append(event.text)

    message = FakeMessage(2, text="Actually, do this instead", bot_message=False)

    async def refuse() -> None:
        raise TelegramAPIError(method=SimpleNamespace(), message="message can't be deleted")

    message.delete = refuse

    await middleware(handler, message, {"services": services})

    assert handled == ["Actually, do this instead"]
    assert services.turn.active is False


async def test_proposal_ui_gives_up_the_turn_before_continuity_work(sessions) -> None:
    store = ProposalStore()
    async with sessions() as session:
        workspace = await session.get(Workspace, 1)
        proposal = store.open_proposal(
            message="Create VrWalk",
            workspace_revision=workspace.revision,
            changes=[
                ProposalChange(
                    entity="tag",
                    action=ChangeAction.CREATE,
                    values={"name": "VrWalk"},
                )
            ],
        )
        await session.commit()
        proposal_id = proposal.id

    class Advisor:
        reviews = store
        proposals = PROPOSALS

        async def handle(self, *_args, **_kwargs):
            return AIOutcome(
                AIOutcomeKind.PROPOSAL,
                "I prepared the proposed changes for your approval.",
                proposal_id=proposal_id,
            )

    class History:
        async def dialogue(self, *_args, **_kwargs):
            return [DialogueMessage(role="user", content="[Initial request]: Create a Tag")]

    turn = TurnManager()

    class Continuity:
        called = False

        async def maybe_summarize(self, *_args, **_kwargs):
            self.called = True
            assert turn.background is True

    continuity = Continuity()
    services = SimpleNamespace(
        sessions=sessions,
        owner_id=42,
        turn=turn,
        chat=ChatHost(TelegramNotes(sessions), MARKS, spawn=spawn_timer),
        text_inputs=FEATURE_TEXT_INPUTS,
        advisor=Advisor(),
        history=History(),
        continuity=continuity,
    )
    message = FakeMessage(20, text="Create a Tag VrWalk", bot_message=False)

    await ordinary_text(message, services)

    assert continuity.called is True
    assert turn.active is False
    async with sessions() as session:
        tokens = list(
            await session.scalars(
                select(CallbackToken).where(
                    CallbackToken.action.in_(["proposal_approve", "proposal_reject"])
                )
            )
        )
        assert len(tokens) == 2


async def test_new_dialogue_discards_and_freezes_pending_proposal(sessions) -> None:
    """PR-INTERRUPT-017 — tests/brd/proposals.feature"""
    store = ProposalStore()
    async with sessions() as session:
        workspace = await session.get(Workspace, 1)
        proposal = store.open_proposal(
            message="Rename the Tag",
            workspace_revision=workspace.revision,
            changes=[
                ProposalChange(
                    entity="tag",
                    action=ChangeAction.UPDATE,
                    entity_id=9,
                    expected_version=1,
                    values={"name": "Family"},
                )
            ],
        )
        session.add_all(
            [
                TelegramMessage(
                    chat_id=700,
                    message_id=10,
                    direction="out",
                    kind=MessageKind.APPROVAL.value,
                    related_id=proposal.id,
                ),
                TelegramMessage(
                    chat_id=700,
                    message_id=9,
                    direction="out",
                    kind=MessageKind.DASHBOARD.value,
                ),
            ]
        )
        await session.commit()
        proposal_id = proposal.id

    bot = FakeBot()
    incoming = FakeMessage(11, text="Another question", bot_message=False, bot=bot)
    await dismiss_prior_ui(incoming, services_for(sessions, reviews=store, advisor=StubAdvisor(store)))

    assert bot.deleted == [9]
    assert bot.edits[0][0] == 10
    assert "🗑 Discarded" in bot.edits[0][1]
    assert "You continued the conversation without saving it." in bot.edits[0][1]
    assert "Rename Tag “Family”" in bot.edits[0][1]
    assert "• Name: Home → Family" in bot.edits[0][1]
    async with sessions() as session:
        proposal = store.proposal(proposal_id)
        assert proposal is None
        frozen = await session.scalar(
            select(TelegramMessage).where(TelegramMessage.message_id == 10)
        )
        assert frozen.kind == MessageKind.DIALOGUE_ASSISTANT.value
        assert (
            await session.scalar(select(TelegramMessage).where(TelegramMessage.message_id == 9))
            is None
        )


async def test_a_command_dismisses_every_other_screen(sessions) -> None:
    """SC-LIVE-001 — tests/brd/screens.feature

    A command is the owner walking away, so the middleware answers the open screens.
    """
    from safwa.shell import dismiss_screens_before_a_command

    async with sessions() as session:
        session.add_all(
            [
                TelegramMessage(
                    chat_id=700,
                    message_id=20,
                    direction="out",
                    kind=MessageKind.DASHBOARD.value,
                ),
                TelegramMessage(
                    chat_id=700,
                    message_id=40,
                    direction="out",
                    kind=MessageKind.EDITOR.value,
                ),
            ]
        )
        await session.commit()

    bot = FakeBot()
    store = ProposalStore()
    services = services_for(sessions, reviews=store, advisor=StubAdvisor(store))
    handled: list[str] = []

    async def handler(event, _data):
        handled.append(event.text)

    # Between the two screens, so "older than this message" would have spared the newer one.
    command = FakeMessage(30, text="/today", bot_message=False, bot=bot)
    await dismiss_screens_before_a_command(handler, command, {"services": services})

    assert handled == ["/today"]
    assert sorted(bot.deleted) == [20, 40]

    text = FakeMessage(31, text="Not a command", bot_message=False, bot=bot)
    await dismiss_screens_before_a_command(handler, text, {"services": services})

    # Ordinary text dismisses from `ordinary_text`, after its live-editor branches.
    assert handled == ["/today", "Not a command"]
    assert sorted(bot.deleted) == [20, 40]


async def test_the_screen_the_owner_walked_into_is_left_alone(sessions) -> None:
    """SC-LIVE-001 — tests/brd/screens.feature

    The selector is every *other* screen, so the one the event belongs to is redrawn in
    place rather than taken away underneath the owner.
    """
    store = ProposalStore()
    async with sessions() as session:
        workspace = await session.get(Workspace, 1)
        proposal = store.open_proposal(
            message="Rename the Tag",
            workspace_revision=workspace.revision,
            changes=[
                ProposalChange(
                    entity="tag",
                    action=ChangeAction.UPDATE,
                    entity_id=9,
                    expected_version=1,
                    values={"name": "Family"},
                )
            ],
        )
        session.add_all(
            [
                TelegramMessage(
                    chat_id=700,
                    message_id=10,
                    direction="out",
                    kind=MessageKind.APPROVAL.value,
                    related_id=proposal.id,
                ),
                TelegramMessage(
                    chat_id=700,
                    message_id=9,
                    direction="out",
                    kind=MessageKind.DASHBOARD.value,
                ),
            ]
        )
        await session.commit()

    bot = FakeBot()
    dashboard = FakeMessage(9, bot_message=True, bot=bot)
    await dismiss_prior_ui(dashboard, services_for(sessions, reviews=store, advisor=StubAdvisor(store)))

    assert bot.deleted == []
    assert bot.edits[0][0] == 10
    assert "🗑 Discarded" in bot.edits[0][1]


async def test_typed_words_end_the_review_and_are_then_answered(sessions) -> None:
    """PR-INTERRUPT-017 — tests/brd/proposals.feature

    Ending the review is half of it. The words that ended it are the next request.
    """
    store = ProposalStore()
    async with sessions() as session:
        workspace = await session.get(Workspace, 1)
        proposal = store.open_proposal(
            message="Rename the Tag",
            workspace_revision=workspace.revision,
            changes=[
                ProposalChange(
                    entity="tag",
                    action=ChangeAction.UPDATE,
                    entity_id=9,
                    expected_version=1,
                    values={"name": "Family"},
                )
            ],
        )
        session.add(
            TelegramMessage(
                chat_id=700,
                message_id=10,
                direction="out",
                kind=MessageKind.APPROVAL.value,
                related_id=proposal.id,
            )
        )
        await session.commit()
        proposal_id = proposal.id

    asked: list[str] = []

    class Advisor(StubAdvisor):
        async def handle(self, text, *_args, **_kwargs):
            asked.append(text)
            return AIOutcome(AIOutcomeKind.ANSWER, "Called it Home instead.")

    class History:
        async def dialogue(self, *_args, **_kwargs):
            return [DialogueMessage(role="user", content="[Initial request]: Rename the Tag")]

    class Continuity:
        async def maybe_summarize(self, *_args, **_kwargs):
            return None

    bot = FakeBot()
    services = SimpleNamespace(
        sessions=sessions,
        owner_id=42,
        turn=TurnManager(),
        chat=ChatHost(TelegramNotes(sessions), MARKS, spawn=spawn_timer),
        text_inputs=FEATURE_TEXT_INPUTS,
        advisor=Advisor(store),
        history=History(),
        continuity=Continuity(),
    )
    message = FakeMessage(11, text="No, call it Home", bot_message=False, bot=bot)

    await ordinary_text(message, services)

    assert asked == ["No, call it Home"]
    assert bot.edits[0][0] == 10
    assert "🗑 Discarded" in bot.edits[0][1]
    async with sessions() as session:
        assert store.proposal(proposal_id) is None
