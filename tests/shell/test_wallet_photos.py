"""Photos, through the shell and a second application: labelled once, read in words after.

Nothing here imports Safwa. The model and Telegram are replaced at their boundaries; the
database, the window, the turn and the review are the real ones.
"""

from __future__ import annotations

import asyncio
import base64
import json
from datetime import UTC, datetime, timedelta

import pytest
from agent_turns import mutation_turn, route_turn
from sqlalchemy import select
from telegram_fakes import QueueTestMessage, owner_photo
from wallet.ledger.agent import RECEIPT_INSTRUCTIONS
from wallet.ledger.model import Entry
from wallet_harness import TODAY, press, seed_lists, take_a_turn

import tg_agent_shell.telegram.services as services_module
from llm_gateway import CompletionTurn
from telegram_llm import TELEGRAM_ALBUM_LIMIT
from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.history import TelegramMessage
from tg_agent_shell.media.library import (
    DESCRIBE_PROMPT,
    DESCRIBE_REASONING,
    DESCRIPTION_EXCHANGES,
    DESCRIPTION_MAX_WORDS,
    Photo,
)
from tg_agent_shell.media.telegram import photo_message, send_photo_screen
from tg_agent_shell.telegram import dismiss_prior_ui, open_citation, render_citations
from tg_agent_shell.telegram.services import OwnerAndWritingMiddleware


def answer(words: str) -> CompletionTurn:
    return CompletionTurn(content=words)


def images_in(request) -> list[str]:
    """Every picture a request hands the model, as the URL it carries."""
    return [
        part["image_url"]["url"]
        for message in request.messages
        if isinstance(message.get("content"), list)
        for part in message["content"]
        if part.get("type") == "image_url"
    ]


def text_of(request) -> str:
    return json.dumps([dict(message) for message in request.messages], ensure_ascii=False)


def described(request) -> str:
    """The words a label is written from: the text beside the photo."""
    return next(
        part["text"]
        for message in request.messages
        if isinstance(message.get("content"), list)
        for part in message["content"]
        if part.get("type") == "text"
    )


async def dialogue_rows(sessions) -> list[TelegramMessage]:
    async with sessions() as session:
        return list(
            await session.scalars(
                select(TelegramMessage).where(
                    TelegramMessage.kind == MessageKind.DIALOGUE_USER.value
                )
            )
        )


async def test_a_photo_is_kept_as_the_owners_message_under_a_short_label(wallet_bot):
    """TG-IMAGE-018 — tests/brd/tg_agent_shell/telegram_history.feature"""
    earlier = ("alpha", "bravo", "charlie", "delta")
    running = await wallet_bot.start(
        *(answer(f"reply to {word}") for word in earlier),
        answer("«Обед в кафе.»\nи ещё строка"),
        answer("Nice lunch."),
        answer("Чек из Migros"),
        answer("Got it."),
        answer("Two photos."),
    )
    # Telegram numbers a private chat in one sequence, so each message the owner sends is
    # above every answer before it.
    for index, word in enumerate(earlier, start=1):
        running.message.message_id = index * 10_000
        await take_a_turn(running, word)

    with_caption = owner_photo(running.message, 50_000, caption="обед с Анной")
    await photo_message(with_caption, running.services)
    without_caption = owner_photo(running.message, 60_000)
    await photo_message(without_caption, running.services)
    running.message.message_id = 70_000
    await take_a_turn(running, "What did I send?")

    requests = running.provider.requests
    describe = requests[4]
    assert describe.messages[0]["content"] == DESCRIBE_PROMPT
    assert f"at most {DESCRIPTION_MAX_WORDS} words" in DESCRIBE_PROMPT
    assert describe.reasoning_effort == DESCRIBE_REASONING
    context = described(describe)
    assert "The user is Owner." in context
    assert "обед с Анной" in context
    # The newest DESCRIPTION_EXCHANGES exchanges, and nothing older.
    kept = earlier[-DESCRIPTION_EXCHANGES:]
    assert all(word in context and f"reply to {word}" in context for word in kept)
    assert earlier[0] not in context
    # The size that is kept is the 1280 one, sent as the file itself.
    (url,) = images_in(describe)
    assert base64.b64decode(url.removeprefix("data:image/jpeg;base64,")).endswith(b"50000-y")

    advisor = requests[5]
    assert "[Обед в кафе](media:1) обед с Анной" in advisor.messages[-1]["content"]
    assert "[Чек из Migros](media:2)" in requests[7].messages[-1]["content"]
    later = text_of(requests[8])
    assert "[Обед в кафе](media:1) обед с Анной" in later
    assert "[Чек из Migros](media:2)" in later
    rows = await dialogue_rows(running.sessions)
    photo_rows = {row.message_id: row for row in rows}
    assert photo_rows[50_000].text == "обед с Анной"
    assert photo_rows[60_000].reads_as == [
        {"role": "user", "content": "[Чек из Migros](media:2)"}
    ]


