"""A subagent finds the item the owner describes by searching for those words."""

from __future__ import annotations

import json
from datetime import date

import pytest
from agent_turns import mutation_turn, route_turn
from database_key import keyed
from scored_encoder import Scored
from test_subagent_e2e import diary_subagent, turn

from safwa.bootstrap.modules import AGENTS, AI_VIEWS, TEXT_MODEL, WORD_FORMS
from safwa.features.cards.use_cases import create_card
from safwa.features.diary.model import DiaryEntry
from tg_agent_shell.ai.outcome import AIOutcomeKind
from tg_agent_shell.search.index import SearchIndex

pytestmark = pytest.mark.e2e

CLOSEST = "SELECT id, {columns} FROM {view} WHERE relevance IS NOT NULL ORDER BY relevance DESC LIMIT 3"


async def meaning(harness, encoder: Scored) -> None:
    """The harness's index with a loaded text model, for every reader built after this."""
    harness.search = SearchIndex(
        keyed(harness.database_path), TEXT_MODEL, lambda: encoder, WORD_FORMS, AI_VIEWS
    )
    await harness.search.load()


def read_rows(provider, call: int) -> list[dict]:
    """What the read the session made just before model call `call` handed back."""
    return json.loads(
        next(message for message in reversed(provider.calls[call]) if message["role"] == "tool")[
            "content"
        ]
    )


def instructions(name: str) -> str:
    return next(agent for agent in AGENTS if agent.name == name).instructions


async def test_ws_find_009_the_workspace_finds_a_card_told_in_other_words(e2e_harness):
    """WS-FIND-009 — tests/brd/workspace_mutator.feature"""
    async with e2e_harness.sessions() as session:
        dentist = await create_card(
            session, kind="action", title="Записаться к стоматологу", effort_points=1
        )
        await create_card(session, kind="action", title="Купить молоко", effort_points=1)
        await session.commit()
    await meaning(e2e_harness, Scored("зубной врач", {"Записаться к стоматологу": 0.8}))
    closest = CLOSEST.format(columns="title", view="ai_cards")
    advisor, provider = e2e_harness.advisor(
        [
            route_turn("workspace_mutator"),
            turn(("query_data", {"search": "зубной врач", "sql": closest})),
            mutation_turn(("action", {"mode": "update", "id": dentist.id, "priority": "critical"})),
        ],
        subagents=(e2e_harness.subagent("workspace_mutator"),),
    )

    outcome = await advisor.handle("Сделай поход к зубному врачу срочным")

    assert read_rows(provider, 2) == [{"id": dentist.id, "title": "Записаться к стоматологу"}]
    assert outcome.kind is AIOutcomeKind.PROPOSAL
    [change] = e2e_harness.reviews.proposal(outcome.proposal_id).changes
    assert (change.entity, change.entity_id) == ("card", dentist.id)


async def test_ws_find_009_with_nothing_close_the_workspace_changes_nothing(e2e_harness):
    """WS-FIND-009 — tests/brd/workspace_mutator.feature"""
    async with e2e_harness.sessions() as session:
        await create_card(session, kind="action", title="Купить молоко", effort_points=1)
        await session.commit()
    await meaning(e2e_harness, Scored("велосипед", {}))
    reason = "Ни одна Card не про велосипед."
    advisor, provider = e2e_harness.advisor(
        [
            route_turn("workspace_mutator"),
            turn(("query_data", {"search": "велосипед", "sql": CLOSEST.format(columns="title", view="ai_cards")})),
            turn(("nothing_to_do", {"reason": reason})),
            "Я не нашла такую Card.",
        ],
        subagents=(e2e_harness.subagent("workspace_mutator"),),
    )

    outcome = await advisor.handle("Отметь починку велосипеда сделанной")

    assert read_rows(provider, 2) == []
    assert outcome.kind is AIOutcomeKind.ANSWER
    assert outcome.proposal_id is None
    assert "Nothing close: call nothing_to_do" in instructions("workspace_mutator")


async def test_di_find_025_the_diary_finds_a_day_by_what_happened_on_it(e2e_harness):
    """DI-FIND-025 — tests/brd/diary.feature"""
    sea, work = date(2026, 3, 8), date(2026, 3, 9)
    async with e2e_harness.sessions() as session:
        sea_day = DiaryEntry(entry_date=sea, body="Ездили на море, купались весь день.")
        session.add_all([sea_day, DiaryEntry(entry_date=work, body="Весь день работал над отчётом.")])
        await session.commit()
    await meaning(e2e_harness, Scored("поездка к морю", {"Ездили на море, купались весь день.": 0.8}))
    closest = CLOSEST.format(columns="entry_date", view="ai_diary")
    advisor, provider = e2e_harness.advisor(
        [
            route_turn("diary"),
            turn(("query_data", {"search": "поездка к морю", "sql": closest})),
            turn(("read_day", {"date": sea.isoformat()})),
            mutation_turn(
                (
                    "diary",
                    {
                        "mode": "update",
                        "date": sea.isoformat(),
                        "body": "Ездили на море, купались весь день. Вечером ели рыбу.",
                    },
                )
            ),
        ],
        subagents=(diary_subagent(e2e_harness),),
    )

    outcome = await advisor.handle("Допиши в день, когда мы ездили на море, что вечером ели рыбу")

    [found] = read_rows(provider, 2)
    assert found["entry_date"] == sea.isoformat()
    assert outcome.kind is AIOutcomeKind.PROPOSAL
    [change] = e2e_harness.reviews.proposal(outcome.proposal_id).changes
    assert (change.entity, change.entity_id) == ("diary", sea_day.id)
    assert "No day is close: call nothing_to_do" in instructions("diary").replace("\n   ", " ")
