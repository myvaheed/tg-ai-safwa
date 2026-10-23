"""How the configured model sets a Reminder on answers from the owner's own words.

Opt-in with ``--live-provider``, as `test_also_track_live.py` is, and run from the folder that
holds `.env` and `data/models`. Every turn is the live model, reasoning off: the Advisor, the
mutator and the Reminder's setup session; search reads the bot's own text model, so the owner's
words find a tracker named otherwise. The workspace holds four daily trackers and three Actions. What is
compared is the condition of the Reminder proposed, with the titles of the items its SELECT
finds; a request too vague to set proposes no Reminder. The requests were written by someone
who never read the prompts, so a pass is a reading, not a copy.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest
from database_key import keyed
from schedule_helpers import create_card, create_check
from sqlalchemy import select

from llm_gateway import OpenAICompatibleProvider
from safwa.bootstrap.modules import AI_VIEWS, TEXT_MODEL, WORD_FORMS
from safwa.features.cards.model import Card
from safwa.features.checks.model import Check
from safwa.features.checks.use_cases import toggle_check_value
from safwa.features.reminders.api import schedule_from_payload
from safwa.features.reminders.conditions import found_ids, one_series_select
from safwa.features.tags.use_cases import create_tag
from safwa.features.values.use_cases import create_value
from safwa.qa import qa_provider_settings
from tg_agent_shell.search.index import SearchIndex
from tg_agent_shell.search.words import FastEmbedEncoder

pytestmark = [pytest.mark.e2e, pytest.mark.live_provider]

REASONING_EFFORT = "none"
ASKS = {"asks": True}
# The answer a condition keeps, by what its items are, in the words the cases were written in.
DOMAIN_ANSWERS = {
    ("check", "yes"): "passed",
    ("check", "no"): "missed",
    ("check", "any"): "answered",
    ("action", "yes"): "done",
}


@dataclass(frozen=True)
class Ask:
    words: str
    expected: dict[str, Any]


ASKED = (
    Ask(
        "Когда я 7 утр подряд буду медитировать, поздравь меня и предложи добавить пять минут.",
        {"titles": ["Помедитировал?"], "answer": "passed", "count": 7, "in_row": True, "once": True},
    ),
    Ask(
        "Если забью на зарядку два дня подряд, каждый раз спрашивай, что помешало.",
        {"titles": ["Сделал зарядку?"], "answer": "missed", "count": 2, "in_row": True, "once": False},
    ),
    Ask(
        "Tell me when I've managed Read 20 minutes 30 times, and suggest a new book to start.",
        {"titles": ["Read 20 minutes?"], "answer": "passed", "count": 30, "in_row": False, "once": True},
    ),
    Ask(
        "Когда наберу 20 тренировок по спорту, всех вместе, поздравь меня и предложи новую цель.",
        {
            "titles": ["Пробежка", "Силовая тренировка"],
            "answer": "done",
            "count": 20,
            "in_row": False,
            "once": True,
        },
    ),
    Ask(
        "После каждых 10 пробежек напоминай проверить, не пора ли менять кроссовки.",
        {"titles": ["Пробежка"], "answer": "done", "count": 10, "in_row": False, "once": False},
    ),
    Ask("Поздравь меня, когда наберу хорошую серию.", ASKS),
    Ask(
        "Whenever I stay up past 11 three nights in a row, ask me what kept me up.",
        {"titles": ["Лёг до 23?"], "answer": "missed", "count": 3, "in_row": True, "once": False},
    ),
    Ask(
        "Когда медитация и зарядка вместе наберут 50 раз, поздравь меня — это будет рекорд.",
        {
            "titles": ["Помедитировал?", "Сделал зарядку?"],
            "answer": "passed",
            "count": 50,
            "in_row": False,
            "once": True,
        },
    ),
    Ask(
        "Как только 14 раз отмечу, лёг ли до 23, неважно с каким ответом, спроси, не сдвинуть ли "
        "время отбоя.",
        {"titles": ["Лёг до 23?"], "answer": "answered", "count": 14, "in_row": False, "once": True},
    ),
    Ask("Let me know when I'm doing well with my health habits.", ASKS),
)


@pytest.fixture(scope="session")
def settings():
    # Read before the harness moves into the worktree, which holds no `.env`.
    return replace(qa_provider_settings().ai_config(), reasoning_effort=REASONING_EFFORT)


@pytest.fixture(scope="session")
def encoder():
    # The cache lives beside `.env`, which the harness leaves for the worktree.
    return FastEmbedEncoder(TEXT_MODEL.name, Path("data/models").resolve())


class Live:
    """The live model with reasoning off, printing every call it makes."""

    def __init__(self, live: OpenAICompatibleProvider) -> None:
        self.live = live

    async def complete(self, request):
        turn = await self.live.complete(replace(request, reasoning_effort=REASONING_EFFORT))
        for call in turn.tool_calls:
            print("  call:", call.name, call.arguments_json[:300])
        return turn

    async def aclose(self) -> None:
        await self.live.aclose()


async def _workspace(harness) -> None:
    async with harness.sessions() as session:
        health = await create_value(session, "Здоровье", "Тело, сон и спокойная голова.")
        sport = await create_tag(session, "Sport")
        for title, schedule, healthy in (
            ("Помедитировал?", "daily 07:00", True),
            ("Сделал зарядку?", "daily 08:00", True),
            ("Read 20 minutes?", "daily 21:00", False),
            ("Лёг до 23?", "daily 22:30", False),
        ):
            check = await create_check(session, title=title, schedule=schedule)
            if healthy:
                await toggle_check_value(session, check.id, health.id)
        await create_card(
            session, kind="action", title="Пробежка", schedule="Mon Wed Fri 07:00", tag_ids={sport.id}
        )
        await create_card(
            session, kind="action", title="Силовая тренировка", schedule="Tue Thu 19:00",
            tag_ids={sport.id},
        )
        await create_card(session, kind="action", title="Позвонить маме")
        await session.commit()


@pytest.mark.parametrize(
    "case", [pytest.param(case, id=f"ask-{number:02d}") for number, case in enumerate(ASKED, start=1)]
)
async def test_the_model_sets_a_reminder_on_answers(settings, encoder, e2e_harness, case) -> None:
    """RM-COND-040 — tests/brd/reminders.feature"""
    await _workspace(e2e_harness)
    e2e_harness.search = SearchIndex(
        keyed(e2e_harness.database_path), TEXT_MODEL, lambda: encoder, WORD_FORMS, AI_VIEWS
    )
    await e2e_harness.search.load()
    provider = Live(OpenAICompatibleProvider(settings))
    try:
        advisor, _ = e2e_harness.advisor([], provider_factory=lambda _: provider)
        outcome = await advisor.handle(case.words)
    finally:
        await provider.aclose()
    changes = advisor.reviews.proposal(outcome.proposal_id).changes if outcome.proposal_id else []
    reminders = [change for change in changes if change.entity == "reminder"]
    print(f"{case.words!r}: said {outcome.message!r}")
    if case.expected == ASKS:
        assert not reminders, f"{case.words!r} proposed {[r.values for r in reminders]}"
        return
    assert reminders, f"{case.words!r} proposed {[c.entity for c in changes]}"
    condition = schedule_from_payload(reminders[-1].values["schedule"]).condition
    assert condition is not None, f"{case.words!r} was read as {reminders[-1].values['schedule_text']!r}"
    model = Card if condition.kind == "action" else Check
    async with e2e_harness.sessions() as session:
        ids = await found_ids(session, condition)
        titles = sorted(set(await session.scalars(select(model.title).where(model.id.in_(ids)))))
        one_item = await one_series_select(session, condition.items) is not None
    reading = {
        "titles": titles,
        "answer": DOMAIN_ANSWERS[condition.kind, condition.answer],
        "count": condition.count,
        "in_row": condition.in_row,
        "once": condition.once,
    }
    print(f"{case.words!r}: items {condition.items!r}, one_item {one_item}, {reading}")
    assert reading == case.expected, f"{case.words!r} was read as {reading}"
