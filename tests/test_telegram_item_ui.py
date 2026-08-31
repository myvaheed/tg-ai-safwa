from __future__ import annotations

import ast
import asyncio
import html
import re
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import get_args
from uuid import uuid4

import pytest
from aiogram import Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from sqlalchemy import select
from ui_harness import (
    CALLBACK_ACTIONS,
    FakeBot,
    FakeMessage,
    StubAdvisor,
    button_texts,
    services_for,
    ui_sources,
)

from safwa.ai.contracts import OpenInput
from safwa.ai.outcome import AIOutcome, AIOutcomeKind
from safwa.ai.sql import create_ai_views
from safwa.bootstrap.modules import (
    AI_VIEWS,
    ALLOWED_VIEWS,
    FEATURE_COMMANDS,
    FEATURE_TEXT_INPUTS,
    PROPOSALS,
    SCREENS,
)
from safwa.constants import (
    ASR_MAX_DURATION_SECONDS,
)
from safwa.domain import (
    DomainError,
    create_card,
    create_check,
    create_tag,
    create_value,
    delete_tag,
    set_sprint_success_criteria,
)
from safwa.enums import MessageKind
from safwa.features.continuity.model import SUMMARY_HEADER, SummaryState
from safwa.features.diary.use_cases import create_diary_entry
from safwa.features.planning.telegram import render_sprint
from safwa.features.proposals.model import ChangeAction, ProposalChange
from safwa.features.proposals.store import ProposalStore
from safwa.features.proposals.use_cases import approve_proposal
from safwa.features.saved_requests.use_cases import (
    create_saved_request,
)
from safwa.history import MARKS, HistoryEntry, TelegramNotes, read_kind_mark
from safwa.models import (
    CallbackToken,
    Card,
    CardCategory,
    CardEnergyType,
    CardTag,
    CardValue,
    Tag,
    TelegramMessage,
    Value,
    Workspace,
)
from safwa.shell import (
    OwnerAndWritingMiddleware,
    dismiss_prior_ui,
    open_item_screen,
    render_citations,
)
from safwa.shell.chat import (
    TURN_NOTICE,
    discard_stale_status,
    edit_registered_message,
    remove_turn_notice,
    send_owner_turn,
    send_registered,
)
from safwa.shell.layout import menu_markup, menu_row, start_payload
from safwa.telegram import (
    SHELL_COMMANDS,
    ordinary_text,
    render_ai_outcome,
    render_proposal,
    voice_message,
)
from safwa.telegram.commands import register_commands
from safwa.telegram.dialogue import run_dialogue_turn
from safwa.turn import TurnManager
from telegram_llm import (
    TELEGRAM_TEXT_LIMIT,
    ChatHost,
    DialogueMessage,
    TranscriptionError,
    TranscriptionResult,
    split_telegram_text,
)


def _telegram_module_trees() -> list[ast.Module]:
    return [ast.parse(path.read_text(encoding="utf-8")) for path in ui_sources()]


def _callback_action_groups(trees: list[ast.Module]) -> list[ast.AST]:
    """Every per-feature group of inline-button actions, as the source declares them."""
    groups = [
        node
        for tree in trees
        for node in ast.walk(tree)
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id.endswith("_CALLBACK_ACTIONS")
        and isinstance(node.value, ast.Dict)
    ]
    if not groups:
        raise AssertionError("no callback action group was found in any telegram submodule")
    return groups


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
    groups = _callback_action_groups(trees)
    registry_nodes = {id(node) for group in groups for node in ast.walk(group)}
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
        for group in groups
        for key in group.value.keys
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }

    assert spelled_out, "no literal registry keys were found to check"
    assert spelled_out <= referenced


