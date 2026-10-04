"""Photos on Diary days: put on a day by number and a few words, and shown as one album."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import pytest
from sqlalchemy import text
from telegram_fakes import QueueTestMessage
from ui_harness import services_for

from llm_gateway import ToolCall
from safwa.bootstrap.modules import PROPOSALS
from safwa.features.diary.agent import DiaryToolInput, day_read_tool
from safwa.features.diary.api import day_media
from safwa.features.diary.hooks import DAY_NOT_READ, unread_days
from safwa.features.diary.proposal import DiaryProposalHandler
from safwa.features.diary.telegram import DiaryProposalPresenter, render_diary
from safwa.features.diary.use_cases import (
    DIARY_DAY_PHOTOS,
    create_diary_entry,
    delete_diary_entry,
    diary_entry_for,
    update_diary_entry,
)
from safwa.features.planning.closing import DayTally
from safwa.features.retro.analysis import days_text
from safwa.features.retro.use_cases import DiaryDay
from telegram_llm import TELEGRAM_ALBUM_LIMIT
from tg_agent_shell.foundation.errors import DomainError
from tg_agent_shell.hooks.contracts import BeforeProposals, ProposedCall, SessionRead
from tg_agent_shell.media.library import DESCRIPTION_MAX_WORDS, ChatMedia, MediaLibrary, Photo
from tg_agent_shell.media.telegram import open_media
from tg_agent_shell.proposals.api import (
    ApplyContext,
    ChangeAction,
    ProposalChange,
    ToolPreparationError,
)
from tg_agent_shell.proposals.prepare import ChangePreparer
from tg_agent_shell.telegram import dismiss_prior_ui, render_citations

DAY = date(2026, 9, 26)


async def keep_photos(sessions, *metas: str) -> list[int]:
    """Photos the owner sent, kept by the shell the way a photo arriving is kept."""
    return await MediaLibrary(sessions, None).keep(  # type: ignore[arg-type]
        [
            (Photo(f"jpeg-{index}".encode(), 1280, 960, f"file-{index}"), meta)
            for index, meta in enumerate(metas, start=1)
        ]
    )


async def prepared(sessions, arguments: dict[str, Any]) -> ProposalChange:
    """One `diary` call, prepared the way the review is: as the change Save would apply."""
    change = PROPOSALS.change_from_tool("diary", arguments)
    async with sessions() as session:
        result = await ChangePreparer(None, None, PROPOSALS).prepare(session, change)  # type: ignore[arg-type]
    return ProposalChange(
        entity="diary",
        action=ChangeAction(change.action),
        entity_id=change.id,
        expected_version=result.expected_version,
        values=result.values,
    )


async def saved(sessions, change: ProposalChange) -> None:
    async with sessions() as session:
        await DiaryProposalHandler().apply(ApplyContext(session, frozenset()), change)
        await session.commit()


async def photos_on(sessions, day: date) -> list[tuple[int, str]]:
    async with sessions() as session:
        entry = await diary_entry_for(session, day)
        return await day_media(session, entry.id)


def test_di_photo_017_a_photo_is_put_on_a_day_by_its_number() -> None:
    """DI-PHOTO-017 — tests/brd/diary.feature"""
    # Photos alone leave the day's words: an update needs no words to carry a photo.
    photo_only = DiaryToolInput.model_validate(
        {"mode": "update", "date": DAY.isoformat(), "add_media": [3]}
    )
    assert photo_only.pov is None
    with pytest.raises(ValueError, match="pov, add_media, remove_media or rename_media"):
        DiaryToolInput.model_validate({"mode": "update", "date": DAY.isoformat()})
    with pytest.raises(ValueError, match="only mode and date"):
        DiaryToolInput.model_validate(
            {"mode": "delete", "date": DAY.isoformat(), "remove_media": [3]}
        )


async def test_di_photo_017_the_screen_names_the_photo_and_keeps_the_words(sessions) -> None:
    """DI-PHOTO-017 — tests/brd/diary.feature"""
    (cat,) = await keep_photos(sessions, "Кот на окне")
    async with sessions() as session:
        await create_diary_entry(session, entry_date=DAY, body="Утро.")
        await session.commit()

    change = await prepared(
        sessions,
        {"mode": "update", "date": DAY.isoformat(), "add_media": [cat]},
    )
    presenter = DiaryProposalPresenter()
    async with sessions() as session:
        screen = await presenter.screen(session, [change])
        details = await presenter.details(session, change, None)

    assert "📷 Кот на окне" in screen.blocks
    assert "Утро." in screen.blocks
    assert not any("replaces" in block for block in screen.blocks)
    assert details == ["Date: 2026-09-26", "Photo added: Кот на окне"]
    await saved(sessions, change)
    async with sessions() as session:
        assert (await diary_entry_for(session, DAY)).body == "Утро."
    assert await photos_on(sessions, DAY) == [(cat, "Кот на окне")]


async def test_di_photo_019_a_day_holds_photos_with_no_words_written_on_it(sessions) -> None:
    """DI-PHOTO-019 — tests/brd/diary.feature"""
    (park,) = await keep_photos(sessions, "Анна в парке")
    change = await prepared(
        sessions,
        {"mode": "update", "date": DAY.isoformat(), "add_media": [park]},
    )
    assert change.action is ChangeAction.CREATE
    await saved(sessions, change)
    async with sessions() as session:
        assert (await diary_entry_for(session, DAY)).body is None

    words = await prepared(
        sessions, {"mode": "update", "date": DAY.isoformat(), "pov": "Гуляли в парке."}
    )
    assert words.action is ChangeAction.UPDATE
    await saved(sessions, words)
    async with sessions() as session:
        assert (await diary_entry_for(session, DAY)).body == "Гуляли в парке."
    assert await photos_on(sessions, DAY) == [(park, "Анна в парке")]


async def test_di_photo_020_a_day_holds_at_most_ten_photos(sessions) -> None:
    """DI-PHOTO-020 — tests/brd/diary.feature"""
    assert DIARY_DAY_PHOTOS == TELEGRAM_ALBUM_LIMIT
    ids = await keep_photos(sessions, *(f"Фото {index}" for index in range(DIARY_DAY_PHOTOS + 1)))
    async with sessions() as session:
        entry = await create_diary_entry(
            session,
            entry_date=DAY,
            body=None,
            media=ids[:DIARY_DAY_PHOTOS],
        )
        await session.commit()

    with pytest.raises(ToolPreparationError) as refused:
        await prepared(
            sessions,
            {"mode": "update", "date": DAY.isoformat(), "add_media": [ids[-1]]},
        )
    assert refused.value.code == "day_full"
    assert refused.value.as_tool_result()["retryable"] is True
    # The operation holds the same line for whoever calls it.
    async with sessions() as session:
        with pytest.raises(DomainError, match=str(DIARY_DAY_PHOTOS)):
            await update_diary_entry(session, entry.id, None, add_media=[ids[-1]])


async def test_di_photo_020_a_photo_that_does_not_fit_is_refused_by_name(sessions) -> None:
    """DI-PHOTO-020 — tests/brd/diary.feature"""
    (cat,) = await keep_photos(sessions, "Кот")
    await saved(
        sessions,
        await prepared(
            sessions,
            {"mode": "update", "date": DAY.isoformat(), "add_media": [cat]},
        ),
    )
    refusals = (
        ("media_not_found", {"add_media": [99]}),
        ("media_already_on_day", {"add_media": [cat]}),
        ("media_not_on_day", {"remove_media": [99]}),
        ("media_not_on_day", {"rename_media": [{"media_id": 99, "meta": "Нет"}]}),
    )
    for code, arguments in refusals:
        with pytest.raises(ToolPreparationError) as refused:
            await prepared(sessions, {"mode": "update", "date": DAY.isoformat(), **arguments})
        assert refused.value.code == code


async def test_di_photo_021_a_photo_taken_off_leaves_the_rest_of_the_day(sessions) -> None:
    """DI-PHOTO-021 — tests/brd/diary.feature"""
    park, bench = await keep_photos(sessions, "Парк", "Скамейка")
    async with sessions() as session:
        await create_diary_entry(
            session, entry_date=DAY, body="День в парке.", media=[park, bench]
        )
        await session.commit()

    change = await prepared(
        sessions, {"mode": "update", "date": DAY.isoformat(), "remove_media": [bench]}
    )
    async with sessions() as session:
        screen = await DiaryProposalPresenter().screen(session, [change])
    assert "📷 <s>Скамейка</s>" in screen.blocks
    await saved(sessions, change)
    async with sessions() as session:
        assert (await diary_entry_for(session, DAY)).body == "День в парке."
    assert await photos_on(sessions, DAY) == [(park, "Парк")]

    other = date(2026, 9, 27)
    async with sessions() as session:
        await create_diary_entry(session, entry_date=other, body=None, media=[park])
        await session.commit()
    last = await prepared(
        sessions, {"mode": "update", "date": other.isoformat(), "remove_media": [park]}
    )
    assert last.action is ChangeAction.DELETE
    async with sessions() as session:
        screen = await DiaryProposalPresenter().screen(session, [last])
    assert screen.mode == "Remove"
    assert any("This removes that day's entry for good." in block for block in screen.blocks)
    assert "📷 <s>Парк</s>" in screen.blocks
    await saved(sessions, last)
    async with sessions() as session:
        assert await diary_entry_for(session, other) is None
        assert await session.get(ChatMedia, park) is not None


async def test_di_read_013_a_day_is_read_with_its_photos(sessions) -> None:
    """DI-READ-013 — tests/brd/diary.feature"""
    (cat,) = await keep_photos(sessions, "Рыжий кот")
    async with sessions() as session:
        await create_diary_entry(
            session, entry_date=DAY, body="День.", feeling_score=6, media=[cat]
        )
        await session.commit()
    tool = day_read_tool(sessions, timezone="UTC")

    read = await tool.run(ToolCall(id="1", name="read_day", arguments_json='{"date":"2026-09-26"}'))

    assert read["saved"] == {
        "body": "День.",
        "feeling_score": 6,
        "media": [f"[Рыжий кот](media:{cat})"],
    }


async def test_di_delete_005_a_deleted_day_takes_its_photos_with_it(sessions) -> None:
    """DI-DELETE-005 — tests/brd/diary.feature"""
    (cat,) = await keep_photos(sessions, "Кот")
    async with sessions() as session:
        entry = await create_diary_entry(
            session, entry_date=DAY, body="Есть что удалять.", feeling_score=6, media=[cat]
        )
        await session.commit()
        await delete_diary_entry(session, entry.id)
        await session.commit()
        assert await diary_entry_for(session, DAY) is None
        assert await day_media(session, entry.id) == []
        # The photo is the chat's: it is still there to be cited and opened.
        assert await session.get(ChatMedia, cat) is not None


async def test_di_photo_022_a_days_screen_shows_its_photos_above_its_words(sessions) -> None:
    """DI-PHOTO-022 — tests/brd/diary.feature"""
    ids = await keep_photos(sessions, "Парк", "Скамейка", "Утки")
    async with sessions() as session:
        entry = await create_diary_entry(
            session,
            entry_date=DAY,
            body="День в парке.",
            feeling_score=7,
            media=ids,
        )
        await session.commit()
    services = services_for(sessions)
    message = QueueTestMessage(is_bot=False, answer_as_new=True)

    await render_diary(message, services, entry.id, replace=False)

    bot = message.bot
    assert bot.photos_sent == [["file-1", "file-2", "file-3"]]
    assert bot.drawn[-2:] == ["[photo]", "<b>📔 26 сентября · 😊7</b>\n\nДень в парке."]
    album = [sent.message_id for sent in message.sent[:3]]

    await dismiss_prior_ui(QueueTestMessage(message_id=990, is_bot=False, parent=message), services)
    assert set(album) | {message.sent[3].message_id} <= set(bot.deleted)


async def test_di_photo_022_a_day_with_photos_alone_shows_its_date_under_them(sessions) -> None:
    """DI-PHOTO-022 — tests/brd/diary.feature"""
    (cat,) = await keep_photos(sessions, "Кот")
    async with sessions() as session:
        entry = await create_diary_entry(session, entry_date=DAY, body=None, media=[cat])
        await session.commit()
    message = QueueTestMessage(is_bot=False, answer_as_new=True)

    await render_diary(message, services_for(sessions), entry.id, replace=False)

    assert message.bot.drawn[-2:] == ["[photo]", "<b>📔 26 сентября</b>"]


def before(*calls: ProposedCall, reads: tuple[SessionRead, ...] = ()) -> BeforeProposals:
    return BeforeProposals(run_id=1, agent_kind="diary", text="Plan.", calls=calls, reads=reads)


def diary_call(**values: Any) -> ProposedCall:
    return ProposedCall(
        call_id="1", tool="diary", entity="diary", action="update", entity_id=None, values=values
    )


def day_read(day: str) -> SessionRead:
    return SessionRead(tool="read_day", result=json.dumps({"date": day, "saved": "..."}))


async def test_di_read_023_new_words_come_only_after_the_day_was_read() -> None:
    """DI-READ-023 — tests/brd/diary.feature"""
    words = diary_call(date="2026-09-26", pov="День в парке.")

    assert await unread_days(before(words)) == (DAY_NOT_READ.format(day="2026-09-26"),)
    assert await unread_days(before(words, reads=(day_read("2026-09-26"),))) == ()
    # Another day read, or a read that failed, is not this day read.
    failed = SessionRead(tool="read_day", result=json.dumps({"status": "error"}))
    assert await unread_days(before(words, reads=(day_read("2026-09-25"), failed))) != ()
    # Photos alone leave the words as they are.
    photo = diary_call(date="2026-09-26", add_media=[{"media_id": 1, "meta": "Кот"}])
    assert await unread_days(before(photo)) == ()


def test_a_day_of_photos_alone_reads_as_nothing_written_to_the_retro() -> None:
    tally = DayTally(day="2026-09-26", planned=0, done=0)
    assert "- Diary: nothing written" in days_text([(tally, DiaryDay(DAY, None, ""))])


async def test_di_photo_024_a_corrected_photo_is_called_by_the_new_words_everywhere(
    sessions,
) -> None:
    """DI-PHOTO-024 — tests/brd/diary.feature"""
    rename = PROPOSALS.tools["diary"].schema()["function"]["parameters"]["properties"]["rename_media"]
    meta = rename["anyOf"][0]["items"]["properties"]["meta"]
    assert f"At most {DESCRIPTION_MAX_WORDS} words" in meta["description"]
    (cat,) = await keep_photos(sessions, "Кот на окне")
    async with sessions() as session:
        await create_diary_entry(session, entry_date=DAY, body=None, media=[cat])
        await session.commit()

    change = await prepared(
        sessions,
        {
            "mode": "update",
            "date": DAY.isoformat(),
            "rename_media": [{"media_id": cat, "meta": "Рыжий кот на окне"}],
        },
    )
    presenter = DiaryProposalPresenter()
    async with sessions() as session:
        screen = await presenter.screen(session, [change])
        details = await presenter.details(session, change, None)
    assert "📷 <s>Кот на окне</s> Рыжий кот на окне" in screen.blocks
    assert details == ["Date: 2026-09-26", "Photo renamed: Кот на окне → Рыжий кот на окне"]

    await saved(sessions, change)
    assert await photos_on(sessions, DAY) == [(cat, "Рыжий кот на окне")]
    services = services_for(sessions)
    async with sessions() as session:
        linked = await render_citations(session, services, f"[Кот на окне](media:{cat})")
    assert ">Рыжий кот на окне</a>" in linked
    message = QueueTestMessage(is_bot=False, answer_as_new=True)
    await open_media(message, services, cat)
    assert message.bot.drawn[-1] == "Рыжий кот на окне"


async def test_the_diary_view_lists_a_days_photos(sessions) -> None:
    (cat,) = await keep_photos(sessions, "Рыжий кот")
    async with sessions() as session:
        await create_diary_entry(session, entry_date=DAY, body=None, media=[cat])
        await session.commit()
        view = (await session.execute(text("SELECT body, media FROM ai_diary"))).one()
    assert (view.body, view.media) == (None, f"[Рыжий кот](media:{cat})")
