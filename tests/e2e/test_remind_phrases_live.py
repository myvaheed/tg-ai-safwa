"""How the configured model sets Remind on an item the owner names, and on a new one.

Opt-in with ``--live-provider``, as `test_timing_phrases_live.py` is, and run from the folder
that holds `.env` and `data/models`. Only the Advisor's hand-over to the reminder subagent is
scripted: the subagent is the live model, reasoning off, and its search reads the bot's own text
model. The workspace holds the seven items the phrases name. What is compared is the item and
the minutes of the last `remind` call; none is a miss. The phrases were written by someone who
never read the prompts, so a pass is a reading, not a copy.

A new item asked for with Remind goes the whole way live, the Advisor too, over an empty
workspace: what is compared is the `remind` of the last call of the item's own tool.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path

import pytest
from agent_turns import route_turn
from database_key import keyed
from schedule_helpers import create_card, create_check

from llm_gateway import OpenAICompatibleProvider
from safwa.bootstrap.modules import AI_VIEWS, PROPOSALS, TEXT_MODEL, WORD_FORMS
from safwa.features.schedules.use_cases import set_remind
from safwa.qa import qa_provider_settings
from tg_agent_shell.ai.outcome import AIOutcome, AIOutcomeKind
from tg_agent_shell.search.index import SearchIndex
from tg_agent_shell.search.words import FastEmbedEncoder

pytestmark = [pytest.mark.e2e, pytest.mark.live_provider]

REASONING_EFFORT = "none"
# The item, its Schedule as `rule_for` reads it, and the Remind it already has.
ITEMS = (
    ("action", "Растяжка", "daily 20:00", None),
    ("check", "Took pills?", "daily 09:00", None),
    ("goal", "Отчёт сдан руководителю", "25.11.2026 18:00", None),
    ("action", "Call mom", "Sun 19:00", None),
    ("action", "Йога", "Tue Thu 07:30", None),
    ("action", "Пробежка", "daily 07:00", None),
    ("action", "Читать", "daily 21:00", 15),
)


@dataclass(frozen=True)
class Lead:
    words: str
    title: str
    minutes: int | None


LEADS = (
    Lead("Напоминай про пробежку за 10 минут до начала", "Пробежка", 10),
    Lead("Can you ping me when it's time for my pills?", "Took pills?", 0),
    Lead("По цели «Отчёт сдан руководителю» напомни за сутки до дедлайна", "Отчёт сдан руководителю", 1440),
    Lead("I always forget, so give me a heads-up two hours ahead of Call mom", "Call mom", 120),
    Lead("Хочу, чтобы про йогу приходило напоминание за полтора часа", "Йога", 90),
    Lead("Notify me the moment Растяжка starts", "Растяжка", 0),
    Lead("Про чтение напоминай не за 15 минут, а за полчаса", "Читать", 30),
    Lead("Turn off the reminder on Читать, I don't need it anymore", "Читать", None),
    Lead("Warn me 3 hours before the report is due", "Отчёт сдан руководителю", 180),
    Lead("Звонок маме — напоминай за 45 минут, а то забываю", "Call mom", 45),
)


@dataclass(frozen=True)
class NewItem:
    words: str
    tool: str
    minutes: int | None


# The first six are the phrases the workspace mutator was measured on while `remind` was a
# field of the item's own tool, before Reminders had their own subagent.
NEW_ITEMS = (
    NewItem("Добавь Action «Растяжка» каждый день в 20:00 и напомни за 30 минут", "action", 30),
    NewItem("Create a Check 'Took pills?' every day at 9 and remind me 10 minutes before", "check", 10),
    NewItem("Цель «Отчёт сдан руководителю» к 25 ноября 18:00, напомни за 2 часа", "goal", 120),
    NewItem("Action 'Call mom' on Sunday at 19:00, remind me when it is time", "action", 0),
    NewItem("Добавь Action «Йога» по вторникам и четвергам в 7:30, напомни за день", "action", 1440),
    NewItem("Добавь Action «Читать» каждый день в 21:00", "action", None),
    NewItem("Create a Goal 'Marathon' by 1 May 2027", "goal", None),
)
# Remind with no time to remind at: the owner is asked for one, and nothing is proposed.
NO_TIME = "Создай Action «Позвонить в банк» и напомни мне о нём"


@pytest.fixture(scope="session")
def settings():
    # Read before the harness moves into the worktree, which holds no `.env`.
    return replace(qa_provider_settings().ai_config(), reasoning_effort=REASONING_EFFORT)


@pytest.fixture(scope="session")
def encoder():
    # The cache lives beside `.env`, which the harness leaves for the worktree.
    return FastEmbedEncoder(TEXT_MODEL.name, Path("data/models").resolve())


class HandedOver:
    """The Advisor's first turn routes to `route`; every later one is live. With no `route`,
    the Advisor's first turn is live as well."""

    def __init__(self, live: OpenAICompatibleProvider, route: str | None = "reminder") -> None:
        self.live = live
        self.route = route
        self.routed = route is None
        self.tool_calls = []

    async def complete(self, request):
        if not self.routed:
            self.routed = True
            return route_turn(self.route)
        turn = await self.live.complete(replace(request, reasoning_effort=REASONING_EFFORT))
        self.tool_calls.extend(turn.tool_calls)
        for call in turn.tool_calls:
            print("  call:", call.name, call.arguments_json[:200])
        return turn

    async def aclose(self) -> None:
        await self.live.aclose()


