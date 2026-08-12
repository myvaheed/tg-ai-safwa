from __future__ import annotations

import ast
import importlib
import inspect
import pkgutil
from datetime import UTC, datetime
from types import SimpleNamespace

from sqlalchemy import select

import safwa.telegram as telegram_source
from safwa.ai.context import DialogueMessage
from safwa.ai.service import AIOutcome, ProposalService
from safwa.domain import (
    create_card,
    create_check,
    create_tag,
    create_value,
    finish_action,
    toggle_card_check,
)
from safwa.enums import CardStage, MessageKind
from safwa.models import (
    CallbackToken,
    Card,
    CardCategory,
    CardEnergyType,
    CardTag,
    CardValue,
    ChangeProposal,
    ProposalChange,
    Tag,
    TelegramMessage,
    UiSession,
    Value,
    Workspace,
)
from safwa.telegram import (
    CALLBACK_ACTIONS,
    GenerationGuard,
    OwnerAndWritingMiddleware,
    callback_token_handler,
    dismiss_prior_ui,
    handle_card_creation_chooser,
    ordinary_text,
    render_card,
    render_card_choices,
    render_card_creation,
    render_children,
    render_dashboard,
    render_item_editor,
    render_item_text_prompt,
    render_proposal,
)


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
        self.edits: list[tuple[int, str, object | None]] = []
        self.deleted: list[int] = []
        self.cleared_markup: list[int] = []

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

    async def edit_message_reply_markup(
        self, *, chat_id: int, message_id: int, reply_markup=None
    ) -> None:
        del chat_id, reply_markup
        self.cleared_markup.append(message_id)

    async def send_chat_action(self, chat_id: int, action) -> None:
        del chat_id, action


class FakeMessage:
    def __init__(
        self,
        message_id: int,
        *,
        text: str = "",
        bot_message: bool,
        bot: FakeBot | None = None,
        chat_id: int = 700,
    ) -> None:
        self.message_id = message_id
        self.text = text
        self.bot = bot or FakeBot()
        self.chat = SimpleNamespace(id=chat_id, type="private")
        self.from_user = SimpleNamespace(id=42, is_bot=bot_message)
        self.date = datetime.now(UTC)
        self.edits: list[tuple[str, object | None]] = []
        self.answers: list[str] = []
        self.was_deleted = False

    async def edit_text(self, text: str, *, reply_markup=None, parse_mode=None):
        del parse_mode
        self.edits.append((text, reply_markup))
        return self

    async def answer(self, text: str, *, reply_markup=None, parse_mode=None):
        del parse_mode
        self.answers.append(text)
        del reply_markup
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


def services_for(sessions):
    return SimpleNamespace(sessions=sessions, owner_id=42, guard=GenerationGuard())


def button_texts(markup) -> list[str]:
    return [button.text for row in markup.inline_keyboard for button in row]


async def test_commands_are_deleted_except_newsession(sessions, monkeypatch) -> None:
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

    boundary = FakeMessage(10, text="/newsession Initial request", bot_message=False)
    await middleware(handler, boundary, {"services": services})

    assert boundary.was_deleted is False
    assert handled == [
        "/start",
        "/mem remember this",
        "/cancel",
        "/newsession Initial request",
    ]


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
            assert guard.active is False

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
    await dismiss_prior_ui(incoming, services_for(sessions))

    assert bot.deleted == [9]
    assert bot.edits[0][0] == 10
    assert "Proposal discarded" in bot.edits[0][1]
    assert "name=" in bot.edits[0][1]
    assert "Family" in bot.edits[0][1]
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

    user_input = FakeMessage(
        31,
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
            select(TelegramMessage).where(TelegramMessage.message_id == 31)
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
                kind="card_create_text",
                state={
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
                    "message_id": 40,
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
    # The Card screen always offers it, because it is the only place a first Check can
    # be added; Tags and Values no longer classify Checks at all.
    message = FakeMessage(card_id, bot_message=True)
    await render_card(message, services, card_id)
    assert any("Checks (0/0)" in text for text in button_texts(message.edits[-1][1]))
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
    assert "Set new Title" in message.edits[-1][0]
    assert button_texts(message.edits[-1][1]) == ["↩️ Back"]


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
