"""A photo through the Advisor and the Diary, on a real database; only the model is scripted."""

from __future__ import annotations

import json
from datetime import date

import pytest
from agent_turns import PLAN
from sqlalchemy import select
from test_subagent_e2e import diary_subagent

from llm_gateway import CompletionTurn as ProviderTurn
from llm_gateway import ToolCall as ProviderToolCall
from safwa.bootstrap.modules import PROPOSALS
from safwa.features.diary.api import day_media
from safwa.features.diary.hooks import DAY_NOT_READ
from safwa.features.diary.model import DiaryEntry
from safwa.features.diary.use_cases import DIARY_DAY_PHOTOS, create_diary_entry
from tg_agent_shell.ai.outcome import AIOutcomeKind
from tg_agent_shell.ai.runs import AgentRun
from tg_agent_shell.media.library import RELOOK_INSTRUCTIONS, MediaLibrary, Photo
from tg_agent_shell.proposals.use_cases import approve_proposal

pytestmark = pytest.mark.e2e

TODAY = date.today().isoformat()


def turn(*calls: tuple[str, dict[str, object]]) -> ProviderTurn:
    return ProviderTurn(
        content=PLAN,
        tool_calls=tuple(
            ProviderToolCall(id=f"call-{index}", name=name, arguments_json=json.dumps(arguments))
            for index, (name, arguments) in enumerate(calls, start=1)
        ),
    )


async def keep_photos(harness, *metas: str) -> list[int]:
    return await MediaLibrary(harness.sessions, None).keep(  # type: ignore[arg-type]
        [
            (Photo(f"jpeg-{index}".encode(), 1280, 960, f"file-{index}"), meta)
            for index, meta in enumerate(metas, start=1)
        ]
    )


async def save(harness, advisor, proposal_id: int):
    async with harness.sessions() as session:
        description = await advisor.describe_proposal(session, proposal_id)
        await approve_proposal(session, advisor.reviews, PROPOSALS, proposal_id)
        await session.commit()
    return description


async def the_day(harness) -> tuple[str | None, list[tuple[int, str]]]:
    async with harness.sessions() as session:
        entry = await session.scalar(select(DiaryEntry))
        return entry.body, await day_media(session, entry.id)


def tool_results(call: list[dict[str, object]]) -> list[dict[str, object]]:
    return [json.loads(str(item["content"])) for item in call if item.get("role") == "tool"]


async def test_di_photo_017_a_photo_with_no_words_is_put_up_for_today(e2e_harness):
    """DI-PHOTO-017 — tests/brd/diary.feature"""
    (cat,) = await keep_photos(e2e_harness, "Кот на окне")
    async with e2e_harness.sessions() as session:
        await create_diary_entry(session, entry_date=date.today(), body="Утро.")
        await session.commit()
    advisor, _ = e2e_harness.advisor(
        [
            turn(("route", {"name": "diary"})),
            # Photos alone leave the words, so nothing asks for the day to be read first.
            turn(
                (
                    "diary",
                    {"mode": "update", "date": TODAY, "add_media": [cat]},
                )
            ),
        ],
        subagents=(diary_subagent(e2e_harness),),
    )

    outcome = await advisor.handle(f"[Кот на окне](media:{cat})")

    assert outcome.kind is AIOutcomeKind.PROPOSAL
    assert await the_day(e2e_harness) == ("Утро.", [])
    description = await save(e2e_harness, advisor, outcome.proposal_id)
    assert "Photo added: Кот на окне" in description.fields
    assert await the_day(e2e_harness) == ("Утро.", [(cat, "Кот на окне")])


async def test_di_photo_018_words_sent_with_a_photo_reach_the_days_words(e2e_harness):
    """DI-PHOTO-018 — tests/brd/diary.feature"""
    (park,) = await keep_photos(e2e_harness, "Анна с Лейлой в парке")
    caption = "Отличный день в парке с Лейлой"
    advisor, _ = e2e_harness.advisor(
        [
            turn(("route", {"name": "diary"})),
            turn(("read_day", {"date": TODAY})),
            turn(
                (
                    "diary",
                    {
                        "mode": "update",
                        "date": TODAY,
                        "pov": "Отличный день: гуляли с Лейлой в парке.",
                        "feeling_score": 8,
                        "add_media": [park],
                    },
                )
            ),
        ],
        subagents=(
            diary_subagent(e2e_harness, f"[12:10] [User]: [Анна с Лейлой в парке](media:{park}) {caption}"),
        ),
    )

    outcome = await advisor.handle(f"[Анна с Лейлой в парке](media:{park}) {caption}")

    assert outcome.kind is AIOutcomeKind.PROPOSAL
    description = await save(e2e_harness, advisor, outcome.proposal_id)
    assert "Photo added: Анна с Лейлой в парке" in description.fields
    assert await the_day(e2e_harness) == (
        "Отличный день: гуляли с Лейлой в парке.",
        [(park, "Анна с Лейлой в парке")],
    )


