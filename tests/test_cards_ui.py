"""The Card screens: the dashboard, the draft, the selectors and the Card itself."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from ui_harness import (
    CALLBACK_ACTIONS,
    FakeBot,
    FakeCallback,
    FakeMessage,
    button_texts,
    services_for,
    ui_sources,
)

from safwa.constants import SELECTOR_PAGE_SIZE
from safwa.features.cards.model import Card, CardStage
from safwa.features.cards.telegram import (
    command_ideas,
    render_card,
    render_card_choices,
    render_card_creation,
    render_children,
    render_dashboard,
)
from safwa.features.cards.telegram import idea as idea_screens
from safwa.features.cards.telegram.selectors import (
    RELATION_CHOICES,
    handle_card_creation_chooser,
)
from safwa.features.cards.use_cases import (
    EFFORT_POINTS,
    archive_subtree,
    create_card,
    finish_action,
    toggle_card_check,
)
from safwa.features.checks.use_cases import create_check
from safwa.features.tags.telegram import render_tag
from safwa.features.tags.use_cases import create_tag
from safwa.features.values.telegram import render_value
from safwa.features.values.use_cases import create_value
from tg_agent_shell.telegram import callback_token_handler
from tg_agent_shell.telegram.dialogue import ordinary_text
from tg_agent_shell.telegram.model import CallbackToken, UiSession


def test_every_card_relationship_is_wired_to_both_selector_surfaces() -> None:
    """Adding a relationship to the table must not leave half the screens unreachable."""
    for field, relation in RELATION_CHOICES.items():
        assert f"card_choose_{field}" in CALLBACK_ACTIONS
        assert f"card_create_choose_{field}" in CALLBACK_ACTIONS
        assert f"card_toggle_{relation.singular}" in CALLBACK_ACTIONS
        assert f"card_create_toggle_{relation.singular}" in CALLBACK_ACTIONS


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
    """SC-PAGE-007 — tests/brd/tg_agent_shell/screens.feature"""
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


async def test_one_tap_moves_an_action_one_step_along_the_stage_ladder(sessions) -> None:
    """The three stage lists are one screen, and the button sits on the side the move goes."""
    async with sessions() as session:
        for stage in ("backlog", "sprint", "today"):
            await create_card(
                session, kind="action", title=f"Do {stage}", stage=stage, effort_points=1
            )
        await session.commit()

    services = services_for(sessions)
    rows = {}
    for stage in (CardStage.BACKLOG, CardStage.SPRINT, CardStage.TODAY):
        message = FakeMessage(96, bot_message=True)
        await render_dashboard(message, services, stage, title=stage.value.title())
        rows[stage] = [button.text for button in message.edits[-1][1].inline_keyboard[0]]

    # Up the ladder the button is on the right; coming back down it is on the left.
    assert rows[CardStage.BACKLOG][1] == "🏃"
    assert rows[CardStage.SPRINT][1] == "☀️"
    assert rows[CardStage.TODAY][0] == "🏃"


async def test_tag_selector_pages_instead_of_truncating(sessions) -> None:
    """TA-PICK-007 — tests/brd/tags.feature"""
    overflow = SELECTOR_PAGE_SIZE + 2
    async with sessions() as session:
        card = await create_card(session, kind="action", title="Pick tags", effort_points=1)
        for index in range(overflow):
            await create_tag(session, f"Tag {index:02d}")
        await session.commit()
        card_id = card.id

    services = services_for(sessions)
    message = FakeMessage(96, bot_message=True)
    await render_card_choices(message, services, "card_choose_tags", card_id)

    text, markup = message.edits[-1]
    names = [name for name in button_texts(markup) if name.startswith("Tag ")]
    assert "page 1/2" in text
    assert names == [f"Tag {index:02d}" for index in range(SELECTOR_PAGE_SIZE)]

    nxt = next(button for row in markup.inline_keyboard for button in row if button.text == "Next ▶")
    await callback_token_handler(FakeCallback(nxt.callback_data.split(":", 1)[1], message), services)

    text, markup = message.edits[-1]
    names = [name for name in button_texts(markup) if name.startswith("Tag ")]
    # The last Tags used to be unreachable: the selector stopped at a hard limit with no paging.
    assert "page 2/2" in text
    assert names == [f"Tag {index:02d}" for index in range(SELECTOR_PAGE_SIZE, overflow)]

    # Ticking one on page 2 used to redraw page 1, which undid the paging on every tap.
    last = next(
        button
        for row in markup.inline_keyboard
        for button in row
        if button.text == f"Tag {overflow - 1:02d}"
    )
    await callback_token_handler(
        FakeCallback(last.callback_data.split(":", 1)[1], message), services
    )
    text, markup = message.edits[-1]
    assert "page 2/2" in text
    assert f"✓ Tag {overflow - 1:02d}" in button_texts(markup)

    back = next(
        button for row in markup.inline_keyboard for button in row if button.text == "↩️ Back"
    )
    await callback_token_handler(FakeCallback(back.callback_data.split(":", 1)[1], message), services)
    assert "Pick tags" in message.edits[-1][0]


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
    for render, item_id in ((render_value, value_id), (render_tag, tag_id)):
        message = FakeMessage(item_id, bot_message=True)
        await render(message, services, mode="view", item_id=item_id)
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


async def test_a_closed_card_shows_when_it_closed_and_where_its_series_went(sessions) -> None:
    async with sessions() as session:
        card = await create_card(
            session, kind="action", title="Run", stage="today", effort_points=1, repeatable=True
        )
        result = await finish_action(session, card.id)
        await session.commit()
        card_id, live_id = card.id, result.successor_ids[0]

    services = services_for(sessions)
    message = FakeMessage(48, bot_message=True)
    await render_card(message, services, card_id)

    text, markup = message.edits[-1]
    # One wording: the owner reads the same marks the model does, and the id in them is
    # the open instance the series moved to.
    assert f"Title: <b>Run [🔄1, live #{live_id}]</b>" in text
    assert "Completed at: " in text
    current = next(button for button in button_texts(markup) if button.startswith("🔄 Current"))
    assert current == "🔄 Current: Run"

    await callback_token_handler(
        FakeCallback(
            next(
                button
                for row in markup.inline_keyboard
                for button in row
                if button.text == current
            ).callback_data.split(":", 1)[1],
            message,
        ),
        services,
    )
    assert "Stage: Today" in message.edits[-1][0]
    async with sessions() as session:
        assert (await session.scalar(select(UiSession))).state["card_id"] == live_id


async def test_card_text_and_blocked_reason_stay_on_one_validated_editor(sessions) -> None:
    """CD-BLOCKED-010 — tests/brd/cards.feature"""
    async with sessions() as session:
        # An Action, because Blocked is an Action field and no other kind is offered it.
        card = await create_card(session, kind="action", title="Original", effort_points=2)
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


async def test_cd_tree_005_no_screen_can_change_a_cards_parent(sessions) -> None:
    """CD-TREE-005 — tests/brd/cards.feature"""
    async with sessions() as session:
        goal = await create_card(session, title="Ship product", kind="goal")
        child = await create_card(
            session, title="Write announcement", kind="action", parent_id=goal.id, effort_points=5
        )
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
    assert not any(text.startswith("🌳 Parent") for text in buttons)

    bot = FakeBot()
    card_message = FakeMessage(52, bot_message=True, bot=bot)
    await render_card(
        card_message,
        services_for(sessions),
        child.id,
        replace_message_id=card_message.message_id,
    )
    # The one Parent button on the Card screen opens the parent; it does not choose one.
    parent_button = next(
        button
        for row in bot.edits[-1][2].inline_keyboard
        for button in row
        if button.text.startswith("🌳 Parent")
    )
    async with sessions() as session:
        token = await session.get(
            CallbackToken, parent_button.callback_data.removeprefix("cb:")
        )
        assert token is not None and token.action == "card_view"

    # And no screen in the whole application writes one either.
    for path in ui_sources():
        assert "set_card_parent" not in path.read_text(encoding="utf-8"), path


async def test_cd_field_007_a_goal_draft_is_not_offered_an_actions_controls(sessions) -> None:
    """CD-FIELD-007 — tests/brd/cards.feature"""
    action_only = {"🚧 Blocked", "🔢 Effort", "🔁 Repeat", "🏷 Categories", "⚡ Energy"}
    async with sessions() as session:
        editor = UiSession(
            owner_id=42,
            kind="card_create",
            state={"kind": "action", "title": "Run", "effort_points": 2, "blocked": False},
            expires_at=datetime.now(UTC).replace(year=2030),
        )
        session.add(editor)
        await session.commit()

    message = FakeMessage(53, bot_message=True)
    await render_card_creation(message, services_for(sessions))
    assert action_only <= set(button_texts(message.edits[-1][1]))

    async with sessions() as session:
        stored = await session.get(UiSession, editor.id)
        stored.state = {**stored.state, "kind": "goal", "blocked": True}
        await session.commit()

    goal_message = FakeMessage(54, bot_message=True)
    await render_card_creation(goal_message, services_for(goal_sessions := sessions))
    goal_buttons = set(button_texts(goal_message.edits[-1][1]))
    assert not (action_only & goal_buttons)
    assert "📝 Blocked reason" not in goal_buttons

    async with goal_sessions() as session:
        stored = await session.get(UiSession, editor.id)
        assert stored.state["blocked"] is False


async def test_cd_effort_008_save_appears_only_once_the_draft_has_an_effort(sessions) -> None:
    """CD-EFFORT-008 — tests/brd/cards.feature"""
    async with sessions() as session:
        editor = UiSession(
            owner_id=42,
            kind="card_create",
            state={"kind": "action", "title": "Run", "effort_points": None},
            expires_at=datetime.now(UTC).replace(year=2030),
        )
        session.add(editor)
        await session.commit()

    message = FakeMessage(55, bot_message=True)
    await render_card_creation(message, services_for(sessions))
    text, markup = message.edits[-1]
    assert "✅ Save" not in button_texts(markup)
    assert "An Action needs effort points" in text

    async with sessions() as session:
        stored = await session.get(UiSession, editor.id)
        stored.state = {**stored.state, "effort_points": min(EFFORT_POINTS)}
        await session.commit()

    ready = FakeMessage(56, bot_message=True)
    await render_card_creation(ready, services_for(sessions))
    assert "✅ Save" in button_texts(ready.edits[-1][1])


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
    kinds = set(button_texts(message.edits[-1][1]))
    assert {"🎯 Goal", "✓ ⭐️ Action"} <= kinds
    # A Subgoal needs a Goal above it, and no screen sets a parent.
    assert not any("Subgoal" in text for text in kinds)

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
        subgoal = await create_card(
            session,
            title="Prepare release",
            kind="subgoal",
            parent_id=goal.id,
        )
        done = await create_card(
            session,
            title="Publish build",
            kind="action",
            parent_id=subgoal.id,
            effort_points=3,
        )
        remaining = await create_card(
            session,
            title="Write announcement",
            kind="action",
            parent_id=goal.id,
            effort_points=5,
        )
        await finish_action(session, done.id)
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
    assert any("🧩 Subgoal · Prepare release" in text for text in children_texts)
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


async def test_no_screen_offers_a_goal_or_an_idea_a_stage_control(sessions) -> None:
    """CD-STAGE-013 — tests/brd/cards.feature"""
    async with sessions() as session:
        goal = await create_card(session, kind="goal", title="Health")
        subgoal = await create_card(session, kind="subgoal", title="Sleep better", parent_id=goal.id)
        action = await create_card(
            session, kind="action", title="Buy a pillow", effort_points=2, parent_id=subgoal.id
        )
        await session.commit()
        ids = (goal.id, subgoal.id, action.id)

    services = services_for(sessions)
    for index, card_id in enumerate(ids, start=310):
        message = FakeMessage(index, bot_message=True)
        await render_card(message, services, card_id)
        offered = "📍 Stage" in button_texts(message.edits[-1][1])
        assert offered is (card_id == ids[2])

    # The creation screen offers it for an Action alone, too.
    async with sessions() as session:
        editor = UiSession(
            owner_id=42,
            kind="card_create",
            state={"kind": "action", "title": "Run", "effort_points": 2},
            expires_at=datetime.now(UTC).replace(year=2030),
        )
        session.add(editor)
        await session.commit()
        editor_id = editor.id

    message = FakeMessage(320, bot_message=True)
    await render_card_creation(message, services)
    assert "📍 Stage" in button_texts(message.edits[-1][1])

    async with sessions() as session:
        stored = await session.get(UiSession, editor_id)
        stored.state = {**stored.state, "kind": "goal"}
        await session.commit()

    goal_message = FakeMessage(321, bot_message=True)
    await render_card_creation(goal_message, services)
    assert "📍 Stage" not in button_texts(goal_message.edits[-1][1])


async def test_a_goal_screen_names_each_blocked_action_and_quotes_its_reason(sessions) -> None:
    """CD-BLOCKED-019 — tests/brd/cards.feature"""
    async with sessions() as session:
        goal = await create_card(session, kind="goal", title="Health")
        await create_card(
            session,
            kind="action",
            title="Buy a pillow",
            effort_points=2,
            parent_id=goal.id,
            blocked=True,
            blocked_description="Shop is shut",
        )
        await session.commit()
        goal_id = goal.id

    services = services_for(sessions)
    message = FakeMessage(330, bot_message=True)
    await render_card(message, services, goal_id)

    text = message.edits[-1][0]
    assert "Blocked: Yes" in text
    assert "Blocked by Buy a pillow: Shop is shut" in text
    # A Goal has no reason of its own, so nothing asks for one.
    assert "Blocked description" not in text
    assert "🚧 Blocked" not in button_texts(message.edits[-1][1])


async def test_cd_archive_027_an_archived_card_reads_as_archived(sessions) -> None:
    """CD-ARCHIVE-027 — tests/brd/cards.feature"""
    async with sessions() as session:
        plain = await create_card(
            session, kind="action", title="Walk", effort_points=2, stage="today"
        )
        await finish_action(session, plain.id)
        await archive_subtree(session, plain.id)
        repeating = await create_card(
            session, kind="action", title="Run", effort_points=2, stage="today", repeatable=True
        )
        await finish_action(session, repeating.id)
        await archive_subtree(session, repeating.id)
        await session.commit()
        plain_id, repeating_id = plain.id, repeating.id

    services = services_for(sessions)
    message = FakeMessage(500, bot_message=True)
    await render_card(message, services, plain_id)
    text, labels = message.edits[-1][0], button_texts(message.edits[-1][1])

    assert "[📦]" in text
    assert "♻️ Reopen" in labels
    assert "Delete" in labels
    # Nothing that would edit it: no field control, and no second trip to the archive.
    assert "Archive" not in labels
    assert not [label for label in labels if label in {"✏️ Title", "📍 Stage", "💎 Values"}]

    # A closed repeat cannot be reopened, so Delete is the only way out of the archive.
    message = FakeMessage(501, bot_message=True)
    await render_card(message, services, repeating_id)
    repeating_labels = button_texts(message.edits[-1][1])
    assert "♻️ Reopen" not in repeating_labels
    assert "Delete" in repeating_labels


async def test_cd_idea_030_ideas_have_a_list_of_their_own_and_safwa_expands_one(
    sessions, monkeypatch
) -> None:
    """CD-IDEA-030 — tests/brd/cards.feature"""
    async with sessions() as session:
        first = await create_card(session, kind="idea", title="Cold showers", note="One week")
        await create_card(session, kind="idea", title="Learn to sail")
        await session.commit()
        first_id = first.id

    services = services_for(sessions)
    assert "ideas" in {screen.nav for screen in services.commands}

    listing = FakeMessage(310, bot_message=True)
    await command_ideas(listing, services)
    text, markup = listing.edits[-1]
    assert "Cold showers" in text and "Learn to sail" in text
    assert "➕ New Idea" in button_texts(markup)

    screen = FakeMessage(311, bot_message=True)
    await render_card(screen, services, first_id)
    text, markup = screen.edits[-1]
    assert "Title: <b>Cold showers</b>" in text
    assert "Note: One week" in text
    assert button_texts(markup) == ["✏️ Title", "📝 Note", "✨ Expand", "Delete", "↩️ Back"]

    turns: list[str] = []

    async def record(_message, _services, request, _source) -> None:
        turns.append(request)

    monkeypatch.setattr(idea_screens, "run_dialogue_turn", record)
    expand = next(
        button
        for row in markup.inline_keyboard
        for button in row
        if button.text == "✨ Expand"
    )
    await callback_token_handler(
        FakeCallback(expand.callback_data.split(":", 1)[1], screen), services
    )

    assert turns == ["Разверни идею «Cold showers» в карточки.\nЗаметка: One week"]
    async with sessions() as session:
        unchanged = await session.get(Card, first_id)
        assert (unchanged.kind, unchanged.title) == ("idea", "Cold showers")


async def test_cd_effort_008_the_effort_selector_names_what_each_rung_costs(sessions) -> None:
    """CD-EFFORT-008 — tests/brd/cards.feature"""
    async with sessions() as session:
        card = await create_card(session, kind="action", title="Run", effort_points=2)
        await session.commit()
        card_id = card.id

    message = FakeMessage(320, bot_message=True)
    await render_card_choices(message, services_for(sessions), "card_choose_effort", card_id)
    text, markup = message.edits[-1]
    assert "how much the whole thing takes in your usual state" in text
    labels = button_texts(markup)
    assert "0.5 · done in passing, the load is barely noticed" in labels
    assert "✓ 2 · a little tired, but able to carry on without a rest" in labels
