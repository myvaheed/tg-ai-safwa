from __future__ import annotations

import ast
import html
import importlib
import inspect
import pkgutil
from datetime import UTC, date, datetime, time
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

import safwa.telegram as telegram_source
from safwa.ai.context import DialogueMessage
from safwa.ai.service import AIOutcome, ProposalDescription, ProposalService
from safwa.ai.sql import create_ai_views
from safwa.asr import TranscriptionError, TranscriptionResult
from safwa.constants import ASR_MAX_DURATION_SECONDS, DIARY_TIME_DEFAULT, TELEGRAM_TEXT_LIMIT
from safwa.domain import (
    DIARY_REMINDER_INSTRUCTION,
    DomainError,
    archive_tag,
    create_card,
    create_check,
    create_diary_entry,
    create_reminder,
    create_saved_request,
    create_tag,
    create_value,
    finish_action,
    set_sprint_success_criteria,
    start_sprint,
    toggle_card_check,
    update_profile,
)
from safwa.enums import CardStage, MessageKind
from safwa.history import (
    CITATION_TYPES,
    HistoryEntry,
    parse_citation_payload,
    read_kind_mark,
)
from safwa.models import (
    CallbackToken,
    Card,
    CardCategory,
    CardEnergyType,
    CardTag,
    CardValue,
    ChangeProposal,
    ProposalChange,
    Reminder,
    Sprint,
    Tag,
    TelegramMessage,
    UiSession,
    UserProfile,
    Value,
    Workspace,
)
from safwa.reminders import resolve
from safwa.telegram import (
    CALLBACK_ACTIONS,
    GenerationGuard,
    OwnerAndWritingMiddleware,
    callback_token_handler,
    dismiss_prior_ui,
    handle_card_creation_chooser,
    open_item_screen,
    ordinary_text,
    render_ai_outcome,
    render_card,
    render_card_choices,
    render_card_creation,
    render_children,
    render_citations,
    render_dashboard,
    render_item_editor,
    render_item_text_prompt,
    render_proposal,
    render_sprint,
    render_today,
    voice_message,
)
from safwa.telegram._messaging import materialize_queued_dialogue
from safwa.telegram._presentation import start_payload
from safwa.telegram.commands import command_settings, command_start
from safwa.telegram.dialogue import run_dialogue_turn
from safwa.telegram.reminders import render_reminder, render_reminders
from safwa.telegram.screens import OPENABLE_MODELS
from safwa.telegram.sprint import render_sprint_confirm


def _telegram_module_trees() -> list[ast.Module]:
    """Every submodule of the ``safwa.telegram`` package as a parsed AST.

    The inline-button invariants were single-module when the UI lived in one file;
    after the package split they must hold across every submodule that contributes
    button actions or the ``CALLBACK_ACTIONS`` registry.
    """
    trees: list[ast.Module] = []
    for info in pkgutil.iter_modules(telegram_source.__path__):
        module = importlib.import_module(f"{telegram_source.__name__}.{info.name}")
        trees.append(ast.parse(inspect.getsource(module)))
    return trees


def _callback_actions_registry(trees: list[ast.Module]) -> ast.AST:
    for tree in trees:
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.target.id == "CALLBACK_ACTIONS"
            ):
                return node
    raise AssertionError("CALLBACK_ACTIONS registry not found in any telegram submodule")


def test_every_inline_button_action_has_a_registered_handler() -> None:
    """An inline button whose action is unregistered is a screen that does nothing."""
    emitted = {
        node.args[3].value
        for tree in _telegram_module_trees()
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", None) == "token_button"
        and len(node.args) > 3
        and isinstance(node.args[3], ast.Constant)
        and isinstance(node.args[3].value, str)
    }

    assert emitted, "no literal token_button actions were found to check"
    assert emitted <= set(CALLBACK_ACTIONS)


def test_no_individually_registered_handler_is_unreachable() -> None:
    """A handler no button can reach is dead code, like the removed value_toggle."""
    trees = _telegram_module_trees()
    registry = _callback_actions_registry(trees)
    registry_nodes = set(map(id, ast.walk(registry)))
    referenced = {
        node.value
        for tree in trees
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in registry_nodes
    }
    # Only keys spelled out in the registry are checked here; the generated selector
    # families build their names from the same constants the buttons use.
    spelled_out = {
        key.value
        for key in registry.value.keys
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }

    assert spelled_out, "no literal registry keys were found to check"
    assert spelled_out <= referenced


def test_every_card_relationship_is_wired_to_both_selector_surfaces() -> None:
    """Adding a relationship to the table must not leave half the screens unreachable."""
    for field, relation in telegram_source.RELATION_CHOICES.items():
        assert f"card_choose_{field}" in CALLBACK_ACTIONS
        assert f"card_create_choose_{field}" in CALLBACK_ACTIONS
        assert f"card_toggle_{relation.singular}" in CALLBACK_ACTIONS
        assert f"card_create_toggle_{relation.singular}" in CALLBACK_ACTIONS


class FakeBot:
    def __init__(self) -> None:
        self.id = 999
        self.downloads: list[str] = []
        self.audio_bytes = b"OggS-fake-audio"
        self.edits: list[tuple[int, str, object | None]] = []
        self.deleted: list[int] = []
        self.deleted_batches: list[list[int]] = []
        self.cleared_markup: list[int] = []
        self.published_commands: list[list[str]] = []

    async def edit_message_text(
        self,
        text: str,
        *,
        chat_id: int,
        message_id: int,
        reply_markup=None,
        parse_mode=None,
    ) -> None:
        del chat_id, parse_mode
        self.edits.append((message_id, text, reply_markup))

    async def delete_message(self, chat_id: int, message_id: int) -> None:
        del chat_id
        self.deleted.append(message_id)

    async def delete_messages(self, *, chat_id: int, message_ids: list[int]) -> None:
        del chat_id
        self.deleted_batches.append(message_ids)

    async def edit_message_reply_markup(
        self, *, chat_id: int, message_id: int, reply_markup=None
    ) -> None:
        del chat_id, reply_markup
        self.cleared_markup.append(message_id)

    async def send_chat_action(self, chat_id: int, action) -> None:
        del chat_id, action

    async def download(self, file_id: str, destination):
        self.downloads.append(file_id)
        destination.write(self.audio_bytes)
        return destination

    async def set_my_commands(self, commands) -> None:
        self.published_commands.append([command.command for command in commands])


class FakeMessage:
    def __init__(
        self,
        message_id: int,
        *,
        text: str = "",
        bot_message: bool,
        bot: FakeBot | None = None,
        chat_id: int = 700,
        answer_as_new: bool = False,
        voice: SimpleNamespace | None = None,
    ) -> None:
        self.message_id = message_id
        self.text = text
        self.voice = voice
        self.audio = None
        self.video_note = None
        self.bot = bot or FakeBot()
        self.chat = SimpleNamespace(id=chat_id, type="private")
        self.from_user = SimpleNamespace(id=42, is_bot=bot_message, full_name="Name Surname")
        self.date = datetime.now(UTC)
        self.edits: list[tuple[str, object | None]] = []
        self.answers: list[str] = []
        self.answer_markups: list[object | None] = []
        self.was_deleted = False
        self.answer_as_new = answer_as_new
        self.sent_messages: list[FakeMessage] = []

    async def edit_text(self, text: str, *, reply_markup=None, parse_mode=None):
        del parse_mode
        self.edits.append((text, reply_markup))
        return self

    async def answer(self, text: str, *, reply_markup=None, parse_mode=None):
        del parse_mode
        self.answers.append(text)
        self.answer_markups.append(reply_markup)
        if self.answer_as_new:
            # Telegram hands out a fresh id per message; a repeated one would collapse
            # several registrations into one row.
            sent = FakeMessage(
                self.message_id + 1_000 + len(self.sent_messages),
                text=text,
                bot_message=True,
                bot=self.bot,
                chat_id=self.chat.id,
            )
            self.sent_messages.append(sent)
            return sent
        return self

    async def delete(self) -> None:
        self.was_deleted = True


class FakeCallback:
    def __init__(self, token: str, message: FakeMessage) -> None:
        self.data = f"cb:{token}"
        self.message = message
        self.answers: list[tuple[str | None, bool]] = []

    async def answer(self, text: str | None = None, *, show_alert: bool = False) -> None:
        self.answers.append((text, show_alert))


class StubAdvisor:
    """Only the two hooks a dismissed proposal screen reaches for."""

    async def describe_proposal(self, _session, _proposal_id) -> ProposalDescription:
        return ProposalDescription(summary="Rename Tag “Family”", fields=["Name: Home → Family"])

    async def cancel_approval_for_target(self, _target_type, _target_id) -> str | None:
        return None


def services_for(sessions, *, advisor=None, transcriber=None):
    return SimpleNamespace(
        sessions=sessions,
        owner_id=42,
        guard=GenerationGuard(),
        bot_username="safwa_ai_bot",
        advisor=advisor,
        transcriber=transcriber,
    )


def button_texts(markup) -> list[str]:
    return [button.text for row in markup.inline_keyboard for button in row]


