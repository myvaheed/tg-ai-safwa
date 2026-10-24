"""How the configured model reads the words of a Reminder on answers, in their own short session.

Opt-in with ``--live-provider``; it reads SAFWA_QA_AI_* over SAFWA_AI_* from `.env`, so run
it from the folder that holds `.env`. Reasoning is off, as Safwa runs it. A case is the owner's
words and what the items are; what is compared is what the code made of the session's answer,
never the answer itself.

The cases were written by someone who never read the prompts, so a pass is a reading, not a
copy: the first 20 are the `goal` phrases of `tests/test_timing_phrases_live.py` that count
answers, the last 10 are new. Whether the subagent passes the owner's words whole is measured
by `tests/e2e/test_series_goal_live.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

import pytest

from llm_gateway import OpenAICompatibleProvider
from safwa.features.reminders.agent import ConditionUnclear, read_condition
from safwa.qa import qa_provider_settings

pytestmark = pytest.mark.live_provider

REASONING_EFFORT = "none"
UNCLEAR = {"unclear": True}


@dataclass(frozen=True)
class Case:
    words: str
    kind: str
    expected: dict[str, Any]


def fields(answer: str, count: int, *, in_row: bool = False, once: bool = False) -> dict[str, Any]:
    return {"answer": answer, "count": count, "in_row": in_row, "once": once}


CASES = (
    Case("#14 «Помедитировал?» — когда дойду до 30 дней подряд", "check", fields("yes", 30, in_row=True, once=True)),
    Case("если пропущу #21 три дня подряд", "check", fields("no", 3, in_row=True)),
    Case("каждые 10 выполненных Действий с тегом Sport", "action", fields("yes", 10)),
    Case("после 10 выполненных Действий с тегом Sport", "action", fields("yes", 10, once=True)),
    Case("как только по Проверкам ценности Health будет 100 «да»", "check", fields("yes", 100, once=True)),
    Case(
        "каждый раз, когда отвечаю на любую Проверку из Действия #8 «Подготовка к марафону»",
        "check",
        fields("any", 1),
    ),
    Case("на каждый пятый пропуск любой Проверки", "check", fields("no", 5)),
    Case("после 20 выполнений #5 и #9 в сумме", "action", fields("yes", 20, once=True)),
    Case("всякий раз как сделаю #33", "action", fields("yes", 1)),
    Case("после 50 ответов на #11 без разницы да или нет", "check", fields("any", 50, once=True)),
    Case("когда по #21 накопится много пропусков", "check", UNCLEAR),
    Case("as soon as I hit a 21-day streak on #14", "check", fields("yes", 21, in_row=True, once=True)),
    Case("if I skip “Morning stretch?” #17 five mornings in a row", "check", fields("no", 5, in_row=True)),
    Case("for every 10 yeses on Checks tagged Reading", "check", fields("yes", 10)),
    Case("when my Health checks have racked up 15 misses", "check", fields("no", 15, once=True)),
    Case("whenever any check gets answered, yes or no", "check", fields("any", 1)),
    Case("once the Checks under #8 have been passed 40 times", "check", fields("yes", 40, once=True)),
    Case("every 4 misses — #14, #17, #21 all counted together", "check", fields("no", 4)),
    Case("on the 100th time I finish #33", "action", fields("yes", 100, once=True)),
    Case("after I've finished a dozen Reading actions", "action", fields("yes", 12, once=True)),
    Case(
        "как только наберу 14 дней подряд с «да» по #7 «Без сахара?»",
        "check",
        fields("yes", 14, in_row=True, once=True),
    ),
    Case("всякий раз, когда два дня подряд отвечаю «нет» на #19", "check", fields("no", 2, in_row=True)),
    Case("на каждое 25-е «да» по Проверкам с тегом Сон", "check", fields("yes", 25)),
    Case("каждый раз, как пропускаю #4 «Зарядка?»", "check", fields("no", 1)),
    Case("дай знать, если я слишком часто пропускаю вечерние Проверки", "check", UNCLEAR),
    Case("если трижды не выполню Действие #12 «Позвонить маме»", "action", UNCLEAR),
    Case("ping me after 35 answers on #6, whatever they are", "check", fields("any", 35, once=True)),
    Case("when I've completed 75 workout actions, let me know", "action", fields("yes", 75, once=True)),
    Case("once #2 gets its 60th yes, tell me", "check", fields("yes", 60, once=True)),
    Case("after every 15 misses on my Sleep checks", "check", fields("no", 15)),
)


async def _read(provider, case: Case) -> dict[str, Any]:
    try:
        read = await read_condition(provider, words=case.words, kind=case.kind, instruction="Cheer me on.")
    except ConditionUnclear as error:
        return {"unclear": True, "reason": str(error)}
    return read.model_dump()


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
    "case", CASES, ids=[f"cond-{number:02d}" for number in range(1, len(CASES) + 1)]
)
async def test_the_model_reads_what_a_reminder_on_answers_counts(provider, case):
    reading = await _read(provider, case)
    assert {key: reading.get(key) for key in case.expected} == case.expected, (
        f"{case.words!r} was read as {reading}"
    )
