"""Presentation of free text, field labels and copyable Markdown sources."""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest
from ui_harness import FakeMessage, link_place, services_for

from safwa.bootstrap.modules import ALLOWED_VIEWS
from safwa.features.cards.telegram import render_card
from safwa.features.cards.telegram.creation import render_card_creation, start_manual_card_creation
from safwa.features.cards.use_cases import create_card
from safwa.features.checks.use_cases import create_check
from safwa.features.diary.use_cases import create_diary_entry
from safwa.features.planning.telegram import render_sprint
from safwa.features.planning.use_cases import start_sprint
from safwa.features.profile.model import UserProfile
from safwa.features.profile.telegram.screens import profile_text
from safwa.features.reminders.api import resolve
from safwa.features.reminders.use_cases import create_reminder
from safwa.features.saved_requests.use_cases import create_saved_request
from safwa.features.tags.telegram import render_tag
from safwa.features.tags.use_cases import create_tag
from safwa.features.values.telegram import render_value
from safwa.features.values.use_cases import create_value
from telegram_llm import markdown_to_telegram_html, split_telegram_text
from tg_agent_shell.proposals.api import Effect, ShownField
from tg_agent_shell.proposals.marks import field_line
from tg_agent_shell.telegram import Place, TextInputScreen, open_item_screen, render_citations
from tg_agent_shell.telegram.model import UiSession
from tg_agent_shell.telegram.text_input import _screen_from_state, _screen_text

WORDS = "**Важно** и *тихо*, ~~позже~~; `x < y`.\n- Один\n- Два 🧠\n<script>"


@pytest.mark.parametrize(
    "item_type", ["card", "check", "value", "tag", "request", "reminder", "diary"]
)
async def test_free_text_on_item_screens_renders_markdown(sessions, item_type):
    """SC-TEXT-016 — tests/brd/tg_agent_shell/screens.feature"""
    async with sessions() as session:
        if item_type == "card":
            item = await create_card(session, kind="action", title="Literal **title**", note=WORDS)
        elif item_type == "check":
            item = await create_check(session, title="Literal **title**", note=WORDS)
        elif item_type == "value":
            item = await create_value(session, "Literal **title**", WORDS)
        elif item_type == "tag":
            item = await create_tag(session, "Literal **title**", WORDS)
        elif item_type == "request":
            item = await create_saved_request(
                session, "Literal **title**", "SELECT 'card' AS item_type, id FROM ai_cards", WORDS, views=ALLOWED_VIEWS
            )
        elif item_type == "reminder":
            item = await create_reminder(
                session,
                remind_text=WORDS,
                tz=ZoneInfo("UTC"),
                schedule=resolve(interval_minutes=120, now=datetime.now(UTC), tz=ZoneInfo("UTC")),
            )
        else:
            item = await create_diary_entry(session, entry_date=date(2026, 10, 18), body=WORDS)
        await session.commit()
        item_id = item.id
        stored = getattr(
            item,
            {"card": "note", "check": "note", "diary": "body", "reminder": "remind_text"}.get(
                item_type, "description"
            ),
        )

    message = FakeMessage(1100, bot_message=True)
    await open_item_screen(message, services_for(sessions), item_type, item_id)
    text = message.edits[-1][0]
    assert "<b>Важно</b>" in text and "<i>тихо</i>" in text
    assert "<s>позже</s>" in text and "<code>x &lt; y</code>" in text
    assert "\n- Один\n- Два 🧠" in text and "&lt;script&gt;" in text
    assert stored == WORDS
    if item_type in {"card", "check", "value", "tag", "request"}:
        assert "Literal **title**" in text


async def test_card_labels_relations_and_blocked_text_have_separate_formatting(sessions):
    """SC-LABEL-015 — tests/brd/tg_agent_shell/screens.feature"""
    async with sessions() as session:
        parents = [
            await create_card(session, kind="goal", title=title) for title in ("Work", "Life")
        ]
        card = await create_card(
            session,
            kind="action",
            title="Release",
            parent_ids={parent.id for parent in parents},
            note="**Read** the plan.",
            blocked_description="Wait for **approval**.",
            categories={"work"},
        )
        await session.commit()
        card_id, parent_id = card.id, parents[0].id
    services = services_for(sessions)
    message = FakeMessage(1101, bot_message=True)
    await render_card(message, services, card_id, full=True)
    text = message.edits[-1][0]
    for label in ("Kind", "Title", "Parents", "Stage", "Categories", "Values", "Tags", "Blocked"):
        assert f"<i>{label}:</i> " in text
    assert "\n\n<i>Note:</i>\n<b>Read</b> the plan.\n\n" in text
    assert "<i>Blocked description:</i>\nWait for <b>approval</b>." in text
    assert await link_place(sessions, text, "Work") == Place("card_view", {"id": card_id}).child(
        "card_view", id=parent_id
    )