async def test_every_command_is_deleted_and_still_dispatched(sessions, monkeypatch) -> None:
    import safwa.telegram._core as core_module

    monkeypatch.setattr(core_module, "Message", FakeMessage)
    middleware = OwnerAndWritingMiddleware()
    services = services_for(sessions)
    handled: list[str] = []

    async def handler(event, _data):
        handled.append(event.text)

    for index, command in enumerate(("/start", "/mem remember this", "/cancel"), start=1):
        message = FakeMessage(index, text=command, bot_message=False)
        await middleware(handler, message, {"services": services})
        assert message.was_deleted is True

    assert handled == ["/start", "/mem remember this", "/cancel"]


async def test_messages_are_queued_with_placeholders_and_restored_as_one_turn(
    sessions, monkeypatch
) -> None:
    import safwa.telegram._core as core_module

    monkeypatch.setattr(core_module, "Message", FakeMessage)
    middleware = OwnerAndWritingMiddleware()
    services = services_for(sessions)
    assert services.guard.reserve(1, queue_messages=True)
    handled: list[str] = []

    async def handler(event, _data):
        handled.append(event.text)

    first = FakeMessage(2, text="First queued request", bot_message=False, answer_as_new=True)
    second = FakeMessage(
        3,
        text="Second queued request",
        bot_message=False,
        bot=first.bot,
        answer_as_new=True,
    )
    await middleware(handler, first, {"services": services})
    await middleware(handler, second, {"services": services})

    assert first.was_deleted and second.was_deleted
    assert handled == []
    assert read_kind_mark(first.answers[0])[1] == (
        "Generating response... /cancel for cancelling.\nQueued: First queued request"
    )

    anchor = FakeMessage(1, text="Active request", bot_message=False, bot=first.bot, answer_as_new=True)
    restored = await materialize_queued_dialogue(anchor, services)

    assert restored is not None
    sent, request = restored
    assert request == "User Name Surname:\nFirst queued request\n\n----\n\nSecond queued request"
    assert read_kind_mark(sent.text) == (
        MessageKind.DIALOGUE_USER.value,
        "<b>User Name Surname:</b>\nFirst queued request\n\n----\n\nSecond queued request",
    )
    assert first.bot.deleted_batches == [[1002, 1003]]


async def test_proposal_ui_releases_generation_guard_before_continuity_work(sessions) -> None:
    async with sessions() as session:
        workspace = await session.get(Workspace, 1)
        proposal = ChangeProposal(
            message="Create VrWalk",
            workspace_revision=workspace.revision,
            status="pending",
        )
        session.add(proposal)
        await session.flush()
        session.add(
            ProposalChange(
                proposal_id=proposal.id,
                position=0,
                entity="tag",
                action="create",
                values={"name": "VrWalk"},
            )
        )
        await session.commit()
        proposal_id = proposal.id

    class Advisor:
        async def handle(self, *_args, **_kwargs):
            return AIOutcome(
                "proposal",
                "I prepared the proposed changes for your approval.",
                proposal_id=proposal_id,
            )

    class History:
        async def dialogue(self, *_args, **_kwargs):
            return [DialogueMessage(role="user", content="[Initial request]: Create a Tag")]

    guard = GenerationGuard()

    class Continuity:
        called = False

        async def maybe_summarize(self, *_args, **_kwargs):
            self.called = True
            assert guard.background is True

    continuity = Continuity()
    services = SimpleNamespace(
        sessions=sessions,
        owner_id=42,
        guard=guard,
        advisor=Advisor(),
        history=History(),
        continuity=continuity,
    )
    message = FakeMessage(20, text="Create a Tag VrWalk", bot_message=False)

    await ordinary_text(message, services)

    assert continuity.called is True
    assert guard.active is False
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
    async with sessions() as session:
        workspace = await session.get(Workspace, 1)
        proposal = ChangeProposal(
            message="Rename the Tag",
            workspace_revision=workspace.revision,
            status="pending",
        )
        session.add(proposal)
        await session.flush()
        session.add(
            ProposalChange(
                proposal_id=proposal.id,
                position=0,
                entity="tag",
                action="update",
                entity_id=9,
                expected_version=1,
                values={"name": "Family"},
            )
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
    await dismiss_prior_ui(incoming, services_for(sessions, advisor=StubAdvisor()))

    assert bot.deleted == [9]
    assert bot.edits[0][0] == 10
    assert "🗑 Discarded" in bot.edits[0][1]
    assert "You continued the conversation without saving it." in bot.edits[0][1]
    assert "Rename Tag “Family”" in bot.edits[0][1]
    assert "• Name: Home → Family" in bot.edits[0][1]
    async with sessions() as session:
        proposal = await session.get(ChangeProposal, proposal_id)
        assert proposal.status == "rejected"
        frozen = await session.scalar(
            select(TelegramMessage).where(TelegramMessage.message_id == 10)
        )
        assert frozen.kind == MessageKind.DIALOGUE_ASSISTANT.value
        assert (
            await session.scalar(select(TelegramMessage).where(TelegramMessage.message_id == 9))
            is None
        )


async def test_a_command_dismisses_every_other_screen(sessions) -> None:
    """A command is the owner walking away, so the middleware answers the open screens."""
    from safwa.telegram.commands import dismiss_screens_before_a_command

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
                    kind=MessageKind.CARD_EDITOR.value,
                ),
            ]
        )
        await session.commit()

    bot = FakeBot()
    services = services_for(sessions, advisor=StubAdvisor())
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


async def test_tag_field_input_reuses_editor_message_and_deletes_input(sessions) -> None:
    services = services_for(sessions)
    callback = FakeMessage(30, bot_message=True)
    await render_item_editor(callback, services, "tag", mode="create")
    await render_item_text_prompt(
        callback,
        services,
        entity="tag",
        mode="create",
        item_id=None,
        field="name",
    )
    prompt_id, prompt_text, prompt_markup = callback.bot.edits[-1]
    assert prompt_id == 30
    assert "Current value:\n<pre>—</pre>" in prompt_text
    assert button_texts(prompt_markup) == ["↩️ Back"]

    invalid_input = FakeMessage(
        31,
        text="   ",
        bot_message=False,
        bot=callback.bot,
    )
    await ordinary_text(invalid_input, services)

    assert invalid_input.was_deleted is True
    assert "Tag name cannot be empty" in callback.bot.edits[-1][1]
    async with sessions() as session:
        assert (await session.scalar(select(UiSession))).kind == "text_input"

    user_input = FakeMessage(
        32,
        text="Family",
        bot_message=False,
        bot=callback.bot,
    )
    await ordinary_text(user_input, services)

    assert user_input.was_deleted is True
    assert user_input.answers == []
    assert callback.bot.edits[-1][0] == 30
    assert "Name: Family" in callback.bot.edits[-1][1]
    async with sessions() as session:
        ui = await session.scalar(select(UiSession).where(UiSession.owner_id == 42))
        assert ui.kind == "item_editor"
        assert ui.state["values"]["name"] == "Family"
        classified = await session.scalar(
            select(TelegramMessage).where(TelegramMessage.message_id == 32)
        )
        assert classified.kind == MessageKind.UI_INPUT.value


async def test_manual_tag_and_value_archive_unlinks_cards(sessions) -> None:
    async with sessions() as session:
        card = Card(kind="action", title="Family walk", effort_points=2)
        tag = Tag(name="Family")
        value = Value(name="Connection", active=True)
        session.add_all([card, tag, value])
        await session.flush()
        session.add_all(
            [
                CardTag(card_id=card.id, tag_id=tag.id),
                CardValue(card_id=card.id, value_id=value.id),
            ]
        )
        await session.commit()
        tag_id, value_id = tag.id, value.id

    services = services_for(sessions)
    for index, (entity, item_id) in enumerate((("tag", tag_id), ("value", value_id)), start=70):
        message = FakeMessage(index, bot_message=True)
        await render_item_editor(message, services, entity, mode="view", item_id=item_id)
        text, markup = message.edits[-1]
        assert "Linked Cards: 1" in text
        archive = next(
            button
            for row in markup.inline_keyboard
            for button in row
            if button.text == f"Archive {entity.title()}"
        )
        await callback_token_handler(
            FakeCallback(archive.callback_data.split(":", 1)[1], message), services
        )
        confirm = next(
            button
            for row in message.edits[-1][1].inline_keyboard
            for button in row
            if button.text == f"Archive {entity.title()}"
        )
        await callback_token_handler(
            FakeCallback(confirm.callback_data.split(":", 1)[1], message), services
        )
        assert "Removed 1 Card link(s)" in message.edits[-1][0]

    async with sessions() as session:
        tag = await session.get(Tag, tag_id)
        value = await session.get(Value, value_id)
        assert tag.archived_at is not None
        assert value.archived_at is not None
        assert value.active is False
        assert await session.get(CardTag, {"card_id": card.id, "tag_id": tag_id}) is None
        assert await session.get(CardValue, {"card_id": card.id, "value_id": value_id}) is None