async def test_an_album_is_one_message_answered_once(wallet_bot, monkeypatch):
    """TG-ALBUM-019 — tests/brd/tg_agent_shell/telegram_history.feature"""
    monkeypatch.setattr(services_module, "ALBUM_GATHER_SECONDS", 0.05)
    monkeypatch.setattr(services_module, "Message", QueueTestMessage)
    running = await wallet_bot.start(
        answer("Кот"), answer("Собака"), answer("Попугай"), answer("Three pets.")
    )
    album = [
        owner_photo(running.message, 950 + index, media_group_id="pets", caption=caption)
        for index, caption in enumerate(("мои питомцы", None, None))
    ]
    middleware = OwnerAndWritingMiddleware()

    async def handler(event, data):
        return await photo_message(event, data["services"], data.get("album"))

    await asyncio.gather(
        *(middleware(handler, photo, {"services": running.services}) for photo in album)
    )

    requests = running.provider.requests
    assert len(requests) == 4
    owner_side = requests[3].messages[-1]["content"]
    assert "[Кот](media:1) мои питомцы" in owner_side
    assert "[Собака](media:2)" in owner_side
    assert "[Попугай](media:3)" in owner_side
    assert owner_side.count("мои питомцы") == 1
    assert not any(photo.was_deleted for photo in album)
    assert len(await dialogue_rows(running.sessions)) == 3


async def test_the_conversation_carries_the_label_and_a_tool_reads_the_photo(wallet_bot):
    """TG-SIGHT-020 — tests/brd/tg_agent_shell/telegram_history.feature"""
    seeding = await wallet_bot.start()
    ids = await seed_lists(seeding.sessions)
    running = await wallet_bot.start(
        answer("Чек из кафе"),
        route_turn("bookkeeper"),
        mutation_turn(("read_receipt", {"media_id": 1}), prefix="look", content=""),
        answer("Суп 5.00\nХлеб 7.50\nTotal: 12.50 USD"),
        mutation_turn(
            (
                "entry",
                {
                    "mode": "create",
                    "wallet_id": ids["cash"],
                    "category_id": ids["food"],
                    "amount_minor": 1250,
                    "happened_on": TODAY.isoformat(),
                    "note": "Кафе",
                },
            )
        ),
        answer("Written down."),
        answer("The cafe is on the Cash wallet."),
    )

    await photo_message(owner_photo(running.message, 960), running.services)
    await press(running, "proposal_approve")

    requests = running.provider.requests
    assert [index for index, request in enumerate(requests) if images_in(request)] == [0, 3]
    read = requests[3]
    assert read.messages[0]["content"] == RECEIPT_INSTRUCTIONS
    assert described(read).startswith("[Чек из кафе](media:1)")
    assert "Total: 12.50 USD" in text_of(requests[4])
    label = "[Чек из кафе](media:1)"
    assert label in text_of(requests[1])
    assert label in text_of(requests[2])
    assert not any("api.telegram.org" in text_of(request) for request in requests)
    async with running.sessions() as session:
        assert len(list(await session.scalars(select(Entry)))) == 1

    now = datetime.now(UTC)
    day = await running.services.history.day_transcript(
        running.message.chat.id,
        start=now - timedelta(hours=1),
        end=now + timedelta(hours=1),
        token_budget=4_000,
    )
    assert label in day