async def test_di_photo_020_safwa_carries_on_from_a_full_day(e2e_harness):
    """DI-PHOTO-020 — tests/brd/diary.feature"""
    *full, extra = await keep_photos(
        e2e_harness, *(f"Фото {index}" for index in range(DIARY_DAY_PHOTOS + 1))
    )
    async with e2e_harness.sessions() as session:
        await create_diary_entry(
            session, entry_date=date.today(), body=None, media=full
        )
        await session.commit()
    advisor, provider = e2e_harness.advisor(
        [
            turn(("route", {"name": "diary"})),
            turn(
                (
                    "diary",
                    {"mode": "update", "date": TODAY, "add_media": [extra]},
                )
            ),
            "В этом дне уже 10 фото.",
            "В этом дне уже 10 фото: уберите одно, и я добавлю новое.",
        ],
        subagents=(diary_subagent(e2e_harness),),
    )

    outcome = await advisor.handle(f"[Ещё](media:{extra})")

    assert outcome.kind is AIOutcomeKind.ANSWER
    (refused,) = tool_results(provider.calls[2])
    assert (refused["code"], refused["retryable"]) == ("day_full", True)


async def test_di_photo_024_the_owner_corrects_what_a_photo_on_a_day_is_called(e2e_harness):
    """DI-PHOTO-024 — tests/brd/diary.feature"""
    (sons,) = await keep_photos(e2e_harness, "Мукхаммет и сын в музее")
    async with e2e_harness.sessions() as session:
        await create_diary_entry(session, entry_date=date.today(), body=None, media=[sons])
        await session.commit()
    advisor, _ = e2e_harness.advisor(
        [
            turn(("route", {"name": "diary"})),
            turn(("read_day", {"date": TODAY})),
            turn(
                (
                    "diary",
                    {
                        "mode": "update",
                        "date": TODAY,
                        "rename_media": [{"media_id": sons, "meta": "Мухаммет с двумя сыновьями"}],
                    },
                )
            ),
        ],
        subagents=(diary_subagent(e2e_harness),),
    )

    outcome = await advisor.handle("Не Мукхаммет, а Мухаммет, и там два моих сына")

    assert outcome.kind is AIOutcomeKind.PROPOSAL
    description = await save(e2e_harness, advisor, outcome.proposal_id)
    assert (
        "Photo renamed: Мукхаммет и сын в музее → Мухаммет с двумя сыновьями" in description.fields
    )
    assert await the_day(e2e_harness) == (None, [(sons, "Мухаммет с двумя сыновьями")])


async def test_di_read_023_words_for_an_unread_day_go_back_to_the_diary(e2e_harness):
    """DI-READ-023 — tests/brd/diary.feature"""
    words = {"mode": "update", "date": TODAY, "pov": "Долгий день на рынке."}
    advisor, provider = e2e_harness.advisor(
        [
            turn(("route", {"name": "diary"})),
            turn(("diary", words)),
            turn(("read_day", {"date": TODAY})),
            turn(("diary", words)),
        ],
        subagents=(diary_subagent(e2e_harness),),
    )

    outcome = await advisor.handle("Запиши день")

    (sent_back,) = tool_results(provider.calls[2])
    assert sent_back["code"] == "day_not_read"
    assert sent_back["error"] == DAY_NOT_READ.format(day=TODAY)
    assert outcome.kind is AIOutcomeKind.PROPOSAL
    async with e2e_harness.sessions() as session:
        assert await session.scalar(select(DiaryEntry)) is None


async def test_ad_photo_005_a_question_about_a_photo_is_answered_by_looking_again(e2e_harness):
    """AD-PHOTO-005 — tests/brd/advisor.feature"""
    (cat,) = await keep_photos(e2e_harness, "Кот на окне")
    advisor, provider = e2e_harness.advisor(
        [
            turn(("relook", {"media_id": cat, "question": "Какого цвета кот?"})),
            "Рыжий",
            "Кот рыжий.",
        ],
        subagents=(diary_subagent(e2e_harness),),
        images=True,
    )

    outcome = await advisor.handle(f"[Кот на окне](media:{cat}) Какого цвета кот?")

    system = str(provider.calls[0][0]["content"])
    assert 'A photo alone, or a photo with words about their day: `route("diary")`' in system
    assert "relook" in [tool["function"]["name"] for tool in provider.options[0]["tools"]]
    look = provider.calls[1]
    assert look[0]["content"] == RELOOK_INSTRUCTIONS
    assert any(
        part.get("type") == "image_url" for part in look[1]["content"] if isinstance(part, dict)
    )
    assert "Рыжий" in json.dumps(provider.calls[2], ensure_ascii=False)
    assert outcome.message == "Кот рыжий."
    async with e2e_harness.sessions() as session:
        assert await session.scalar(select(AgentRun).where(AgentRun.kind == "diary")) is None


async def test_ad_photo_005_without_images_the_advisor_cannot_look_again(e2e_harness):
    """AD-PHOTO-005 — tests/brd/advisor.feature"""
    advisor, provider = e2e_harness.advisor(["Не вижу фото."])

    await advisor.handle("Какого цвета кот?")

    assert "relook" not in [tool["function"]["name"] for tool in provider.options[0]["tools"]]