async def test_card_note_input_updates_same_creation_message(sessions) -> None:
    async with sessions() as session:
        session.add(
            UiSession(
                owner_id=42,
                    kind="text_input",
                    state={
                        "flow": "card_create",
                    "kind": "action",
                    "title": "Run",
                    "note": "",
                    "stage": "backlog",
                    "priority": "medium",
                    "hard_time": False,
                    "blocked": False,
                    "blocked_description": "",
                    "effort_points": 2,
                    "repeatable": False,
                    "categories": [],
                    "energy_types": [],
                    "value_ids": [],
                    "tag_ids": [],
                        "input_field": "note",
                        "text_input": {
                            "message_id": 40,
                            "title": "Edit Card Note",
                            "current_value": "",
                            "instruction": "Send the new note.",
                            "back_action": "card_create_view",
                            "back_payload": {},
                            "related_id": None,
                            "ttl_seconds": 1800,
                            "extra_actions": [],
                        },
                },
                expires_at=datetime.now(UTC).replace(year=2030),
            )
        )
        await session.commit()

    bot = FakeBot()
    user_input = FakeMessage(41, text="Weekdays", bot_message=False, bot=bot)
    await ordinary_text(user_input, services_for(sessions))

    assert user_input.was_deleted is True
    assert bot.edits[-1][0] == 40
    assert "Note: Weekdays" in bot.edits[-1][1]
    async with sessions() as session:
        editor = await session.scalar(select(UiSession).where(UiSession.owner_id == 42))
        assert editor.kind == "card_create"
        assert editor.state["note"] == "Weekdays"


async def test_dashboard_paging_walks_between_pages(sessions) -> None:
    async with sessions() as session:
        for index in range(7):
            await create_card(
                session, kind="action", title=f"Task {index}", stage="backlog", effort_points=1
            )
        await session.commit()

    services = services_for(sessions)
    message = FakeMessage(95, bot_message=True)
    await render_dashboard(message, services, CardStage.BACKLOG, title="Backlog")

    text, markup = message.edits[-1]
    assert "page 1/2" in text
    assert "◀ Previous" not in button_texts(markup)
    nxt = next(button for row in markup.inline_keyboard for button in row if button.text == "Next ▶")

    await callback_token_handler(FakeCallback(nxt.callback_data.split(":", 1)[1], message), services)

    text, markup = message.edits[-1]
    assert "page 2/2" in text
    assert "◀ Previous" in button_texts(markup)
    assert "Next ▶" not in button_texts(markup)


async def test_tag_selector_pages_instead_of_truncating(sessions) -> None:
    async with sessions() as session:
        card = await create_card(session, kind="action", title="Pick tags", effort_points=1)
        for index in range(12):
            await create_tag(session, f"Tag {index:02d}")
        await session.commit()
        card_id = card.id

    services = services_for(sessions)
    message = FakeMessage(96, bot_message=True)
    await render_card_choices(message, services, "card_choose_tags", card_id)

    text, markup = message.edits[-1]
    names = [name for name in button_texts(markup) if name.startswith("Tag ")]
    assert "page 1/2" in text
    assert names == [f"Tag {index:02d}" for index in range(10)]

    nxt = next(button for row in markup.inline_keyboard for button in row if button.text == "Next ▶")
    await callback_token_handler(FakeCallback(nxt.callback_data.split(":", 1)[1], message), services)

    text, markup = message.edits[-1]
    names = [name for name in button_texts(markup) if name.startswith("Tag ")]
    # Tag 11 used to be unreachable: the selector stopped at a hard limit with no paging.
    assert "page 2/2" in text
    assert names == ["Tag 10", "Tag 11"]


async def test_moving_a_blocked_card_shows_its_warning_on_the_card_screen(sessions) -> None:
    async with sessions() as session:
        card = await create_card(
            session,
            kind="action",
            title="Waiting",
            stage="today",
            effort_points=2,
            blocked=True,
            blocked_description="Need account access",
        )
        await session.commit()
        card_id = card.id

    services = services_for(sessions)
    message = FakeMessage(90, bot_message=True)
    await render_card(message, services, card_id)

    stage = next(
        button
        for row in message.edits[-1][1].inline_keyboard
        for button in row
        if button.text == "📍 Stage"
    )
    await callback_token_handler(
        FakeCallback(stage.callback_data.split(":", 1)[1], message), services
    )
    backlog = next(
        button
        for row in message.edits[-1][1].inline_keyboard
        for button in row
        if "Backlog" in button.text
    )
    await callback_token_handler(
        FakeCallback(backlog.callback_data.split(":", 1)[1], message), services
    )

    # A callback replaces the current message, so the warning has to arrive as part of
    # the destination screen rather than as a message the next render overwrites.
    text = message.edits[-1][0]
    assert "Need account access" in text
    assert "Stage: Backlog" in text


async def test_checks_button_is_on_the_card_only(sessions) -> None:
    async with sessions() as session:
        card = await create_card(session, kind="action", title="Card", effort_points=1)
        value = await create_value(session, "Value")
        tag = await create_tag(session, "Tag")
        await session.commit()
        card_id, value_id, tag_id = card.id, value.id, tag.id

    services = services_for(sessions)
    # The button appears only once a Check hangs on the Card. Tags and Values never carry it.
    message = FakeMessage(card_id, bot_message=True)
    await render_card(message, services, card_id)
    assert not any("Checks" in text for text in button_texts(message.edits[-1][1]))
    for entity, item_id in (("value", value_id), ("tag", tag_id)):
        message = FakeMessage(item_id, bot_message=True)
        await render_item_editor(message, services, entity, mode="view", item_id=item_id)
        assert not any("Checks" in text for text in button_texts(message.edits[-1][1]))

    async with sessions() as session:
        linked = await create_check(session, title="Linked")
        await toggle_card_check(session, card_id, linked.id)
        await session.commit()

    message = FakeMessage(card_id + 100, bot_message=True)
    await render_card(message, services, card_id)
    assert any("Checks (1/1)" in text for text in button_texts(message.edits[-1][1]))


async def test_open_item_screen_renders_the_manual_screen_of_every_item(sessions) -> None:
    async with sessions() as session:
        await (await session.connection()).run_sync(create_ai_views)
        card = await create_card(session, kind="action", title="Pull-ups", effort_points=1)
        value = await create_value(session, "Health")
        tag = await create_tag(session, "Training")
        check = await create_check(session, title="Form is safe")
        request = await create_saved_request(
            session, "Open actions", "SELECT id FROM ai_cards WHERE kind = 'action'"
        )
        await session.commit()
        targets = [
            ("card", card.id, "Pull-ups"),
            ("value", value.id, "Health"),
            ("tag", tag.id, "Training"),
            ("check", check.id, "Form is safe"),
            ("request", request.id, "Open actions"),
        ]

    services = services_for(sessions)
    for index, (item_type, item_id, expected) in enumerate(targets):
        message = FakeMessage(900 + index, bot_message=True)
        await open_item_screen(message, services, item_type, item_id)
        text, markup = message.edits[-1]
        assert expected in text
        # "Opened" means the real screen, buttons included — not a read-only summary.
        assert button_texts(markup)

    message = FakeMessage(999, bot_message=True)
    with pytest.raises(DomainError):
        await open_item_screen(message, services, "sprint", 1)


async def test_citations_become_deep_links_only_for_live_items(sessions) -> None:
    async with sessions() as session:
        card = await create_card(session, kind="action", title="Pull-ups", effort_points=1)
        tag = await create_tag(session, "Training")
        await archive_tag(session, tag.id)
        await session.commit()
        card_id, tag_id = card.id, tag.id

    services = services_for(sessions)
    text = html.escape(
        f"[Pull & ups](card:{card_id}) under [Training](tag:{tag_id}), "
        "[nothing](card:4242), [not one](sprint:1)."
    )
    async with sessions() as session:
        rendered = await render_citations(session, services, text)

    assert (
        f'<a href="https://t.me/safwa_ai_bot?start=card-{card_id}">⭐️ Pull-ups · ⚡1</a>'
        in rendered
    )
    # An archived item is as gone as a deleted one, and an unknown type is not a citation.
    assert f"[Training](tag:{tag_id})" not in rendered and "Training" in rendered
    assert "nothing" in rendered and "card-4242" not in rendered
    assert "[not one](sprint:1)" in rendered

    services.bot_username = ""
    async with sessions() as session:
        assert "<a href" not in await render_citations(session, services, text)


async def test_a_citation_aimed_at_something_that_is_not_an_id_keeps_only_its_words(
    sessions,
) -> None:
    """A stamp is not an id, and raw Markdown must never reach the chat."""
    services = services_for(sessions)
    text = html.escape("Записал [📅 17 августа 2026 · 🌟5](diary:wwsouhzg_fB8) за сегодня.")

    async with sessions() as session:
        rendered = await render_citations(session, services, text)

    assert "diary:wwsouhzg_fB8" not in rendered and "<a href" not in rendered
    assert "Записал 📅 17 августа 2026 · 🌟5 за сегодня." in rendered


