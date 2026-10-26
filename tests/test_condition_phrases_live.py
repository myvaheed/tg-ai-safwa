"""How the configured model reads a Reminder on answers, in its own short session.

Opt-in with ``--live-provider``; it reads SAFWA_QA_AI_* over SAFWA_AI_* from `.env`, so run
it from the folder that holds `.env`. Reasoning is off, as Safwa runs it. A case is the owner's
words: the owner said only them, so the conversation is one line of theirs, and every case
reads the same Open items. What is compared is what the code made of the session's answer,
never the answer itself.

The cases were written by someone who never read the prompts, so a pass is a reading, not a
copy: the first 20 are the `goal` phrases of `tests/test_timing_phrases_live.py` that count
answers, the last 10 are new. A Check with its own Schedule never hangs on an Action, so the
Checks under Action #8 are in no Open items and no item fits them. Whether the subagent sends
the owner's words here is measured by `tests/e2e/test_series_goal_live.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

import pytest

from llm_gateway import OpenAICompatibleProvider
from safwa.features.reminders.agent import ConditionUnclear, read_condition
from safwa.features.reminders.conditions import named_items
from safwa.qa import qa_provider_settings
from tg_agent_shell.ai.conversation import conversation_block

pytestmark = pytest.mark.live_provider

REASONING_EFFORT = "none"
UNCLEAR = {"unclear": True}

OPEN = """Open items:
- Check #2 «Выпил 2 литра воды?»; schedule: daily 20:00; values: Health
- Check #4 «Зарядка?»; schedule: daily 08:00; values: Health
- Check #6 «Прочитал 20 страниц?»; schedule: daily 22:00; values: Reading
- Check #7 «Без сахара?»; schedule: daily 21:00; values: Health
- Check #11 «Записал день в дневник?»; schedule: daily 23:00
- Check #14 «Помедитировал?»; schedule: daily 21:00; values: Health
- Check #17 «Morning stretch?»; schedule: daily 07:30; values: Health
- Check #19 «Лёг до 23?»; schedule: daily 23:00; values: Sleep
- Check #21 «Без телефона после 22?»; schedule: daily 22:30; values: Sleep
- Action #5 «Пробежка 5 км»; schedule: Mon Wed Fri 07:00; tags: Sport; values: Health
- Action #8 «Подготовка к марафону»; schedule: Sat 08:00; values: Health
- Action #9 «Силовая тренировка»; schedule: Tue Thu 19:00; tags: Sport
- Action #12 «Позвонить маме»; schedule: Sun 12:00
- Action #33 «Прочитать главу»; schedule: daily 21:30; tags: Reading"""

ALL_CHECKS = (2, 4, 6, 7, 11, 14, 17, 19, 21)
HEALTH_CHECKS = (2, 4, 7, 14, 17)
SLEEP_CHECKS = (19, 21)
SPORT_ACTIONS = (5, 9)


@dataclass(frozen=True)
class Case:
    words: str
    expected: dict[str, Any]


def check(*ids: int) -> tuple[str, frozenset[int]]:
    return "check", frozenset(ids)


def action(*ids: int) -> tuple[str, frozenset[int]]:
    return "action", frozenset(ids)


def fields(
    items: tuple[str, frozenset[int]],
    answer: str,
    count: int,
    *,
    in_row: bool = False,
    once: bool = False,
) -> dict[str, Any]:
    return {"items": items, "answer": answer, "count": count, "in_row": in_row, "once": once}


CASES = (
    Case(
        "#14 «Помедитировал?» — когда дойду до 30 дней подряд",
        fields(check(14), "yes", 30, in_row=True, once=True),
    ),
    Case("если пропущу #21 три дня подряд", fields(check(21), "no", 3, in_row=True)),
    Case("каждые 10 выполненных Действий с тегом Sport", fields(action(*SPORT_ACTIONS), "yes", 10)),
    Case(
        "после 10 выполненных Действий с тегом Sport",
        fields(action(*SPORT_ACTIONS), "yes", 10, once=True),
    ),
    Case(
        "как только по Проверкам ценности Health будет 100 «да»",
        fields(check(*HEALTH_CHECKS), "yes", 100, once=True),
    ),
    Case(
        "каждый раз, когда отвечаю на любую Проверку из Действия #8 «Подготовка к марафону»",
        UNCLEAR,
    ),
    Case("на каждый пятый пропуск любой Проверки", fields(check(*ALL_CHECKS), "no", 5)),
    Case("после 20 выполнений #5 и #9 в сумме", fields(action(5, 9), "yes", 20, once=True)),
    Case("всякий раз как сделаю #33", fields(action(33), "yes", 1)),
    Case("после 50 ответов на #11 без разницы да или нет", fields(check(11), "any", 50, once=True)),
    Case("когда по #21 накопится много пропусков", UNCLEAR),
    Case(
        "as soon as I hit a 21-day streak on #14",
        fields(check(14), "yes", 21, in_row=True, once=True),
    ),
    Case(
        "if I skip “Morning stretch?” #17 five mornings in a row",
        fields(check(17), "no", 5, in_row=True),
    ),
    Case("for every 10 yeses on Checks tagged Reading", fields(check(6), "yes", 10)),
    Case(
        "when my Health checks have racked up 15 misses",
        fields(check(*HEALTH_CHECKS), "no", 15, once=True),
    ),
    Case("whenever any check gets answered, yes or no", fields(check(*ALL_CHECKS), "any", 1)),
    Case("once the Checks under #8 have been passed 40 times", UNCLEAR),
    Case("every 4 misses — #14, #17, #21 all counted together", fields(check(14, 17, 21), "no", 4)),
    Case("on the 100th time I finish #33", fields(action(33), "yes", 100, once=True)),
    Case("after I've finished a dozen Reading actions", fields(action(33), "yes", 12, once=True)),
    Case(
        "как только наберу 14 дней подряд с «да» по #7 «Без сахара?»",
        fields(check(7), "yes", 14, in_row=True, once=True),
    ),
    Case(
        "всякий раз, когда два дня подряд отвечаю «нет» на #19",
        fields(check(19), "no", 2, in_row=True),
    ),
    Case("на каждое 25-е «да» по Проверкам с тегом Сон", fields(check(*SLEEP_CHECKS), "yes", 25)),
    Case("каждый раз, как пропускаю #4 «Зарядка?»", fields(check(4), "no", 1)),
    Case("дай знать, если я слишком часто пропускаю вечерние Проверки", UNCLEAR),
    Case("если трижды не выполню Действие #12 «Позвонить маме»", UNCLEAR),
    Case(
        "ping me after 35 answers on #6, whatever they are", fields(check(6), "any", 35, once=True)
    ),
    Case(
        "when I've completed 75 workout actions, let me know",
        fields(action(*SPORT_ACTIONS), "yes", 75, once=True),
    ),
    Case("once #2 gets its 60th yes, tell me", fields(check(2), "yes", 60, once=True)),
    Case("after every 15 misses on my Sleep checks", fields(check(*SLEEP_CHECKS), "no", 15)),
)


async def _read(provider, case: Case) -> dict[str, Any]:
    try:
        read = await read_condition(
            provider,
            open_items=OPEN,
            conversation=conversation_block([{"role": "user", "content": case.words}]),
        )
    except ConditionUnclear as error:
        return {"unclear": True, "reason": str(error)}
    kind, ids = named_items(read.items)
    return {**read.model_dump(), "items": (kind, frozenset(ids))}


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
