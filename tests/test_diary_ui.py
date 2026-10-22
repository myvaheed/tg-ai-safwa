"""The Diary browser and saved-day editor through the registered Telegram callbacks."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select
from telegram_fakes import QueueTestMessage
from ui_harness import FakeMessage, button_texts, place_of, press, services_for

from safwa.bootstrap.modules import FEATURE_COMMANDS
from safwa.features.diary import telegram
from safwa.features.diary.api import day_media
from safwa.features.diary.model import DiaryEntry
from safwa.features.diary.telegram import (
    DIARY_MONTHS,
    DIARY_PAGE_SIZE,
    DIARY_RECENT_DAYS,
    command_diary,
)
from safwa.features.diary.use_cases import create_diary_entry
from safwa.features.home.api import menu_markup
from safwa.foundation.workspace import Workspace
from tg_agent_shell.media.library import MediaLibrary, Photo
from tg_agent_shell.telegram import Place
from tg_agent_shell.telegram.dialogue import ordinary_text
from tg_agent_shell.telegram.model import UiSession


async def test_diary_browses_years_months_and_days_and_returns_to_the_same_page(sessions):
    """DI-BROWSE-026 — tests/brd/diary.feature"""
    async with sessions() as session:
        for year in range(2026 - DIARY_PAGE_SIZE, 2027):
            await create_diary_entry(session, entry_date=date(year, 1, 1), body=str(year))
        for month in (8, 9):
            for day in range(1, DIARY_PAGE_SIZE + 2):
                await create_diary_entry(
                    session, entry_date=date(2026, month, day), body=f"Entry {month}/{day} <saved>"
                )
        for month in range(2, 13):
            if month not in (8, 9):
                await create_diary_entry(session, entry_date=date(2026, month, 1), body="Day.")
        await session.commit()
    services = services_for(sessions)
    message = FakeMessage(70, bot_message=True)
    assert "📔 Diary" in button_texts(menu_markup(FEATURE_COMMANDS))
    assert not any(command.command == "diary" for command in FEATURE_COMMANDS)

    await command_diary(message, services)
    years, markup = message.edits[-1]
    labels = button_texts(markup)
    assert labels[:DIARY_PAGE_SIZE] == [str(2026 - offset) for offset in range(DIARY_PAGE_SIZE)]
    assert "◀ Previous" not in labels and "Next ▶" in labels
    await press(services, message, markup, "Next ▶")
    assert str(2026 - DIARY_PAGE_SIZE) in button_texts(message.edits[-1][1])
    assert "Next ▶" not in button_texts(message.edits[-1][1])
    await press(services, message, message.edits[-1][1], "◀ Previous")
    assert message.edits[-1][0] == years

    await press(services, message, message.edits[-1][1], "2026")
    months, markup = message.edits[-1]
    assert "2026" in months
    assert button_texts(markup) == [*reversed(DIARY_MONTHS), "↩️ Back"]
    await press(services, message, markup, "Декабрь")
    assert button_texts(message.edits[-1][1]) == ["1 декабря · 2026", "↩️ Back"]
    await press(services, message, message.edits[-1][1], "↩️ Back")
    markup = message.edits[-1][1]
    await press(services, message, markup, "Сентябрь")
    days, markup = message.edits[-1]
    assert "2026 · Сентябрь" in days
    assert len(button_texts(markup)) == DIARY_PAGE_SIZE + 2
    await press(services, message, markup, "Next ▶")
    last_page, markup = message.edits[-1]
    assert button_texts(markup) == ["1 сентября · 2026", "◀ Previous", "↩️ Back"]
    await press(services, message, markup, "1 сентября · 2026")
    entry, markup = message.edits[-1]
    assert "Entry 9/1 &lt;saved&gt;" in entry
    assert [[button.text for button in row] for row in markup.inline_keyboard] == [
        ["✏️ Edit", "↩️ Back"]
    ]
    await press(services, message, markup, "✏️ Edit")
    assert button_texts(message.edits[-1][1]) == ["🙂 Feeling", "📝 Entry", "↩️ Back"]
    await press(services, message, message.edits[-1][1], "📝 Entry")
    await press(services, message, message.bot.edits[-1][2], "↩️ Back")
    async with sessions() as session:
        assert await session.scalar(select(UiSession)) is None
    await press(services, message, message.edits[-1][1], "↩️ Back")
    assert "Entry 9/1 &lt;saved&gt;" in message.edits[-1][0]
    markup = message.edits[-1][1]
    await press(services, message, markup, "↩️ Back")
    assert message.edits[-1][0] == last_page
    await press(services, message, message.edits[-1][1], "↩️ Back")
    assert message.edits[-1][0] == months
    await press(services, message, message.edits[-1][1], "↩️ Back")
    assert message.edits[-1][0] == years


async def test_diary_browser_includes_a_photo_only_day_and_keeps_its_back_button(sessions):
    """DI-BROWSE-026 — tests/brd/diary.feature"""
    (media_id,) = await MediaLibrary(sessions, None).keep(
        [(Photo(b"jpeg", 1280, 960, "file-cat"), "Кот")]
    )
    async with sessions() as session:
        await create_diary_entry(session, entry_date=date(2026, 9, 26), body=None, media=[media_id])
        await session.commit()
    services = services_for(sessions)
    message = QueueTestMessage(is_bot=True, answer_as_new=True)
    await command_diary(message, services)
    await press(services, message, message.markups[-1], "2026")
    await press(services, message, message.markups[-1], "Сентябрь")
    await press(services, message, message.markups[-1], "26 сентября · 2026")
    assert message.bot.photos_sent == [["file-cat"]]
    body, markup = message.bot.drawn[-1], message.markups[-1]
    assert "26 сентября" in body
    assert (await place_of(sessions, markup, "↩️ Back")).args["month"] == 9
    album_ids = [photo.message_id for photo in message.sent[:-1]]
    await press(services, message.sent[-1], markup, "↩️ Back")
    assert set(album_ids) <= set(message.bot.deleted)
    assert "2026 · Сентябрь" in message.bot.drawn[-1]


async def test_diary_recent_uses_the_local_day_and_opens_empty_and_saved_dates(sessions, monkeypatch):
    """DI-RECENT-027 — tests/brd/diary.feature"""
    monkeypatch.setattr(telegram, "utcnow", lambda: datetime(2026, 9, 30, 22, 0, tzinfo=UTC))
    today = date(2026, 10, 1)
    async with sessions() as session:
        workspace = await session.get(Workspace, 1)
        workspace.timezone = "Europe/Istanbul"
        await create_diary_entry(session, entry_date=today, body="Today.", feeling_score=7)
        await create_diary_entry(
            session, entry_date=today - timedelta(days=DIARY_RECENT_DAYS), body="Too old."
        )
        await create_diary_entry(session, entry_date=today + timedelta(days=1), body="Future.")
        await session.commit()
    message = FakeMessage(80, bot_message=True)
    services = services_for(sessions)
    await command_diary(message, services)
    await press(services, message, message.edits[-1][1], "Last 7 days")
    recent, markup = message.edits[-1]
    labels = button_texts(markup)
    dates = [
        (await place_of(sessions, markup, label)).args["date"]
        for label in labels if label != "↩️ Back"
    ]
    assert dates == [(today - timedelta(days=offset)).isoformat() for offset in range(DIARY_RECENT_DAYS)]
    await press(services, message, markup, labels[1])
    assert "No entry for this day." in message.edits[-1][0]
    assert "30.09.2026" in message.edits[-1][0]
    assert button_texts(message.edits[-1][1]) == ["↩️ Back"]
    await press(services, message, message.edits[-1][1], "↩️ Back")
    assert message.edits[-1][0] == recent
    await press(services, message, message.edits[-1][1], labels[0])
    assert "Today." in message.edits[-1][0]
    await press(services, message, message.edits[-1][1], "↩️ Back")
    await press(services, message, message.edits[-1][1], "↩️ Back")
    assert "Last 7 days" in button_texts(message.edits[-1][1])


async def test_empty_diary_still_offers_all_seven_recent_dates(sessions):
    """DI-RECENT-027 — tests/brd/diary.feature"""
    message = FakeMessage(90, bot_message=True)
    services = services_for(sessions)
    await command_diary(message, services)
    assert "No entries here yet." in message.edits[-1][0]
    assert button_texts(message.edits[-1][1]) == ["Last 7 days", "↩️ Menu"]
    await press(services, message, message.edits[-1][1], "Last 7 days")
    assert len(button_texts(message.edits[-1][1])) == DIARY_RECENT_DAYS + 1


@pytest.mark.parametrize("score", [None, 7])
async def test_saved_diary_entry_is_replaced_without_changing_its_feeling(sessions, score):
    """DI-EDIT-030 — tests/brd/diary.feature"""
    async with sessions() as session:
        entry = await create_diary_entry(
            session, entry_date=date(2026, 9, 26), body="**Original** <words>.",
            feeling_score=score,
        )
        await session.commit()
        entry_id = entry.id
    services = services_for(sessions)
    message = FakeMessage(100, bot_message=True)
    back = Place("diary_browser", {"year": 2026, "month": 9, "page": 1})
    await telegram.render_diary(message, services, entry_id, back=back)
    await press(services, message, message.edits[-1][1], "✏️ Edit")
    await press(services, message, message.edits[-1][1], "📝 Entry")
    assert "**Original** &lt;words&gt;." in message.bot.edits[-1][1]
    assert "Markdown is supported" in message.bot.edits[-1][1]

    invalid = FakeMessage(101, text="   ", bot_message=False, bot=message.bot)
    await ordinary_text(invalid, services)
    assert "Entry cannot be empty." in message.bot.edits[-1][1]
    async with sessions() as session:
        assert (await session.get(DiaryEntry, entry_id)).body == "**Original** <words>."
        assert (await session.scalar(select(UiSession))).kind == "text_input"

    typed = FakeMessage(102, text="**New** <words>.", bot_message=False, bot=message.bot)
    await ordinary_text(typed, services)
    async with sessions() as session:
        saved = await session.get(DiaryEntry, entry_id)
        assert (saved.body, saved.feeling_score, saved.entry_date) == (
            "**New** <words>.", score, date(2026, 9, 26),
        )
        assert saved.version == 2
        assert await session.scalar(select(UiSession)) is None
    message_id, text, markup = message.bot.edits[-1]
    assert message_id == message.message_id
    assert "<b>New</b> &lt;words&gt;." in text
    assert button_texts(markup) == ["✏️ Edit", "↩️ Back"]
    assert await place_of(sessions, markup, "↩️ Back") == back
    assert invalid.was_deleted and typed.was_deleted


@pytest.mark.parametrize("score", [0, 10])
async def test_saved_diary_feeling_is_selected_without_changing_its_words(sessions, score):
    """DI-EDIT-030 — tests/brd/diary.feature"""
    async with sessions() as session:
        entry = await create_diary_entry(
            session, entry_date=date(2026, 9, 26), body="Saved words.", feeling_score=7,
        )
        await session.commit()
        entry_id = entry.id
    services = services_for(sessions)
    message = FakeMessage(110, bot_message=True)
    await telegram.render_diary(message, services, entry_id)
    assert button_texts(message.edits[-1][1]) == ["✏️ Edit", "↩️ Menu"]
    await press(services, message, message.edits[-1][1], "✏️ Edit")
    await press(services, message, message.edits[-1][1], "🙂 Feeling")
    assert button_texts(message.edits[-1][1]) == [
        ("✓ " if value == 7 else "") + f"{value} {face}"
        for value, face in telegram.FEELING_SCORE_EMOJI.items()
    ] + ["↩️ Back"]
    await press(services, message, message.edits[-1][1], "↩️ Back")
    assert button_texts(message.edits[-1][1]) == ["🙂 Feeling", "📝 Entry", "↩️ Back"]
    await press(services, message, message.edits[-1][1], "🙂 Feeling")
    markup = message.edits[-1][1]
    label = f"{score} {telegram.FEELING_SCORE_EMOJI[score]}"
    await press(services, message, markup, label)
    assert button_texts(message.edits[-1][1]) == ["✏️ Edit", "↩️ Menu"]
    await press(services, message, markup, label)
    async with sessions() as session:
        saved = await session.get(DiaryEntry, entry_id)
        assert (saved.body, saved.feeling_score, saved.version) == ("Saved words.", score, 2)


async def test_photo_only_diary_gets_words_and_feeling_without_losing_photos(sessions):
    """DI-EDIT-030 — tests/brd/diary.feature"""
    (media_id,) = await MediaLibrary(sessions, None).keep(
        [(Photo(b"jpeg", 1280, 960, "file-cat"), "Кот")]
    )
    async with sessions() as session:
        entry = await create_diary_entry(
            session, entry_date=date(2026, 9, 26), body=None, media=[media_id],
        )
        await session.commit()
        entry_id = entry.id
    services = services_for(sessions)
    message = QueueTestMessage(is_bot=False, answer_as_new=True)
    await telegram.render_diary(message, services, entry_id, replace=False)
    await press(services, message.sent[-1], message.markups[-1], "✏️ Edit")
    await press(services, message.sent[-1], message.markups[-1], "🙂 Feeling")
    assert not any(label.startswith("✓") for label in message.buttons())
    await press(services, message.sent[-1], message.markups[-1], "6 🙂")
    await press(services, message.sent[-1], message.markups[-1], "✏️ Edit")
    editor = message.sent[-1]
    await press(services, editor, message.markups[-1], "📝 Entry")
    typed = QueueTestMessage(
        message_id=9999, text="**Cat** at home.", is_bot=False, answer_as_new=True, parent=message,
    )
    await ordinary_text(typed, services)
    assert "<b>Cat</b> at home." in message.bot.drawn[-1]
    assert message.buttons() == ["✏️ Edit", "↩️ Menu"]
    async with sessions() as session:
        saved = await session.get(DiaryEntry, entry_id)
        assert (saved.body, saved.feeling_score) == ("**Cat** at home.", 6)
        assert await day_media(session, entry_id) == [(media_id, "Кот")]
    assert editor.message_id in message.bot.deleted
    photo_messages = [sent for sent in message.sent if sent.photo]
    assert [photo.message_id for photo in photo_messages if photo.message_id not in message.bot.deleted] == [
        photo_messages[-1].message_id
    ]