async def test_citations_use_compact_labels_from_saved_items(sessions) -> None:
    async with sessions() as session:
        await (await session.connection()).run_sync(create_ai_views)
        goal = await create_card(session, kind="goal", title="Быть здоровым")
        action = await create_card(
            session,
            kind="action",
            title="Бегать 3 км",
            parent_id=goal.id,
            effort_points=2,
            categories={"self"},
            energy_types={"physical", "social"},
        )
        value = await create_value(session, "Свобода")
        tag = await create_tag(session, "Здоровье")
        long_tag = await create_tag(session, "x" * 26)
        request = await create_saved_request(
            session, "План на неделю", "SELECT id FROM ai_cards WHERE kind = 'action'"
        )
        diary = await create_diary_entry(
            session, entry_date=date(2026, 8, 16), body="Хороший день.", feeling_score=6
        )
        await session.commit()

    services = services_for(sessions)
    text = html.escape(
        " ".join(
            (
                f"[goal](card:{goal.id})",
                f"[action](card:{action.id})",
                f"[value](value:{value.id})",
                f"[tag](tag:{tag.id})",
                f"[long tag](tag:{long_tag.id})",
                f"[request](request:{request.id})",
                f"[diary](diary:{diary.id})",
            )
        )
    )
    async with sessions() as session:
        rendered = await render_citations(session, services, text)

    expected = {
        f"card-{goal.id}": "🎯 Быть здоровым · ⚡0/2",
        f"card-{action.id}": "⭐️ Бегать 3 км · 💪🤝·🌱·⚡2",
        f"value-{value.id}": "💎 Свобода",
        f"tag-{tag.id}": "🏷 Здоровье",
        f"tag-{long_tag.id}": f"🏷 {'x' * 24}…",
        f"request-{request.id}": "💬 План на неделю · 1",
        f"diary-{diary.id}": "16 августа · 🙂6",
    }
    for payload, label in expected.items():
        assert f'?start={payload}">{label}</a>' in rendered


async def test_ai_markdown_is_rendered_as_safe_html_around_live_citations(sessions) -> None:
    async with sessions() as session:
        goal = await create_card(session, kind="goal", title="Быть здоровым")
        await session.commit()
        goal_id = goal.id

    message = FakeMessage(949, bot_message=False)
    await render_ai_outcome(
        message,
        services_for(sessions),
        AIOutcome(
            "answer",
            f"Это **важно**: **[цель](card:{goal_id})**; *курсив*, ~~нет~~, "
            "`x < y` и <script>.",
        ),
    )
    _kind, visible = read_kind_mark(message.answers[-1])

    assert "<b>важно</b>" in visible
    assert f'<b><a href="https://t.me/safwa_ai_bot?start=card-{goal_id}">' in visible
    assert "</a></b>" in visible
    assert "<i>курсив</i>" in visible
    assert "<s>нет</s>" in visible
    assert "<code>x &lt; y</code>" in visible
    assert "&lt;script&gt;" in visible
    assert "**" not in visible


async def test_a_diary_citation_is_named_by_the_entry_and_opens_the_whole_day(sessions) -> None:
    async with sessions() as session:
        entry = await create_diary_entry(
            session,
            entry_date=date(2026, 3, 4),
            body="Долгий день, но рынок закрыл.",
            feeling_score=6,
        )
        await session.commit()
        entry_id = entry.id

    services = services_for(sessions)
    async with sessions() as session:
        # Whatever the model wrote as the label, the link shows the saved date and score.
        rendered = await render_citations(
            session, services, html.escape(f"Тот день: [что-то своё](diary:{entry_id}).")
        )
    assert (
        f'<a href="https://t.me/safwa_ai_bot?start=diary-{entry_id}">4 марта · 🙂6</a>'
        in rendered
    )

    # The compact score comes back out of Telegram as part of the label, and still links.
    async with sessions() as session:
        assert f"?start=diary-{entry_id}" in await render_citations(
            session, services, html.escape(f"[4 марта · 🙂6](diary:{entry_id})")
        )

    message = FakeMessage(970, bot_message=True)
    await open_item_screen(message, services, "diary", entry_id)
    text, markup = message.edits[-1]
    assert "📔 4 марта · 🙂6" in text
    assert "Долгий день, но рынок закрыл." in text
    # The Diary is written through proposals alone, so its screen offers no control.
    assert markup is None


def test_citation_codec_matches_every_openable_item_screen() -> None:
    expected = {"card", "check", "tag", "value", "request", "diary"}
    assert set(CITATION_TYPES) == expected
    assert set(OPENABLE_MODELS) == expected


def test_start_payload_reads_only_a_command_line() -> None:
    assert start_payload("/start card-12") == "card-12"
    assert start_payload("/start@safwa_ai_bot card-12") == "card-12"
    assert start_payload("/start") is None
    # The menu's Home button hands command_start the bot's own screen, never a command.
    assert start_payload("<b>Card</b>: Pull ups") is None
    assert parse_citation_payload("check-14") == ("check", 14)
    assert parse_citation_payload("sprint-1") is None


async def test_card_text_field_prompt_replaces_creation_message(sessions) -> None:
    async with sessions() as session:
        session.add(
            UiSession(
                owner_id=42,
                kind="card_create",
                state={"kind": "action", "title": "", "effort_points": None},
                expires_at=datetime.now(UTC).replace(year=2030),
            )
        )
        session.add(
            CallbackToken(
                token="card-title",
                owner_id=42,
                action="card_create_edit_text",
                payload={"field": "title"},
                expires_at=datetime.now(UTC).replace(year=2030),
            )
        )
        await session.commit()

    message = FakeMessage(45, bot_message=True)
    await callback_token_handler(FakeCallback("card-title", message), services_for(sessions))

    assert message.answers == []
    prompt_id, prompt_text, prompt_markup = message.bot.edits[-1]
    assert prompt_id == 45
    assert "<b>Edit Card Title</b>" in prompt_text
    assert "Current value:\n<pre>—</pre>" in prompt_text
    assert button_texts(prompt_markup) == ["↩️ Back"]

    blank = FakeMessage(46, text=" ", bot_message=False, bot=message.bot)
    await ordinary_text(blank, services_for(sessions))

    assert blank.was_deleted is True
    assert "Card title cannot be empty" in message.bot.edits[-1][1]
    async with sessions() as session:
        assert (await session.scalar(select(UiSession))).kind == "text_input"


async def test_card_text_and_blocked_reason_stay_on_one_validated_editor(sessions) -> None:
    async with sessions() as session:
        card = await create_card(session, kind="idea", title="Original")
        await session.commit()
        card_id = card.id

    services = services_for(sessions)
    message = FakeMessage(47, bot_message=True)
    await render_card(message, services, card_id)

    title_button = next(
        button
        for row in message.edits[-1][1].inline_keyboard
        for button in row
        if button.text == "✏️ Title"
    )
    await callback_token_handler(
        FakeCallback(title_button.callback_data.split(":", 1)[1], message), services
    )
    assert "Current value:\n<pre>Original</pre>" in message.bot.edits[-1][1]

    invalid_title = FakeMessage(48, text=" ", bot_message=False, bot=message.bot)
    await ordinary_text(invalid_title, services)
    assert "Card title cannot be empty" in message.bot.edits[-1][1]

    valid_title = FakeMessage(49, text="Renamed", bot_message=False, bot=message.bot)
    await ordinary_text(valid_title, services)
    assert valid_title.was_deleted is True
    assert "Renamed" in message.bot.edits[-1][1]

    blocked_button = next(
        button
        for row in message.bot.edits[-1][2].inline_keyboard
        for button in row
        if button.text == "🚧 Blocked"
    )
    await callback_token_handler(
        FakeCallback(blocked_button.callback_data.split(":", 1)[1], message), services
    )
    assert "Mark Card as blocked" in message.bot.edits[-1][1]

    invalid_reason = FakeMessage(50, text=" ", bot_message=False, bot=message.bot)
    await ordinary_text(invalid_reason, services)
    assert "Blocked description cannot be empty" in message.bot.edits[-1][1]

    valid_reason = FakeMessage(51, text="Waiting for API access", bot_message=False, bot=message.bot)
    await ordinary_text(valid_reason, services)
    async with sessions() as session:
        card = await session.get(Card, card_id)
        assert (card.blocked, card.blocked_description) == (True, "Waiting for API access")
    assert "Waiting for API access" in message.bot.edits[-1][1]


async def test_reminder_text_requires_a_value_and_restores_its_view(sessions) -> None:
    async with sessions() as session:
        reminder = await create_reminder(
            session,
            instruction="Take a walk.",
            schedule=resolve(interval_minutes=120, now=datetime.now(UTC), tz=ZoneInfo("UTC")),
            tz=ZoneInfo("UTC"),
        )
        await session.commit()
        reminder_id = reminder.id

    services = services_for(sessions)
    message = FakeMessage(52, bot_message=True)
    await render_reminder(message, services, reminder_id)
    edit = next(
        button
        for row in message.edits[-1][1].inline_keyboard
        for button in row
        if button.text == "✏️ Text"
    )
    await callback_token_handler(FakeCallback(edit.callback_data.split(":", 1)[1], message), services)
    assert "Current value:\n<pre>Take a walk.</pre>" in message.bot.edits[-1][1]

    invalid = FakeMessage(53, text=" ", bot_message=False, bot=message.bot)
    await ordinary_text(invalid, services)
    assert "Reminder text cannot be empty" in message.bot.edits[-1][1]

    valid = FakeMessage(54, text="Walk around the block.", bot_message=False, bot=message.bot)
    await ordinary_text(valid, services)
    assert valid.was_deleted is True
    assert "Walk around the block." in message.bot.edits[-1][1]


