"""How the configured model sets Remind on an item the owner names.

Opt-in with ``--live-provider``, as `test_timing_phrases_live.py` is, and run from the folder
that holds `.env` and `data/models`. Only the Advisor's hand-over to the reminder subagent is
scripted: the subagent is the live model, reasoning off, and its search reads the bot's own text
model. The workspace holds the seven items the phrases name. What is compared is the item and
the minutes of the last `remind` call; none is a miss. The phrases were written by someone who
never read the prompts, so a pass is a reading, not a copy.
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
from safwa.bootstrap.modules import AI_VIEWS, TEXT_MODEL, WORD_FORMS
from safwa.features.schedules.use_cases import set_remind
from safwa.qa import qa_provider_settings
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


@pytest.fixture(scope="session")
def settings():
    # Read before the harness moves into the worktree, which holds no `.env`.
    return replace(qa_provider_settings().ai_config(), reasoning_effort=REASONING_EFFORT)


@pytest.fixture(scope="session")
def encoder():
    # The cache lives beside `.env`, which the harness leaves for the worktree.
    return FastEmbedEncoder(TEXT_MODEL.name, Path("data/models").resolve())


class HandedOver:
    """The Advisor's first turn routes to the reminder subagent; every later one is live."""

    def __init__(self, live: OpenAICompatibleProvider) -> None:
        self.live = live
        self.routed = False
        self.tool_calls = []

    async def complete(self, request):
        if not self.routed:
            self.routed = True
            return route_turn("reminder")
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
    assert (sent[-1]["item"], sent[-1]["id"], sent[-1].get("minutes_before")) == (
        item,
        item_id,
        case.minutes,
    )
