"""How the configured model reviews the method's pairs: one item, clear or unclear.

Opt-in with ``--live-provider``, as `test_timing_phrases_live.py` is, and run from the folder
that holds `.env`. Each case is one review session, run as its hook runs it, reasoning off.
The cases are the pairs of the method's review (docs/REMINDERS_TRACKING_REVIEW.md §7.7), each
in Russian and in English; what is compared is whether the review answered clear.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

import pytest

from llm_gateway import OpenAICompatibleProvider
from safwa.features.cards.action_review import ActionReviewer
from safwa.features.cards.goal_review import GoalReviewer
from safwa.features.checks.check_review import CheckReviewer
from safwa.features.values.link_review import ValueLinkReviewer
from safwa.features.values.value_review import ValueReviewer
from safwa.qa import qa_provider_settings

pytestmark = pytest.mark.live_provider

REASONING_EFFORT = "none"


@dataclass(frozen=True)
class Case:
    review: str
    item: dict[str, Any]
    clear: bool


def check(title: str, schedule: str = "") -> dict[str, Any]:
    return {"title": title, "note": "", "schedule": schedule, "card": ""}


def goal(title: str, note: str = "") -> dict[str, Any]:
    return {"kind": "Goal", "title": title, "note": note, "deadline": "", "parents": []}


def action(title: str, schedule: str, categories: list[str], energy: list[str]) -> dict[str, Any]:
    return {
        "title": title,
        "note": "",
        "schedule": schedule,
        "goals": [],
        "categories": categories,
        "energy_types": energy,
    }


def link(kind: str, title: str, value: str, description: str, note: str = "") -> dict[str, Any]:
    return {
        "kind": kind,
        "title": title,
        "note": note,
        "value": value,
        "value_description": description,
    }


CASES = (
    Case("check", check("Сделал утреннюю зарядку?", "каждый день в 07:00"), True),
    Case("check", check("Did my morning exercises?", "every morning"), True),
    Case("check", check("Читал 20 минут?", "каждый вечер"), True),
    Case("check", check("Ran today?", "every day"), True),
    Case("check", check("Бегал сегодня?", "каждый день"), True),
    # A repeating Check needs no Remind, Value or last day.
    Case("check", check("Выпил стакан воды утром?", "каждый день в 08:00"), True),
    Case("check", check("Был хорошим?"), False),
    Case("check", check("Was I a good friend today?", "every evening"), False),
    Case("check", check("Сколько минут читал?", "каждый вечер"), False),
    Case(
        "value",
        {
            "name": "Здоровье",
            "description": "Высыпаюсь, двигаюсь каждый день, пью воду по утрам и готовлю дома.",
        },
        True,
    ),
    Case(
        "value",
        {"name": "Health", "description": "I sleep 7 hours, walk every day and cook at home."},
        True,
    ),
    Case("value", {"name": "Пить воду по утрам", "description": "Стакан воды после пробуждения."}, False),
    Case("value", {"name": "Drink water in the morning", "description": "A glass after I wake up."}, False),
    Case("goal", goal("Медитировать ежедневно"), False),
    Case("goal", goal("Meditate every day"), False),
    Case(
        "goal",
        goal(
            "30 утр подряд с медитацией",
            "Готово, когда отмечу медитацию 30 запланированных утр подряд.",
        ),
        True,
    ),
    Case(
        "goal",
        goal("Meditation streak", "Done when I mark meditation on 30 scheduled mornings in a row."),
        True,
    ),
    # Repeating work is not a reason to make it a Check.
    Case("action", action("Пробежка", "каждый день в 07:00", ["rest"], ["physical"]), True),
    Case("action", action("Morning run", "every day at 07:00", ["rest"], ["physical"]), True),
    Case("action", action("Читать 20 минут", "каждый день в 21:00", ["growth"], ["cognitive"]), True),
    Case(
        "link",
        link("check", "Выпил воду утром?", "Здоровье", "Высыпаюсь, двигаюсь, пью воду по утрам."),
        True,
    ),
    Case(
        "link",
        link("check", "Drank water in the morning?", "Health", "I sleep well, move and drink water."),
        True,
    ),
    Case(
        "link",
        link(
            "goal",
            "Заработать 300 тысяч",
            "Здоровье",
            "Высыпаюсь, двигаюсь, пью воду по утрам.",
            note="Мне нужно здоровье, чтобы заработать.",
        ),
        False,
    ),
)


class NoDialogue:
    """The conversation before an Action, which none of these cases has."""

    async def dialogue(self, _chat_id: int) -> list[Any]:
        return []


async def _clear(provider, case: Case) -> tuple[bool, str | None]:
    """Whether the review answered clear, and the advice it gave otherwise."""
    if case.review == "link":
        return await ValueLinkReviewer(provider).clear(case.item), None
    if case.review == "action":
        advice = await ActionReviewer(provider, NoDialogue(), 0, None).advice(case.item, [])  # type: ignore[arg-type]
    else:
        reviewer = {"check": CheckReviewer, "value": ValueReviewer, "goal": GoalReviewer}[case.review]
        advice = await reviewer(provider).advice(case.item)
    return advice is None, advice


@pytest.fixture
async def provider():
    provider = OpenAICompatibleProvider(
        replace(qa_provider_settings().ai_config(), reasoning_effort=REASONING_EFFORT)
    )
    try:
        yield provider
    finally:
        await provider.aclose()


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(case, id=f"{case.review}-{number:02d}")
        for number, case in enumerate(CASES, start=1)
    ],
)
async def test_the_review_reads_the_methods_pair(provider, case):
    clear, advice = await _clear(provider, case)
    print(f"{case.review} {case.item}: clear={clear} {advice or ''}")
    assert clear is case.clear, f"{case.item} was read as clear={clear}: {advice}"