async def test_manual_card_creation_uses_save_discard_and_no_parent_control(sessions) -> None:
    async with sessions() as session:
        session.add(
            UiSession(
                owner_id=42,
                kind="card_create",
                state={
                    "kind": "action",
                    "title": "Run",
                    "effort_points": 2,
                },
                expires_at=datetime.now(UTC).replace(year=2030),
            )
        )
        await session.commit()

    message = FakeMessage(51, bot_message=True)
    await render_card_creation(message, services_for(sessions))
    buttons = button_texts(message.edits[-1][1])
    assert "✅ Save" in buttons
    assert "🗑 Discard" in buttons
    assert "🌳 Parent" not in buttons


async def test_card_creation_choosers_show_kind_category_and_energy_emojis(sessions) -> None:
    async with sessions() as session:
        session.add(
            UiSession(
                owner_id=42,
                kind="card_create",
                state={
                    "kind": "action",
                    "title": "Run",
                    "effort_points": 2,
                    "categories": [],
                    "energy_types": [],
                },
                expires_at=datetime.now(UTC).replace(year=2030),
            )
        )
        await session.commit()

    message = FakeMessage(52, bot_message=True)
    services = services_for(sessions)

    await handle_card_creation_chooser(message, services, "card_create_choose_kind")
    assert {"🎯 Goal", "💡 Idea", "✓ ⭐️ Action"} <= set(button_texts(message.edits[-1][1]))

    await handle_card_creation_chooser(message, services, "card_create_choose_categories")
    assert {"🌱 Self", "❤️ Contribution", "💰 Work", "🔋 Rest"} <= set(
        button_texts(message.edits[-1][1])
    )

    await handle_card_creation_chooser(message, services, "card_create_choose_energy")
    assert {"💪 Physical", "🧠 Cognitive", "🤝 Social", "💎 Values"} <= set(
        button_texts(message.edits[-1][1])
    )


async def test_card_overview_uses_derived_progress_and_relationship_navigation(sessions) -> None:
    async with sessions() as session:
        goal = await create_card(session, title="Ship product", kind="goal")
        idea = await create_card(
            session,
            title="Prepare release",
            kind="idea",
            parent_id=goal.id,
        )
        done = await create_card(
            session,
            title="Publish build",
            kind="action",
            parent_id=idea.id,
            effort_points=3,
        )
        remaining = await create_card(
            session,
            title="Write announcement",
            kind="action",
            parent_id=goal.id,
            effort_points=5,
        )
        await finish_action(session, done.id, CardStage.DONE)
        await session.commit()

    bot = FakeBot()
    goal_message = FakeMessage(70, bot_message=True, bot=bot)
    await render_card(
        goal_message,
        services_for(sessions),
        goal.id,
        replace_message_id=goal_message.message_id,
    )
    goal_text, goal_markup = bot.edits[-1][1:]
    assert "Kind: 🎯 Goal" in goal_text
    assert "Stage: Backlog" in goal_text
    assert "Effort: 3/8 EP" in goal_text
    assert "Children: 1/2 completed" in goal_text
    assert "Parent:" not in goal_text
    assert "👥 Children" in button_texts(goal_markup)
    assert not any(text.startswith("🌳 Parent:") for text in button_texts(goal_markup))

    children_message = FakeMessage(73, bot_message=True)
    await render_children(children_message, services_for(sessions), goal.id)
    children_texts = button_texts(children_message.edits[-1][1])
    assert any("💡 Idea · Prepare release" in text for text in children_texts)
    assert any("⭐️ Action · Write announcement" in text for text in children_texts)
    assert not any("Publish build" in text for text in children_texts)

    child_message = FakeMessage(71, bot_message=True, bot=bot)
    await render_card(
        child_message,
        services_for(sessions),
        remaining.id,
        replace_message_id=child_message.message_id,
    )
    child_text, child_markup = bot.edits[-1][1:]
    assert "Kind: ⭐️ Action" in child_text
    assert "Parent: Ship product" in child_text
    assert "🌳 Parent: Ship product" in button_texts(child_markup)
    assert "👥 Children" not in button_texts(child_markup)


async def test_backlog_dashboard_lists_actions_only(sessions) -> None:
    async with sessions() as session:
        await create_card(session, title="Hidden Goal", kind="goal")
        await create_card(session, title="Visible Action", kind="action", effort_points=2)
        await session.commit()

    message = FakeMessage(72, bot_message=True)
    await render_dashboard(
        message,
        services_for(sessions),
        CardStage.BACKLOG,
        title="Backlog",
    )

    dashboard_text, dashboard_markup = message.edits[-1]
    assert "⭐️ Action" in dashboard_text
    assert "Visible Action" in dashboard_text
    assert "Hidden Goal" not in dashboard_text
    assert any("Visible Action" in text for text in button_texts(dashboard_markup))


async def test_menu_offers_today_only_while_a_sprint_runs(sessions) -> None:
    services = services_for(sessions)
    message = FakeMessage(74, bot_message=True)

    await command_start(message, services)
    assert "☀️ Today" not in button_texts(message.edits[-1][1])

    async with sessions() as session:
        await start_sprint(session, success_criteria="Ship v2")
        await session.commit()

    await command_start(message, services)
    assert "☀️ Today" in button_texts(message.edits[-1][1])


async def test_today_screen_is_closed_during_planning(sessions) -> None:
    message = FakeMessage(75, bot_message=True)

    await render_today(message, services_for(sessions))

    text, markup = message.edits[-1]
    assert "Plan the next Sprint first" in text
    assert button_texts(markup) == ["↩️ Menu"]


async def test_quick_move_buttons_walk_an_action_between_today_and_sprint(sessions) -> None:
    async with sessions() as session:
        action = await create_card(
            session, kind="action", title="Ship it", stage="today", effort_points=2
        )
        await start_sprint(session, success_criteria="Ship v2")
        await session.commit()
        action_id = action.id

    services = services_for(sessions)
    message = FakeMessage(76, bot_message=True)
    await render_today(message, services)

    text, markup = message.edits[-1]
    assert "Ship it" in text
    move_to_sprint = next(
        button for row in markup.inline_keyboard for button in row if button.text == "🏃"
    )
    assert markup.inline_keyboard[0][0] is move_to_sprint

    await callback_token_handler(
        FakeCallback(move_to_sprint.callback_data.split(":", 1)[1], message), services
    )

    async with sessions() as session:
        assert (await session.get(Card, action_id)).effective_stage == CardStage.SPRINT.value
    text, markup = message.edits[-1]
    assert "Ship it" not in text

    await render_sprint(message, services)
    text, markup = message.edits[-1]
    move_to_today = next(
        button for row in markup.inline_keyboard for button in row if button.text == "☀️"
    )
    assert markup.inline_keyboard[0][-1] is move_to_today

    await callback_token_handler(
        FakeCallback(move_to_today.callback_data.split(":", 1)[1], message), services
    )

    async with sessions() as session:
        assert (await session.get(Card, action_id)).effective_stage == CardStage.TODAY.value


