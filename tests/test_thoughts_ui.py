from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from ui_harness import FakeCallback, FakeMessage, button_texts, press, services_for

from safwa.features.cards.model import Card
from safwa.features.cards.telegram import (
    render_card,
    render_card_creation,
    sanitize_card_creation_state,
)
from safwa.features.cards.telegram.thoughts import command_thoughts
from safwa.features.cards.use_cases import create_card, toggle_card_tag, update_card_fields
from safwa.features.profile.agent import profile_now
from safwa.features.profile.model import ProfileField, UserProfile
from safwa.features.profile.telegram.screens import command_profile
from safwa.features.profile.use_cases import validated_profile_value
from safwa.features.saved_requests.telegram.screens import _result_summary
from safwa.features.tags.use_cases import create_tag
from safwa.features.workspace_mutator.state import workspace_context
from tg_agent_shell.foundation.errors import DomainError
from tg_agent_shell.telegram.commands import navigation
from tg_agent_shell.telegram.model import UiSession


async def test_thoughts_list_opens_cards_refreshes_and_returns_to_menu(sessions):
    """SR-INBOX-014 — tests/brd/saved_requests.feature"""
    async with sessions() as session:
        first = await create_card(session, kind="thought", title="Z first")
        second = await create_card(session, kind="thought", title="A second")
        ordinary = await create_card(session, kind="action", title="Not a Thought")
        tag = await create_tag(session, "Inbox")
        await toggle_card_tag(session, ordinary.id, tag.id)
        await session.commit()
    services = services_for(sessions)
    message = FakeMessage(191, bot_message=True)
    callback = FakeCallback("", message)
    callback.data = "nav:thoughts"
    await navigation(callback, services)
    body, markup = message.edits[-1]
    assert "<b>Thoughts</b>" in body and "2 matching cards" in body
    assert button_texts(markup) == ["💭 Z first", "💭 A second", "↻ Refresh", "↩️ Menu"]
    await press(services, message, markup, "💭 Z first")
    assert "Quality Plan" in button_texts(message.edits[-1][1])
    await press(services, message, message.edits[-1][1], "↩️ Back")
    assert "<b>Thoughts</b>" in message.edits[-1][0]
    async with sessions() as session:
        await update_card_fields(session, first.id, {"title": "Renamed"})
        await session.delete(await session.get(Card, second.id))
        await session.commit()
    await press(services, message, message.edits[-1][1], "↻ Refresh")
    assert button_texts(message.edits[-1][1]) == ["💭 Renamed", "↻ Refresh", "↩️ Menu"]
    assert "1 matching card" in message.edits[-1][0]


async def test_empty_thoughts_list_has_refresh_and_menu(sessions):
    """SR-INBOX-014 — tests/brd/saved_requests.feature"""
    message = FakeMessage(192, bot_message=True)
    await command_thoughts(message, services_for(sessions))
    assert "0 matching cards" in message.edits[-1][0]
    assert button_texts(message.edits[-1][1]) == ["↻ Refresh", "↩️ Menu"]


async def test_manual_save_and_full_editor_show_only_thought_fields(sessions):
    """CD-THOUGHT-074 — tests/brd/cards.feature"""
    async with sessions() as session:
        session.add(UiSession(owner_id=42, kind="card_create", expires_at=datetime.now(UTC) + timedelta(hours=1), state=sanitize_card_creation_state({
            "kind": "thought", "title": "<Idea>", "note": "**One**", "blocked_description": "Waiting & asking",
        })))
        await session.commit()
    services = services_for(sessions)
    message = FakeMessage(99, bot_message=True)
    await render_card_creation(message, services)
    text, markup = message.edits[-1]
    assert "&lt;Idea&gt;" in text and "<b>One</b>" in text
    assert button_texts(markup) == ["🧩 Kind", "✏️ Title", "📝 Note", "📝 Blocked reason", "✅ Save", "🗑 Discard"]
    await press(services, message, markup, "✅ Save")
    async with sessions() as session:
        card = await session.scalar(select(Card))
        assert card.title == "<Idea>" and card.kind == "thought" and card.effective_stage is None
        card_id = card.id
    await render_card(message, services, card_id, full=True)
    text, markup = message.edits[-1]
    assert button_texts(markup) == ["Quality Plan", "Direct Create", "✏️ Title", "📝 Note", "📝 Blocked reason", "Delete", "🗜 Compact", "↩️ Menu"]
    for field in ("Stage:", "Priority:", "Deadline:", "Effort:", "Values:", "Tags:", "Children:"):
        assert field not in text


async def test_a_mixed_request_counts_a_thought_without_treating_it_as_work(sessions):
    """CD-THOUGHT-074 — tests/brd/cards.feature"""
    from safwa.features.cards.telegram.presentation import result_order

    async with sessions() as session:
        thought = await create_card(session, kind="thought", title="Idea")
        goal = await create_card(session, kind="goal", title="Result")
        action = await create_card(session, kind="action", title="Step")
        await session.commit()
        assert [item.id for item in await result_order(session, [thought, action, goal])] == [goal.id, action.id, thought.id]
        assert _result_summary([thought, action, goal]) == "🎯1 · ⭐️1 · 💭1"
        context = (await workspace_context(session)).state
        assert "Idea" not in context


async def test_thought_default_switch_is_persisted_and_read_live(sessions):
    """PS-THOUGHT-026 — tests/brd/profile.feature"""
    services = services_for(sessions)
    message = FakeMessage(190, bot_message=True)
    async with sessions() as session:
        profile = await session.get(UserProfile, 1)
        assert not profile.thoughts_by_default
        time, effort = profile.time_tracking, profile.effort_tracking
    await command_profile(message, services)
    markup = message.edits[-1][1]
    for on in (True, False):
        await press(services, message, markup, f"💭 Thoughts by default: {'off' if on else 'on'}")
        markup = message.bot.edits[-1][2]
        async with sessions() as session:
            profile = await session.get(UserProfile, 1)
            assert profile.thoughts_by_default is on
            assert (profile.time_tracking, profile.effort_tracking) == (time, effort)
            assert f"Thoughts by default: {'on' if on else 'off'}" in (await workspace_context(session)).state
        assert f"thoughts_by_default: {'on' if on else 'off'}" in await profile_now(SimpleNamespace(sessions=sessions, switches=()), "")
    with pytest.raises(DomainError):
        validated_profile_value(ProfileField.THOUGHTS_BY_DEFAULT, "yes")