async def test_a_photo_where_images_are_off_is_refused_plainly(wallet_bot):
    """TG-OFF-021 — tests/brd/tg_agent_shell/telegram_history.feature"""
    running = await wallet_bot.start(images=False)
    photo = owner_photo(running.message, 970, caption="чек")

    await photo_message(photo, running.services)

    assert running.chat[-1] == "Image input is off."
    assert running.provider.requests == []
    assert running.message.bot.downloads == []
    assert await dialogue_rows(running.sessions) == []


async def keep_photos(running, *metas: str) -> list[int]:
    return await running.services.media.keep(
        [
            (Photo(f"jpeg-{index}".encode(), 1280, 960, f"file-{index}"), meta)
            for index, meta in enumerate(metas, start=1)
        ]
    )


async def test_a_screen_shows_its_photos_as_one_album_above_its_words(wallet_bot):
    """SC-ALBUM-010 — tests/brd/tg_agent_shell/screens.feature"""
    running = await wallet_bot.start()
    media_ids = await keep_photos(running, "Кот", "Собака", "Попугай")

    screen = await send_photo_screen(
        running.message,
        running.services,
        media_ids,
        "<b>Питомцы</b>",
        kind=MessageKind.DASHBOARD,
        replace=False,
    )

    bot = running.message.bot
    assert bot.photos_sent == [["file-1", "file-2", "file-3"]]
    assert running.chat[-2:] == ["[photo]", "<b>Питомцы</b>"]
    album = [message.message_id for message in running.message.sent[:3]]
    assert screen.message_id not in album

    async with running.sessions() as session:
        kept = list(
            await session.scalars(
                select(TelegramMessage).where(TelegramMessage.message_id.in_(album))
            )
        )
    assert {row.kind for row in kept} == {MessageKind.DASHBOARD.value}
    assert all(row.text is None for row in kept)

    await dismiss_prior_ui(QueueTestMessage(message_id=990, is_bot=False, parent=running.message), running.services)
    assert set(album) | {screen.message_id} <= set(bot.deleted)

    too_many = await keep_photos(running, *(f"p{index}" for index in range(11)))
    assert len(too_many) == TELEGRAM_ALBUM_LIMIT + 1
    with pytest.raises(ValueError, match=str(TELEGRAM_ALBUM_LIMIT)):
        await send_photo_screen(
            running.message, running.services, too_many, "x", kind=MessageKind.DASHBOARD
        )


async def test_a_photo_telegram_forgot_is_sent_again_from_what_is_kept(wallet_bot):
    """SC-ALBUM-010 — tests/brd/tg_agent_shell/screens.feature"""
    running = await wallet_bot.start()
    media_ids = await keep_photos(running, "Кот")
    running.message.bot.forgot_file_ids = True

    await send_photo_screen(
        running.message, running.services, media_ids, "Кот", kind=MessageKind.DASHBOARD
    )

    (sent,) = running.message.bot.photos_sent
    assert sent[0].data == b"jpeg-1"
    running.message.bot.forgot_file_ids = False
    await send_photo_screen(
        running.message, running.services, media_ids, "Кот", kind=MessageKind.DASHBOARD
    )
    assert running.message.bot.photos_sent[-1][0].startswith("uploaded-")


async def test_a_photo_the_answer_points_at_opens_as_that_photo(wallet_bot):
    """SC-CITE-011 — tests/brd/tg_agent_shell/screens.feature"""
    running = await wallet_bot.start()
    (media_id,) = await keep_photos(running, "Кот на окне")
    running.services.bot_username = "wallet_bot"

    async with running.sessions() as session:
        linked = await render_citations(session, running.services, "Вот: [кот](media:1)")
    assert '<a href="https://t.me/wallet_bot?start=media-1">Кот на окне</a>' in linked

    await open_citation(running.message, running.services, f"media-{media_id}")
    bot = running.message.bot
    assert bot.photos_sent == [["file-1"]]
    assert running.chat[-1] == "Кот на окне"
    shown = running.message.sent[-1].message_id

    await dismiss_prior_ui(QueueTestMessage(message_id=991, is_bot=False, parent=running.message), running.services)
    assert shown in bot.deleted