async def test_starting_a_sprint_needs_criteria_then_confirms_the_plan(sessions) -> None:
    async with sessions() as session:
        await create_card(
            session, kind="action", title="Sprint work", stage="sprint", effort_points=2
        )
        await create_card(
            session, kind="action", title="Today work", stage="today", effort_points=3
        )
        await session.commit()

    services = services_for(sessions)
    message = FakeMessage(77, bot_message=True, answer_as_new=True)
    await render_sprint(message, services)

    text, markup = message.edits[-1]
    assert "Success criteria: not set yet" in text
    start = next(
        button
        for row in markup.inline_keyboard
        for button in row
        if button.text.startswith("▶️ Start")
    )
    assert start.text == "▶️ Start 14-day Sprint"

    await callback_token_handler(FakeCallback(start.callback_data.split(":", 1)[1], message), services)

    prompt_id, prompt_text, prompt_markup = message.bot.edits[-1]
    assert prompt_id == 77
    assert "Send what this Sprint must achieve" in prompt_text
    assert button_texts(prompt_markup) == ["↩️ Back"]
    async with sessions() as session:
        assert (await session.scalar(select(UiSession))).kind == "text_input"

    invalid = FakeMessage(78, text=" ", bot_message=False, bot=message.bot)
    await ordinary_text(invalid, services)
    assert invalid.was_deleted is True
    assert "Success criteria cannot be empty" in message.bot.edits[-1][1]

    typed = FakeMessage(79, text="Ship v2 to production", bot_message=False, bot=message.bot)
    await ordinary_text(typed, services)

    planning_id, planning_text, planning_markup = message.bot.edits[-1]
    assert planning_id == 77
    assert "Success criteria: Ship v2 to production" in planning_text
    assert "▶️ Start 14-day Sprint" in button_texts(planning_markup)

    start_again = next(
        button
        for row in planning_markup.inline_keyboard
        for button in row
        if button.text.startswith("▶️ Start")
    )
    await callback_token_handler(
        FakeCallback(start_again.callback_data.split(":", 1)[1], message), services
    )
    continue_button = next(
        button
        for row in message.bot.edits[-1][2].inline_keyboard
        for button in row
        if button.text == "✅ Continue to plan"
    )
    await callback_token_handler(
        FakeCallback(continue_button.callback_data.split(":", 1)[1], message), services
    )
    confirm_text, confirm_markup = message.edits[-1]
    # Both Sprint and Today Actions are committed, so both are shown before Start.
    assert "Sprint work" in confirm_text
    assert "☀️ Today work" in confirm_text
    assert "Selected effort: 5 EP" in confirm_text
    assert "✅ Confirm plan: Start" in button_texts(confirm_markup)

    confirm = next(
        button
        for row in confirm_markup.inline_keyboard
        for button in row
        if button.text == "✅ Confirm plan: Start"
    )
    await callback_token_handler(
        FakeCallback(confirm.callback_data.split(":", 1)[1], message), services
    )

    async with sessions() as session:
        workspace = await session.get(Workspace, 1)
        assert workspace.active_sprint_id is not None
        sprint = await session.get(Sprint, workspace.active_sprint_id)
        assert sprint.success_criteria == "Ship v2 to production"
        assert len(list(await session.scalars(select(Reminder)))) == 2
    # The Today command becomes available again the moment the Sprint exists.
    assert "today" in message.bot.published_commands[-1]

    finish = next(
        button
        for row in message.edits[-1][1].inline_keyboard
        for button in row
        if button.text == "⏹ Finish early"
    )
    await callback_token_handler(
        FakeCallback(finish.callback_data.split(":", 1)[1], message), services
    )

    assert "today" not in message.bot.published_commands[-1]
    async with sessions() as session:
        assert (await session.get(Workspace, 1)).active_sprint_id is None
        assert await session.scalar(select(Reminder).limit(1)) is None


async def test_the_confirm_screen_refuses_an_empty_plan(sessions) -> None:
    async with sessions() as session:
        await set_sprint_success_criteria(session, "Ship v2")
        await session.commit()

    message = FakeMessage(79, bot_message=True)
    await render_sprint_confirm(message, services_for(sessions))

    text, markup = message.edits[-1]
    assert "Move Actions to the Sprint stage first" in text
    assert "✅ Confirm plan: Start" not in button_texts(markup)


async def test_item_proposal_shows_diffs_and_only_save_discard_footer(sessions) -> None:
    async with sessions() as session:
        tag = Tag(name="Family", description="Old description")
        session.add(tag)
        await session.flush()
        workspace = await session.get(Workspace, 1)
        proposal = ChangeProposal(
            message="Improve the Family Tag",
            workspace_revision=workspace.revision,
            status="pending",
        )
        session.add(proposal)
        await session.flush()
        session.add(
            ProposalChange(
                proposal_id=proposal.id,
                position=0,
                entity="tag",
                action="update",
                entity_id=tag.id,
                expected_version=tag.version,
                values={"description": "Relationships and home"},
            )
        )
        await session.commit()
        proposal_id = proposal.id

    message = FakeMessage(60, bot_message=True)
    await render_proposal(message, services_for(sessions), proposal_id)
    text, markup = message.edits[-1]
    assert "Old description" in text
    assert "Relationships and home" in text
    assert "→" in text
    buttons = button_texts(markup)
    assert buttons == ["✅ Save", "🗑 Discard"]
    assert "↩️ Back" not in buttons


async def test_card_proposal_uses_full_card_editor_with_human_diffs(sessions) -> None:
    async with sessions() as session:
        card = Card(
            kind="action",
            title="Evening walk",
            note="After work",
            effort_points=3,
        )
        session.add(card)
        await session.flush()
        session.add(CardCategory(card_id=card.id, category="self"))
        workspace = await session.get(Workspace, 1)
        proposal = ChangeProposal(
            message="Change the Action's energy profile",
            workspace_revision=workspace.revision,
            status="pending",
        )
        session.add(proposal)
        await session.flush()
        session.add(
            ProposalChange(
                proposal_id=proposal.id,
                position=0,
                entity="card",
                action="update",
                entity_id=card.id,
                expected_version=card.version,
                values={
                    "categories": ["contribution", "rest"],
                    "energy_types": ["physical", "social"],
                },
            )
        )
        await session.commit()
        proposal_id = proposal.id

    message = FakeMessage(61, bot_message=True)
    await render_proposal(message, services_for(sessions), proposal_id)
    text, markup = message.edits[-1]

    assert "Card overview" in text
    assert "Kind: ⭐️ Action" in text
    assert "Title: <b>Evening walk</b>" in text
    assert "Effort: 3" in text
    assert "Categories: 🌱 Self → ❤️ Contribution, 🔋 Rest" in text
    assert "Energy: — → 💪 Physical, 🤝 Social" in text
    buttons = button_texts(markup)
    assert buttons == ["✅ Save", "🗑 Discard"]
    assert "↩️ Back" not in buttons


async def test_card_check_link_proposal_shows_the_check_in_overview_and_diff(sessions) -> None:
    async with sessions() as session:
        card = await create_card(session, kind="goal", title="Be healthy")
        check = await create_check(session, title="Walk upright")
        workspace = await session.get(Workspace, 1)
        proposal = ChangeProposal(
            message="Link the Check to the Goal",
            workspace_revision=workspace.revision,
            status="pending",
        )
        session.add(proposal)
        await session.flush()
        session.add(
            ProposalChange(
                proposal_id=proposal.id,
                position=0,
                entity="card",
                action="link",
                entity_id=card.id,
                expected_version=card.version,
                values={"check_ids": [check.id]},
            )
        )
        await session.commit()
        proposal_id = proposal.id

    message = FakeMessage(64, bot_message=True)
    await render_proposal(message, services_for(sessions), proposal_id)
    text, _ = message.edits[-1]

    assert "Checks: Walk upright" in text
    assert "• Checks: — → Walk upright" in text


async def test_diary_proposal_shows_the_entry_itself_and_only_save_or_discard(
    sessions,
) -> None:
    async with sessions() as session:
        workspace = await session.get(Workspace, 1)
        proposal = ChangeProposal(
            message="Save today's Diary entry",
            workspace_revision=workspace.revision,
            status="pending",
        )
        session.add(proposal)
        await session.flush()
        session.add(
            ProposalChange(
                proposal_id=proposal.id,
                position=0,
                entity="diary",
                action="update",
                entity_id=7,
                expected_version=1,
                values={
                    "stamp": "abc123",
                    "entry_date": "2026-08-15",
                    "body": "Сходил на рынок, вечером стало легче.",
                    "feeling_score": 6,
                    "remark": "A day that ended better than it began.",
                },
            )
        )
        await session.commit()
        proposal_id = proposal.id

    message = FakeMessage(65, bot_message=True)
    await render_proposal(message, services_for(sessions), proposal_id)
    text, markup = message.edits[-1]

    assert "<b>Edit Diary entry · AI proposal</b>" in text
    assert "Date: 2026-08-15" in text
    assert "Feeling: 6 🙂" in text
    assert "This replaces the entry already saved for that day." in text
    assert "Сходил на рынок, вечером стало легче." in text
    assert "<i>A day that ended better than it began.</i>" in text
    # The screen is the entry; a field diff would only repeat it, and the stamp is
    # bookkeeping the owner never has to read.
    assert "<b>Proposed changes</b>" not in text
    assert "abc123" not in text
    assert button_texts(markup) == ["✅ Save", "🗑 Discard"]


async def test_diary_removal_shows_the_entry_it_would_delete(sessions) -> None:
    async with sessions() as session:
        entry = await create_diary_entry(
            session, entry_date=date(2026, 8, 14), body="День, который уходит."
        )
        workspace = await session.get(Workspace, 1)
        proposal = ChangeProposal(
            message="Remove that day's Diary entry",
            workspace_revision=workspace.revision,
            status="pending",
        )
        session.add(proposal)
        await session.flush()
        session.add(
            ProposalChange(
                proposal_id=proposal.id,
                position=0,
                entity="diary",
                action="delete",
                entity_id=entry.id,
                expected_version=entry.version,
                values={"stamp": "abc123", "entry_date": "2026-08-14", "body": "", "remark": ""},
            )
        )
        await session.commit()
        proposal_id = proposal.id

    message = FakeMessage(66, bot_message=True)
    await render_proposal(message, services_for(sessions), proposal_id)
    text, markup = message.edits[-1]

    assert "<b>Remove Diary entry · AI proposal</b>" in text
    assert "This removes that day's entry for good." in text
    # The owner reads what is about to go, not an empty replacement.
    assert "День, который уходит." in text
    assert button_texts(markup) == ["✅ Save", "🗑 Discard"]