def test_every_recorded_text_input_flow_has_a_declared_handler() -> None:
    """A flow no feature declares is an editor that swallows what the owner types."""
    recorded: set[str] = set()
    for tree in _telegram_module_trees():
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                recorded |= {
                    value.value
                    for key, value in zip(node.keys, node.values, strict=True)
                    if isinstance(key, ast.Constant)
                    and key.value == "flow"
                    and isinstance(value, ast.Constant)
                    and isinstance(value.value, str)
                }
            elif (
                isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                recorded |= {
                    node.value.value
                    for target in node.targets
                    if isinstance(target, ast.Subscript)
                    and isinstance(target.slice, ast.Constant)
                    and target.slice.value == "flow"
                }

    assert recorded, "no literal text input flow was found to check"
    assert recorded <= set(FEATURE_TEXT_INPUTS)


async def test_every_command_is_deleted_and_still_dispatched(sessions, monkeypatch) -> None:
    import safwa.shell.services as core_module

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


def test_every_declared_command_is_bound_to_its_command_line() -> None:
    """Binding left import time with the catalogue, so something has to check it."""
    commands = (*SHELL_COMMANDS, *FEATURE_COMMANDS)
    target = Router(name="test-commands")
    register_commands(target, commands)
    bound = {
        argument
        for handler in target.message.handlers
        for filter_ in handler.filters or ()
        if isinstance(filter_.callback, Command)
        for argument in filter_.callback.commands
    }

    assert bound == {screen.command for screen in commands if screen.command is not None}


def test_every_menu_button_reaches_a_declared_screen() -> None:
    """The menu named its handlers in a dict of its own until the features declared them."""
    rows = [*menu_markup(sprint_active=True).inline_keyboard, menu_row()]
    pressed = {button.callback_data.split(":", 1)[1] for row in rows for button in row}

    assert pressed <= {
        screen.nav for screen in (*SHELL_COMMANDS, *FEATURE_COMMANDS) if screen.nav is not None
    }


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
        chat=ChatHost(TelegramNotes(sessions), MARKS),
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
        chat=ChatHost(TelegramNotes(sessions), MARKS),
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


async def test_open_item_screen_renders_the_manual_screen_of_every_item(sessions) -> None:
    async with sessions() as session:
        await (await session.connection()).run_sync(
            lambda connection: create_ai_views(connection, AI_VIEWS)
        )
        card = await create_card(session, kind="action", title="Pull-ups", effort_points=1)
        value = await create_value(session, "Health")
        tag = await create_tag(session, "Training")
        check = await create_check(session, title="Form is safe")
        request = await create_saved_request(
            session, "Open actions", "SELECT id FROM ai_cards WHERE kind = 'action'",
            views=ALLOWED_VIEWS,
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
        await delete_tag(session, tag.id)
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
    # A deleted item leaves its words and loses its link, and an unknown type is not a citation.
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
        await (await session.connection()).run_sync(
            lambda connection: create_ai_views(connection, AI_VIEWS)
        )
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
            session, "План на неделю", "SELECT id FROM ai_cards WHERE kind = 'action'",
            views=ALLOWED_VIEWS,
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
            AIOutcomeKind.ANSWER,
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


def test_the_screen_catalogue_is_the_one_list_of_openable_items() -> None:
    """The features publish what can be opened; only the `open` tool's enum is by hand."""
    assert set(SCREENS.types) == {"card", "check", "tag", "value", "request", "diary", "retro"}
    # A tool's enum is prompt text and stays in ai/contracts.py, so it has to agree here.
    literal = set(get_args(OpenInput.model_fields["item_type"].annotation))
    assert literal == {name for name, spec in SCREENS.by_type.items() if spec.ai_openable}


def test_start_payload_reads_only_a_command_line() -> None:
    assert start_payload("/start card-12") == "card-12"
    assert start_payload("/start@safwa_ai_bot card-12") == "card-12"
    assert start_payload("/start") is None
    # The menu's Home button hands command_start the bot's own screen, never a command.
    assert start_payload("<b>Card</b>: Pull ups") is None
    assert SCREENS.parse_payload("check-14") == ("check", 14)
    assert SCREENS.parse_payload("sprint-1") is None


async def test_item_proposal_shows_diffs_and_only_save_discard_footer(sessions) -> None:
    store = ProposalStore()
    async with sessions() as session:
        tag = Tag(name="Family", description="Old description")
        session.add(tag)
        await session.flush()
        workspace = await session.get(Workspace, 1)
        proposal = store.open_proposal(
            message="Improve the Family Tag",
            workspace_revision=workspace.revision,
            changes=[
                ProposalChange(
                    entity="tag",
                    action=ChangeAction.UPDATE,
                    entity_id=tag.id,
                    expected_version=tag.version,
                    values={"description": "Relationships and home"},
                )
            ],
        )
        await session.commit()
        proposal_id = proposal.id

    message = FakeMessage(60, bot_message=True)
    await render_proposal(message, services_for(sessions, reviews=store), proposal_id)
    text, markup = message.edits[-1]
    assert "Old description" in text
    assert "Relationships and home" in text
    assert "→" in text
    buttons = button_texts(markup)
    assert buttons == ["✅ Save", "🗑 Discard"]
    assert "↩️ Back" not in buttons


async def test_card_proposal_uses_full_card_editor_with_human_diffs(sessions) -> None:
    store = ProposalStore()
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
        proposal = store.open_proposal(
            message="Change the Action's energy profile",
            workspace_revision=workspace.revision,
            changes=[
                ProposalChange(
                    entity="card",
                    action=ChangeAction.UPDATE,
                    entity_id=card.id,
                    expected_version=card.version,
                    values={
                    "categories": ["contribution", "rest"],
                    "energy_types": ["physical", "social"],
                    },
                )
            ],
        )
        await session.commit()
        proposal_id = proposal.id

    message = FakeMessage(61, bot_message=True)
    await render_proposal(message, services_for(sessions, reviews=store), proposal_id)
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
    store = ProposalStore()
    async with sessions() as session:
        card = await create_card(session, kind="goal", title="Be healthy")
        check = await create_check(session, title="Walk upright")
        workspace = await session.get(Workspace, 1)
        proposal = store.open_proposal(
            message="Link the Check to the Goal",
            workspace_revision=workspace.revision,
            changes=[
                ProposalChange(
                    entity="card",
                    action=ChangeAction.LINK,
                    entity_id=card.id,
                    expected_version=card.version,
                    values={"check_ids": [check.id]},
                )
            ],
        )
        await session.commit()
        proposal_id = proposal.id

    message = FakeMessage(64, bot_message=True)
    await render_proposal(message, services_for(sessions, reviews=store), proposal_id)
    text, _ = message.edits[-1]

    assert "Checks: Walk upright" in text
    assert "• Checks: — → Walk upright" in text


async def test_diary_proposal_shows_the_entry_itself_and_only_save_or_discard(
    sessions,
) -> None:
    store = ProposalStore()
    async with sessions() as session:
        workspace = await session.get(Workspace, 1)
        proposal = store.open_proposal(
            message="Save today's Diary entry",
            workspace_revision=workspace.revision,
            changes=[
                ProposalChange(
                    entity="diary",
                    action=ChangeAction.UPDATE,
                    entity_id=7,
                    expected_version=1,
                    values={
                    "entry_date": "2026-08-15",
                    "body": "Сходил на рынок, вечером стало легче.",
                    "feeling_score": 6,
                    "remark": "A day that ended better than it began.",
                    },
                )
            ],
        )
        await session.commit()
        proposal_id = proposal.id

    message = FakeMessage(65, bot_message=True)
    await render_proposal(message, services_for(sessions, reviews=store), proposal_id)
    text, markup = message.edits[-1]

    assert "<b>Edit Diary entry · AI proposal</b>" in text
    assert "Date: 2026-08-15" in text
    assert "Feeling: 6 🙂" in text
    assert "This replaces the entry already saved for that day." in text
    assert "Сходил на рынок, вечером стало легче." in text
    assert "<i>A day that ended better than it began.</i>" in text
    # The screen is the day in the owner's voice plus Safwa's one line about it; a field
    # diff would only repeat the entry back at them.
    assert "<b>Proposed changes</b>" not in text
    assert button_texts(markup) == ["✅ Save", "🗑 Discard"]


async def test_diary_removal_shows_the_entry_it_would_delete(sessions) -> None:
    store = ProposalStore()
    async with sessions() as session:
        entry = await create_diary_entry(
            session, entry_date=date(2026, 8, 14), body="День, который уходит."
        )
        workspace = await session.get(Workspace, 1)
        proposal = store.open_proposal(
            message="Remove that day's Diary entry",
            workspace_revision=workspace.revision,
            changes=[
                ProposalChange(
                    entity="diary",
                    action=ChangeAction.DELETE,
                    entity_id=entry.id,
                    expected_version=entry.version,
                    values={"stamp": "abc123", "entry_date": "2026-08-14", "body": "", "remark": ""},
                )
            ],
        )
        await session.commit()
        proposal_id = proposal.id

    message = FakeMessage(66, bot_message=True)
    await render_proposal(message, services_for(sessions, reviews=store), proposal_id)
    text, markup = message.edits[-1]

    assert "<b>Remove Diary entry · AI proposal</b>" in text
    assert "This removes that day's entry for good." in text
    # The owner reads what is about to go, not an empty replacement.
    assert "День, который уходит." in text
    assert button_texts(markup) == ["✅ Save", "🗑 Discard"]


async def test_card_creation_proposal_has_no_proposed_changes_section(sessions) -> None:
    store = ProposalStore()
    async with sessions() as session:
        workspace = await session.get(Workspace, 1)
        proposal = store.open_proposal(
            message="Create a walking Action",
            workspace_revision=workspace.revision,
            changes=[
                ProposalChange(
                    entity="card",
                    action=ChangeAction.CREATE,
                    values={
                    "kind": "action",
                    "title": "Evening walk",
                    "effort_points": 3,
                    "categories": ["self"],
                    },
                )
            ],
        )
        await session.commit()
        proposal_id = proposal.id

    message = FakeMessage(62, bot_message=True)
    await render_proposal(message, services_for(sessions, reviews=store), proposal_id)
    text, markup = message.edits[-1]

    assert "Card overview" in text
    assert "Kind: ⭐️ Action" in text
    assert "Title: <b>Evening walk</b>" in text
    assert "Categories: 🌱 Self" in text
    assert "<b>Proposed changes</b>" not in text
    assert button_texts(markup) == ["✅ Save", "🗑 Discard"]


async def test_move_proposal_exposes_only_stage_control(sessions) -> None:
    store = ProposalStore()
    async with sessions() as session:
        card = Card(kind="action", title="Evening walk", effort_points=3)
        session.add(card)
        await session.flush()
        workspace = await session.get(Workspace, 1)
        proposal = store.open_proposal(
            message="Move the Action to Today",
            workspace_revision=workspace.revision,
            changes=[
                ProposalChange(
                    entity="card",
                    action=ChangeAction.MOVE,
                    entity_id=card.id,
                    expected_version=card.version,
                    values={"stage": "today"},
                )
            ],
        )
        await session.commit()
        proposal_id = proposal.id

    message = FakeMessage(63, bot_message=True)
    await render_proposal(message, services_for(sessions, reviews=store), proposal_id)
    text, markup = message.edits[-1]

    assert "Stage: Backlog → Today" in text
    assert button_texts(markup) == ["✅ Save", "🗑 Discard"]


async def test_saving_card_proposal_applies_every_editable_field(sessions) -> None:
    store = ProposalStore()
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
        proposal = store.open_proposal(
            message="Update the Action",
            workspace_revision=workspace.revision,
            changes=[
                ProposalChange(
                    entity="card",
                    action=ChangeAction.UPDATE,
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
            ],
        )
        await session.commit()
        proposal_id = proposal.id
        card_id = card.id

    async with sessions() as session:
        affected = await approve_proposal(session, store, PROPOSALS, proposal_id)
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
    """TG-RELAY-004 — tests/brd/telegram_history.feature"""
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
    original_handle = services.advisor.handle

    async def cancel_then_answer(request, *, source_message_id=None, dialogue=None):
        await remove_turn_notice(message, services, services.turn.cancel())
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
    services.advisor = ProposalAdvisor()
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
    import safwa.telegram.dialogue as dialogue_module

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
    import safwa.shell.chat as messaging

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
    services = services_for(sessions, reviews=store, advisor=StubAdvisor(store))
    message = FakeMessage(53, text="Carry on", bot_message=False, bot=bot)

    await dismiss_prior_ui(message, services)

    assert bot.deleted == [50]


def test_a_split_falls_on_a_line_break_and_closes_what_it_opened() -> None:
    """SC-SPLIT-004 — tests/brd/screens.feature"""
    body = "\n".join(f"line {index} of the answer" for index in range(400))
    text = f"<b>Heading</b>\n<i>{body}</i>"

    parts = split_telegram_text(text)

    assert len(parts) > 1
    assert all(len(part) <= TELEGRAM_TEXT_LIMIT for part in parts)
    # Nothing is left open across a cut, and the next part opens it again.
    assert all(part.count("<i>") == part.count("</i>") for part in parts)
    assert parts[1].startswith("<i>")
    assert all(part.endswith("</i>") for part in parts[:-1])
    assert all(part.rsplit("<", 1)[0].endswith("answer") for part in parts[:-1])
    # And nothing is lost between them.
    visible = " ".join(re.sub(r"<[^>]+>", "", part) for part in parts)
    assert visible.split() == re.sub(r"<[^>]+>", "", text).split()


async def test_an_over_long_answer_arrives_as_several_dialogue_messages(sessions) -> None:
    """SC-SPLIT-004 — tests/brd/screens.feature"""
    services = services_for(sessions)
    message = FakeMessage(970, bot_message=False, answer_as_new=True)
    answer = " ".join(f"word{index}" for index in range(1_500))

    await render_ai_outcome(message, services, AIOutcome(AIOutcomeKind.ANSWER, answer))

    assert len(message.sent_messages) > 1
    async with sessions() as session:
        rows = list(await session.scalars(select(TelegramMessage)))
    # Every part is registered as dialogue, which is what makes the window read them all
    # and `dialogue()` merge them back into the one answer they were.
    assert [row.kind for row in rows] == [MessageKind.DIALOGUE_ASSISTANT.value] * len(rows)
    assert len(rows) == len(message.sent_messages)


async def test_an_over_long_summary_is_split_and_the_cut_place_is_its_last_part(
    sessions,
) -> None:
    """SC-SPLIT-004 — tests/brd/screens.feature"""
    import safwa.shell.chat as messaging

    services = services_for(sessions)
    message = FakeMessage(971, bot_message=False, answer_as_new=True)
    text = f"{SUMMARY_HEADER}\n" + "\n".join(f"point {index}" for index in range(600))

    await messaging.send_summary(message, services, text, covered_id=500)

    assert len(message.sent_messages) > 1
    async with sessions() as session:
        state = await session.get(SummaryState, 1)
        rows = list(await session.scalars(select(TelegramMessage)))
    assert [row.kind for row in rows] == [MessageKind.SUMMARY.value] * len(rows)
    # The backwards read meets the newest part first, so that is the cut place.
    assert state.summary_message_id == message.sent_messages[-1].message_id
    assert state.covered_message_id == 500


async def test_a_split_cue_is_delivered_only_once_its_last_part_is_in_the_chat(
    sessions,
) -> None:
    """SC-SPLIT-004 — tests/brd/screens.feature"""
    import safwa.shell.chat as messaging

    services = services_for(sessions)
    message = FakeMessage(972, bot_message=False, answer_as_new=True)
    event_id = uuid4().hex

    sent = await messaging.send_prose(
        message,
        services,
        " ".join(f"word{index}" for index in range(1_500)),
        kind=MessageKind.CUE,
        event_id=event_id,
    )

    assert len(message.sent_messages) > 1
    async with sessions() as session:
        carrier = await session.scalar(
            select(TelegramMessage).where(TelegramMessage.event_id == event_id)
        )
    # `CueRuntime.speak` reads this row back to mean "the owner has these words". On the
    # first part it would call a send that failed halfway delivered.
    assert carrier.message_id == sent.message_id == message.sent_messages[-1].message_id


async def test_a_status_message_the_process_died_under_is_swept_at_startup(sessions) -> None:
    services = services_for(sessions)
    screen = FakeMessage(90, bot_message=True, answer_as_new=True)
    await send_registered(screen, services, "Thinking", kind=MessageKind.STATUS, replace=False)
    orphan = screen.sent_messages[-1].message_id

    await discard_stale_status(screen.bot, services, screen.chat.id)

    assert orphan in screen.bot.deleted
    async with sessions() as session:
        assert (
            await session.scalar(
                select(TelegramMessage).where(TelegramMessage.kind == MessageKind.STATUS.value)
            )
            is None
        )


