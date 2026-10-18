"""How the configured model fills `remind` when the owner asks to be reminded.

Opt-in with ``--live-provider``, as `test_timing_phrases_live.py` is, and run from the folder
that holds `.env`. Only the Advisor's hand-over to the workspace mutator is scripted: the
mutator, and the Scheduler that reads its Schedule while the proposal is prepared, are the live
model, reasoning off. What is compared is the `remind` the model sent on its last action, goal
or check call, and an absent one is None.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from agent_turns import route_turn
from schedule_helpers import rule_for

from llm_gateway import OpenAICompatibleProvider
from safwa.features.cards.use_cases import create_card
from safwa.qa import qa_provider_settings

pytestmark = [pytest.mark.e2e, pytest.mark.live_provider]

REASONING_EFFORT = "none"
# The phrase, the tool it is for and the minutes it asks for. The numbers are none the
# `remind` description names, so a pass is a reading, not a copy.
CASES = (
    ("Добавь Action «Растяжка» каждый день в 20:00 и напомни за 30 минут", "action", 30),
    ("Create a Check 'Took pills?' every day at 9 and remind me 10 minutes before", "check", 10),
    ("Цель «Отчёт сдан руководителю» к 25 ноября 18:00, напомни за 2 часа", "goal", 120),
    ("Action 'Call mom' on Sunday at 19:00, remind me when it is time", "action", 0),
    ("Добавь Action «Йога» по вторникам и четвергам в 7:30, напомни за день", "action", 1440),
    ("Напоминай мне о «Пробежке» за 45 минут", "action", 45),
    ("Добавь Action «Читать» каждый день в 21:00", "action", None),
)


@pytest.fixture(scope="session")
def settings():
    # Read before the harness moves into the worktree, which holds no `.env`.
    return replace(qa_provider_settings().ai_config(), reasoning_effort=REASONING_EFFORT)


class HandedOver:
    """The Advisor's first turn routes to the mutator; every later one is the live model."""

    def __init__(self, live: OpenAICompatibleProvider) -> None:
        self.live = live
        self.routed = False
        self.tool_calls = []

    async def complete(self, request):
        if not self.routed:
            self.routed = True
            return route_turn("workspace_mutator")
        turn = await self.live.complete(replace(request, reasoning_effort=REASONING_EFFORT))
        self.tool_calls.extend(turn.tool_calls)
        for call in turn.tool_calls:
            print("  call:", call.name, call.arguments_json[:200])
        return turn

    async def aclose(self) -> None:
        await self.live.aclose()


@pytest.mark.parametrize(
    ("phrase", "tool", "remind"), CASES, ids=[f"{tool}-{remind}" for _, tool, remind in CASES]
)
async def test_the_model_fills_remind(settings, e2e_harness, phrase, tool, remind) -> None:
    """SCH-REMIND-029 — tests/brd/schedules.feature"""
    async with e2e_harness.sessions() as session:
        await create_card(
            session,
            kind="action",
            title="Пробежка",
            schedule="каждый день в 7:00",
            schedule_rule=await rule_for(session, "daily 07:00"),
        )
        await session.commit()
    provider = HandedOver(OpenAICompatibleProvider(settings))
    try:
        advisor, _ = e2e_harness.advisor([], provider_factory=lambda _: provider)
        await advisor.handle(phrase)
    finally:
        await provider.aclose()
    sent = [json.loads(call.arguments_json) for call in provider.tool_calls if call.name == tool]
    print(f"{phrase!r} was read as {sent}")
    assert sent and sent[-1].get("remind") == remind