async def test_card_creation_proposal_has_no_proposed_changes_section(sessions) -> None:
    async with sessions() as session:
        workspace = await session.get(Workspace, 1)
        proposal = ChangeProposal(
            message="Create a walking Action",
            workspace_revision=workspace.revision,
            status="pending",
        )
        session.add(proposal)
        await session.flush()
        session.add(
            ProposalChange(
                proposal_id=proposal.id,
                position=0,
                entity="card",
                action="create",
                values={
                    "kind": "action",
                    "title": "Evening walk",
                    "effort_points": 3,
                    "categories": ["self"],
                },
            )
        )
        await session.commit()
        proposal_id = proposal.id

    message = FakeMessage(62, bot_message=True)
    await render_proposal(message, services_for(sessions), proposal_id)
    text, markup = message.edits[-1]

    assert "Card overview" in text
    assert "Kind: ⭐️ Action" in text
    assert "Title: <b>Evening walk</b>" in text
    assert "Categories: 🌱 Self" in text
    assert "<b>Proposed changes</b>" not in text
    assert button_texts(markup) == ["✅ Save", "🗑 Discard"]


async def test_move_proposal_exposes_only_stage_control(sessions) -> None:
    async with sessions() as session:
        card = Card(kind="action", title="Evening walk", effort_points=3)
        session.add(card)
        await session.flush()
        workspace = await session.get(Workspace, 1)
        proposal = ChangeProposal(
            message="Move the Action to Today",
            workspace_revision=workspace.revision,
            status="pending",
        )
        session.add(proposal)
        await session.flush()
        session.add(
            ProposalChange(
                proposal_id=proposal.id,
                position=0,
                entity="card",
                action="move",
                entity_id=card.id,
                expected_version=card.version,
                values={"stage": "today"},
            )
        )
        await session.commit()
        proposal_id = proposal.id

    message = FakeMessage(63, bot_message=True)
    await render_proposal(message, services_for(sessions), proposal_id)
    text, markup = message.edits[-1]

    assert "Stage: Backlog → Today" in text
    assert button_texts(markup) == ["✅ Save", "🗑 Discard"]


async def test_saving_card_proposal_applies_every_editable_field(sessions) -> None:
    async with sessions() as session:
        parent = Card(kind="goal", title="Be healthy")
        card = Card(kind="action", title="Walk", effort_points=2)
        value = Value(name="Health")
        tag = Tag(name="Outside")
        session.add_all([parent, card, value, tag])
        await session.flush()
        session.add_all(
            [
                CardCategory(card_id=card.id, category="self"),
                CardEnergyType(card_id=card.id, energy_type="cognitive"),
            ]
        )
        workspace = await session.get(Workspace, 1)
        proposal = ChangeProposal(
            message="Update the Action",
            workspace_revision=workspace.revision,
            status="pending",
        )
        session.add(proposal)
        await session.flush()
        session.add(
            ProposalChange(
                proposal_id=proposal.id,
                position=0,
                entity="card",
                action="update",
                entity_id=card.id,
                expected_version=card.version,
                values={
                    "priority": "critical",
                    "hard_time": True,
                    "blocked": True,
                    "blocked_description": "Waiting for access",
                    "effort_points": 5,
                    "parent_id": parent.id,
                    "categories": ["rest", "work"],
                    "energy_types": ["physical", "social"],
                    "value_ids": [value.id],
                    "tag_ids": [tag.id],
                },
            )
        )
        await session.commit()
        proposal_id = proposal.id
        card_id = card.id

    async with sessions() as session:
        affected = await ProposalService(session).apply(proposal_id)
        await session.commit()

    assert affected == [card_id]
    async with sessions() as session:
        card = await session.get(Card, card_id)
        assert (
            card.priority,
            card.hard_time,
            card.blocked,
            card.blocked_description,
            card.effort_points,
            card.parent_id,
        ) == (
            "critical",
            True,
            True,
            "Waiting for access",
            5,
            parent.id,
        )
        assert set(
            await session.scalars(
                select(CardCategory.category).where(CardCategory.card_id == card_id)
            )
        ) == {"rest", "work"}
        assert set(
            await session.scalars(
                select(CardEnergyType.energy_type).where(CardEnergyType.card_id == card_id)
            )
        ) == {"physical", "social"}
        assert set(
            await session.scalars(select(CardValue.value_id).where(CardValue.card_id == card_id))
        ) == {value.id}
        assert set(
            await session.scalars(select(CardTag.tag_id).where(CardTag.card_id == card_id))
        ) == {tag.id}


async def test_the_reminders_screen_and_settings_hide_safwas_own_reminder(sessions) -> None:
    """The owner sets the Diary in Settings; the Reminder behind it is not theirs to see."""
    async with sessions() as session:
        await update_profile(session, diary_time=time(22, 0))
        await create_reminder(
            session,
            instruction="Check my posture.",
            schedule=resolve(interval_minutes=120, now=datetime.now(UTC), tz=ZoneInfo("UTC")),
            tz=ZoneInfo("UTC"),
        )
        await session.commit()

    listing = FakeMessage(910, bot_message=True)
    await render_reminders(listing, services_for(sessions))
    settings = FakeMessage(911, bot_message=True)
    await command_settings(settings, services_for(sessions))

    labels = button_texts(listing.edits[-1][1])
    assert any("Check my posture" in label for label in labels)
    assert not any(DIARY_REMINDER_INSTRUCTION[:20] in label for label in labels)
    assert "Diary: 22:00" in settings.edits[-1][0]


@pytest.mark.parametrize(
    ("label", "typed", "field", "expected", "shown"),
    [
        ("👤 About me", "I prefer mornings.", "about_me", "I prefer mornings.", "About me: I prefer mornings."),
        ("🧭 Advisor instructions", "Keep plans concise.", "advisor_instructions", "Keep plans concise.", "Advisor instructions: Keep plans concise."),
        ("📔 Diary time", "07:15", "diary_time", time(7, 15), "Diary: 07:15"),
        ("📔 Diary time", "off", "diary_time", None, "Diary: off"),
        ("🧠 Memory sync", "03:00", "memory_update_time", time(3, 0), "Memory sync: 03:00"),
        ("🎯 Sprint capacity", "21", "capacity_effort_points", 21, "Sprint capacity: 21 EP"),
        ("✍️ Diary instruction", "Спроси про сон.", "diary_instructions", "Спроси про сон.",
         "Diary instruction: Спроси про сон."),
        ("🏁 Sprint length", "21", "sprint_length_days", 21, "Sprint length: 21 days"),
    ],
)
async def test_every_settings_value_is_edited_from_its_own_button(
    sessions, label, typed, field, expected, shown
) -> None:
    services = services_for(sessions)
    message = FakeMessage(920, bot_message=True, answer_as_new=True)
    await command_settings(message, services)

    button = next(
        item for row in message.edits[-1][1].inline_keyboard for item in row if item.text == label
    )
    await callback_token_handler(
        FakeCallback(button.callback_data.split(":", 1)[1], message), services
    )
    async with sessions() as session:
        ui = await session.scalar(select(UiSession))
        assert (ui.kind, ui.state["field"]) == ("text_input", field)
        assert "Current value:\n<pre>" in message.bot.edits[-1][1]

    answer = FakeMessage(921, text=typed, bot_message=False, bot=message.bot)
    await ordinary_text(answer, services)

    async with sessions() as session:
        assert getattr(await session.get(UserProfile, 1), field) == expected
    assert shown in message.bot.edits[-1][1]


async def test_a_rejected_settings_value_reopens_its_own_prompt(sessions) -> None:
    services = services_for(sessions)
    message = FakeMessage(930, bot_message=True, answer_as_new=True)
    await command_settings(message, services)
    button = next(
        item
        for row in message.edits[-1][1].inline_keyboard
        for item in row
        if item.text == "📔 Diary time"
    )
    await callback_token_handler(
        FakeCallback(button.callback_data.split(":", 1)[1], message), services
    )

    answer = FakeMessage(931, text="tomorrow", bot_message=False, bot=message.bot)
    await ordinary_text(answer, services)

    assert answer.was_deleted is True
    assert "Send a time as HH:MM" in message.bot.edits[-1][1]
    async with sessions() as session:
        assert (await session.get(UserProfile, 1)).diary_time == time.fromisoformat(
            DIARY_TIME_DEFAULT
        )
        assert (await session.scalar(select(UiSession))).kind == "text_input"


class ScriptedTranscriber:
    """The ASR network boundary: one canned transcript, or one failure."""

    def __init__(
        self, text: str = "", error: str = "", progress_at: tuple[float, ...] = ()
    ) -> None:
        self.text = text
        self.error = error
        self.progress_at = progress_at
        self.clips: list[object] = []

    async def transcribe(self, clip, *, progress=None):
        self.clips.append(clip)
        for done in self.progress_at:
            if progress is not None:
                await progress(done, clip.duration_seconds)
        if self.error:
            raise TranscriptionError(self.error)
        return TranscriptionResult(text=self.text, elapsed_seconds=0.1)

    async def close(self) -> None:
        return None


def voice_message_for(
    message_id: int, *, duration: int = 12, file_size: int = 4_096
) -> FakeMessage:
    return FakeMessage(
        message_id,
        bot_message=False,
        answer_as_new=True,
        voice=SimpleNamespace(
            file_id=f"voice-{message_id}",
            duration=duration,
            file_size=file_size,
            mime_type="audio/ogg",
        ),
    )