async def _workspace(harness) -> dict[str, tuple[str, int]]:
    """Each title with the `item` and the id a `remind` call on it names."""
    named = {}
    async with harness.sessions() as session:
        for item, title, schedule, lead in ITEMS:
            if item == "check":
                made = await create_check(session, title=title, schedule=schedule)
            else:
                made = await create_card(session, kind=item, title=title, schedule=schedule)
            if lead is not None:
                await set_remind(session, made, lead)
            named[title] = (item, made.id)
        await session.commit()
    return named


@pytest.mark.parametrize(
    "case", [pytest.param(case, id=f"lead-{number:02d}") for number, case in enumerate(LEADS, start=1)]
)
async def test_the_model_sets_remind(settings, encoder, e2e_harness, case) -> None:
    """SCH-REMIND-029 — tests/brd/schedules.feature"""
    named = await _workspace(e2e_harness)
    e2e_harness.search = SearchIndex(
        keyed(e2e_harness.database_path), TEXT_MODEL, lambda: encoder, WORD_FORMS, AI_VIEWS
    )
    await e2e_harness.search.load()
    provider = HandedOver(OpenAICompatibleProvider(settings))
    try:
        advisor, _ = e2e_harness.advisor([], provider_factory=lambda _: provider)
        await advisor.handle(case.words)
    finally:
        await provider.aclose()
    sent = [json.loads(call.arguments_json) for call in provider.tool_calls if call.name == "remind"]
    print(f"{case.words!r} was read as {sent}")
    item, item_id = named[case.title]
    assert sent, f"{case.words!r} made no remind call"
    # A Goal and an Action are both a Card: either word names the same change.
    changed = PROPOSALS.change_from_tool("remind", sent[-1])
    expected = PROPOSALS.change_from_tool(
        "remind", {"item": item, "id": item_id, "minutes_before": case.minutes}
    )
    assert (changed.entity, changed.id, changed.values) == (
        expected.entity,
        expected.id,
        expected.values,
    )


async def _ask_live(settings, e2e_harness, words: str) -> tuple[HandedOver, AIOutcome]:
    provider = HandedOver(OpenAICompatibleProvider(settings), route=None)
    try:
        advisor, _ = e2e_harness.advisor([], provider_factory=lambda _: provider)
        outcome = await advisor.handle(words)
    finally:
        await provider.aclose()
    return provider, outcome


@pytest.mark.parametrize(
    "case", [pytest.param(case, id=f"new-{number:02d}") for number, case in enumerate(NEW_ITEMS, start=1)]
)
async def test_the_model_proposes_a_new_item_with_its_remind(settings, e2e_harness, case) -> None:
    """SCH-REMIND-029 — tests/brd/schedules.feature"""
    provider, _ = await _ask_live(settings, e2e_harness, case.words)
    sent = [json.loads(call.arguments_json) for call in provider.tool_calls if call.name == case.tool]
    print(f"{case.words!r} was read as {sent}")
    assert sent, f"{case.words!r} made no {case.tool} call"
    assert sent[-1].get("remind") == case.minutes


async def test_the_model_asks_when_a_new_item_to_remind_of_has_no_time(settings, e2e_harness) -> None:
    """SCH-REMIND-029 — tests/brd/schedules.feature"""
    _, outcome = await _ask_live(settings, e2e_harness, NO_TIME)
    print(f"{NO_TIME!r} came back as {outcome.kind}: {outcome.message!r}")
    assert outcome.kind is AIOutcomeKind.ANSWER