@pytest.mark.parametrize("entity", ["card", "value", "tag"])
async def test_manual_drafts_render_markdown_without_links_that_abandon_the_draft(sessions, entity):
    """SC-TEXT-016 — tests/brd/tg_agent_shell/screens.feature"""
    services = services_for(sessions)
    async with sessions() as session:
        linked = await create_card(session, kind="goal", title="Plan")
        await session.commit()
        text = f"{WORDS}\n[Plan](card:{linked.id})"
    message = FakeMessage(1102, bot_message=True)
    if entity == "card":
        await start_manual_card_creation(message, services)
        async with sessions() as session:
            from sqlalchemy import select

            draft = await session.scalar(select(UiSession).where(UiSession.kind == "card_create"))
            draft.state = {**draft.state, "title": "New", "note": text}
            await session.commit()
        await render_card_creation(message, services)
    else:
        render = render_value if entity == "value" else render_tag
        await render(message, services, mode="create", values={"name": "New", "description": text})
    shown = message.edits[-1][0]
    assert "<b>Важно</b>" in shown and "<code>x &lt; y</code>" in shown
    assert "🎯 Plan" in shown and "<a " not in shown


async def test_code_preserves_citations_while_prose_links_and_resolves_missing_targets(sessions):
    """SC-TEXT-016 — tests/brd/tg_agent_shell/screens.feature"""
    async with sessions() as session:
        card = await create_card(session, kind="goal", title="Plan")
        citation = f"[Plan](card:{card.id})"
        text = markdown_to_telegram_html(
            f"**{citation}**\n`{citation}`\n```\n{citation}\n[Gone](card:999)\n```\n[Gone](card:999)"
        )
        shown = await render_citations(
            session, services_for(sessions), text, here=Place("card_view", {"id": card.id})
        )
        only_code = await render_citations(
            session, services_for(sessions), f"<code>{citation}</code>"
        )
    assert shown.count("<a ") == 1
    assert f"<code>{citation}</code>" in shown
    assert f"<pre><code>{citation}\n[Gone](card:999)\n</code></pre>" in shown
    assert shown.endswith("\nGone")
    assert only_code == f"<code>{citation}</code>"


async def test_profile_and_sprint_render_free_text_and_keep_short_settings_literal(sessions):
    """SC-TEXT-016 — tests/brd/tg_agent_shell/screens.feature"""
    async with sessions() as session:
        profile = await session.get(UserProfile, 1)
        profile.about_me = profile.advisor_instructions = profile.diary_instructions = "**Focus**"
        shown = profile_text(profile, "Europe/Istanbul")
        assert shown.count("<b>Focus</b>") == 3
        assert "<i>Timezone:</i> Europe/Istanbul" in shown
        await create_card(session, kind="action", title="Release", stage="sprint")
        await start_sprint(session, success_criteria="Ship **today**.")
        await session.commit()
    message = FakeMessage(1103, bot_message=True)
    await render_sprint(message, services_for(sessions))
    assert "<i>Success criteria:</i>\nShip <b>today</b>." in message.edits[-1][0]


def test_editor_keeps_markdown_source_and_hint_when_restored_after_validation():
    """SC-TEXT-016 — tests/brd/tg_agent_shell/screens.feature"""
    state = {
        "text_input": {
            "title": "Note",
            "current_value": "**Source** <tag>",
            "instruction": "Send the Note.",
            "back": Place("card_view", {"id": 1}).address,
            "markdown": True,
        }
    }
    screen = _screen_from_state(state)
    text = _screen_text(screen, "Try again.")
    assert "<i>Current value:</i>\n<pre>**Source** &lt;tag&gt;</pre>" in text
    assert "Markdown is supported" in text and "Try again." in text
    assert "Markdown is supported" not in _screen_text(
        TextInputScreen("Title", "", "Send a title.", Place("home")), None
    )


def test_review_keeps_source_markdown_and_changes_visible():
    """SC-LABEL-015 — tests/brd/tg_agent_shell/screens.feature"""
    assert (
        field_line(ShownField("Note", "plain", "**bold**"), Effect.UPDATE)
        == "<i>Note:</i> <s>plain</s> <b>**bold**</b>"
    )
    assert (
        field_line(ShownField("Note", None, "**bold**"), Effect.CREATE) == "<i>Note:</i> **bold**"
    )
    long = field_line(ShownField("Note", None, "**long** " * 600), Effect.CREATE)
    assert "<blockquote expandable>**long**" in long
    parts = split_telegram_text(long)
    assert len(parts) > 1
    assert all(
        part.count("<blockquote expandable>") == part.count("</blockquote>") for part in parts
    )