def capture_dialogue_turns(monkeypatch) -> list[tuple[str, object]]:
    """Stop at the handler's edge: the advisor loop itself is the text path's test."""
    import safwa.telegram.dialogue as dialogue_module

    turns: list[tuple[str, object]] = []

    async def fake_turn(_message, _services, request, source):
        turns.append((request, source))

    monkeypatch.setattr(dialogue_module, "run_dialogue_turn", fake_turn)
    return turns


async def test_voice_message_becomes_one_owner_dialogue_turn(sessions, monkeypatch) -> None:
    turns = capture_dialogue_turns(monkeypatch)
    transcriber = ScriptedTranscriber("Renew the passport this week.")
    services = services_for(sessions, transcriber=transcriber)
    message = voice_message_for(940)

    await voice_message(message, services)

    assert message.bot.downloads == ["voice-940"]
    assert transcriber.clips[0].filename == "voice.ogg"
    assert transcriber.clips[0].duration_seconds == 12.0
    posted = message.sent_messages
    assert len(posted) == 1
    assert "Renew the passport this week." in posted[0].text
    async with sessions() as session:
        rows = list(await session.scalars(select(TelegramMessage)))
    dialogue_rows = [row for row in rows if row.kind == MessageKind.DIALOGUE_USER.value]
    assert [row.direction for row in dialogue_rows] == ["out"]
    assert turns == [("Renew the passport this week.", turns[0][1])]
    assert turns[0][1].role == "user"
    assert turns[0][1].message_id == posted[0].message_id


async def test_long_transcript_is_split_and_answered_once(sessions, monkeypatch) -> None:
    turns = capture_dialogue_turns(monkeypatch)
    transcript = " ".join(f"word{index}" for index in range(1_200))
    services = services_for(sessions, transcriber=ScriptedTranscriber(transcript))
    message = voice_message_for(941, duration=600)

    await voice_message(message, services)

    assert len(message.sent_messages) > 1
    assert "User Name Surname:" in message.sent_messages[0].text
    assert all("User Name Surname:" not in item.text for item in message.sent_messages[1:])
    async with sessions() as session:
        rows = list(await session.scalars(select(TelegramMessage)))
    dialogue_rows = [row for row in rows if row.kind == MessageKind.DIALOGUE_USER.value]
    assert len(dialogue_rows) == len(message.sent_messages)
    assert len(turns) == 1
    assert turns[0][0] == transcript


async def test_decode_progress_is_shown_then_removed(sessions, monkeypatch) -> None:
    capture_dialogue_turns(monkeypatch)
    transcriber = ScriptedTranscriber("Done at last.", progress_at=(30.0, 90.0))
    services = services_for(sessions, transcriber=transcriber)
    message = voice_message_for(946, duration=120)

    await voice_message(message, services)

    status = message.sent_messages[0]
    assert "Transcribing" in status.text
    assert "25%" in status.text
    assert [text for _id, text, _markup in message.bot.edits if "75%" in text]
    assert status.message_id in message.bot.deleted
    assert "Done at last." in message.sent_messages[-1].text
    async with sessions() as session:
        rows = list(await session.scalars(select(TelegramMessage)))
    # The percentage is transient: it leaves no row behind and never becomes dialogue.
    assert all(row.kind != MessageKind.STATUS.value for row in rows)


async def test_voice_message_without_a_transcriber_explains_itself(sessions) -> None:
    services = services_for(sessions, transcriber=None)
    message = voice_message_for(942)

    await voice_message(message, services)

    assert "SAFWA_ASR_PROVIDER" in message.answers[-1]
    assert message.bot.downloads == []


async def test_failed_transcription_reports_and_changes_nothing(sessions, monkeypatch) -> None:
    turns = capture_dialogue_turns(monkeypatch)
    services = services_for(
        sessions, transcriber=ScriptedTranscriber(error="upstream refused the file")
    )
    message = voice_message_for(943)

    await voice_message(message, services)

    assert "could not transcribe" in message.answers[-1]
    assert "upstream refused the file" in message.answers[-1]
    assert turns == []
    async with sessions() as session:
        rows = list(await session.scalars(select(TelegramMessage)))
    assert all(row.kind != MessageKind.DIALOGUE_USER.value for row in rows)


async def test_overlong_recording_is_refused_before_download(sessions, monkeypatch) -> None:
    turns = capture_dialogue_turns(monkeypatch)
    transcriber = ScriptedTranscriber("never reached")
    services = services_for(sessions, transcriber=transcriber)
    message = voice_message_for(944, duration=ASR_MAX_DURATION_SECONDS + 1)

    await voice_message(message, services)

    assert message.bot.downloads == []
    assert transcriber.clips == []
    assert turns == []
    assert "transcribes up to" in message.answers[-1]


async def test_voice_arriving_during_generation_is_queued(sessions, monkeypatch) -> None:
    turns = capture_dialogue_turns(monkeypatch)
    services = services_for(sessions, transcriber=ScriptedTranscriber("And one more thing."))
    await services.guard.acquire(900, queue_messages=True)
    message = voice_message_for(945)

    await voice_message(message, services)

    assert turns == []
    assert message.was_deleted is False
    assert "Queued: And one more thing." in message.answers[-1]
    assert [item.text for item in await services.guard.drain_queue()] == ["And one more thing."]


async def test_a_queued_transcript_is_previewed_and_split_on_drain(sessions) -> None:
    transcript = " ".join(f"word{index}" for index in range(1_200))
    services = services_for(sessions, transcriber=ScriptedTranscriber(transcript))
    await services.guard.acquire(950, queue_messages=True)
    message = voice_message_for(951)

    await voice_message(message, services)

    placeholder = message.answers[-1]
    assert len(placeholder) < TELEGRAM_TEXT_LIMIT
    assert "…" in placeholder
    assert "word1199" not in placeholder

    drain_target = FakeMessage(952, bot_message=False, answer_as_new=True, bot=message.bot)
    sent, dialogue_text = await materialize_queued_dialogue(drain_target, services)

    assert len(drain_target.sent_messages) > 1
    # TELEGRAM_TEXT_LIMIT budgets the body; the name prefix and the invisible kind mark
    # ride on top of it and still have to fit Telegram's own 4096.
    assert all(len(item.text) <= 4_096 for item in drain_target.sent_messages)
    assert sent is drain_target.sent_messages[-1]
    assert transcript in dialogue_text


async def test_a_transcript_keeps_its_turn_when_the_lease_changed_hands(
    sessions, monkeypatch
) -> None:
    """A decode is long enough for a background generation to have taken the lease."""
    turns = capture_dialogue_turns(monkeypatch)
    services = services_for(sessions, transcriber=ScriptedTranscriber("Plan my week."))
    message = voice_message_for(953)
    assert services.guard.reserve_background()

    await voice_message(message, services)

    assert [request for request, _source in turns] == ["Plan my week."]
    assert services.guard.background is False


class TurnAdvisor:
    """One answer, produced by a turn that saved a change the way autoapproval does."""

    def __init__(self, sessions) -> None:
        self.sessions = sessions

    async def handle(self, request, *, source_message_id=None, dialogue=None):
        del request, source_message_id, dialogue
        async with self.sessions() as session:
            await set_sprint_success_criteria(session, "Ship the release")
            await session.commit()
        return AIOutcome("answer", "⚡ Auto-saved the proposed change.")


def turn_services(sessions):
    services = services_for(sessions, advisor=None)
    services.advisor = TurnAdvisor(sessions)
    services.history = SimpleNamespace(dialogue=_empty_dialogue)
    services.continuity = SimpleNamespace(maybe_summarize=_no_summary)
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


async def test_a_cancelled_turn_is_not_rendered(sessions) -> None:
    services = turn_services(sessions)
    original_handle = services.advisor.handle

    async def cancel_then_answer(request, *, source_message_id=None, dialogue=None):
        services.guard.cancel()
        return await original_handle(
            request, source_message_id=source_message_id, dialogue=dialogue
        )

    services.advisor.handle = cancel_then_answer
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

    assert message.sent_messages == []


async def test_the_owner_turn_is_headed_by_the_telegram_name(sessions, monkeypatch) -> None:
    capture_dialogue_turns(monkeypatch)
    services = services_for(sessions, transcriber=ScriptedTranscriber("Plan my week."))
    message = voice_message_for(962)

    await voice_message(message, services)

    assert "<b>User Name Surname:</b>" in message.sent_messages[0].text


async def test_a_turn_with_no_owner_message_is_headed_by_the_bare_role(sessions) -> None:
    """A Reminder anchor carries no real `from_user`, so only the role is left."""
    services = services_for(sessions)
    services.guard.finish_queue(services.guard.begin_queue("Later, then."), None)
    anchor = FakeMessage(964, bot_message=True, answer_as_new=True)
    anchor.from_user = SimpleNamespace(id=1, is_bot=True, full_name="Safwa")

    _sent, dialogue_text = await materialize_queued_dialogue(anchor, services)

    assert dialogue_text.startswith("User:")
