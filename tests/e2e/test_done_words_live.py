"""How the configured model marks an Action or a Check the owner says they did.

Opt-in with ``--live-provider``, as `test_series_goal_live.py` is, and run from the folder that
holds `.env` and `data/models`. Every turn is the live model, reasoning off: the Advisor and the
mutator. The workspace holds two Today Actions, two repeating Actions and three daily trackers.
Each case is the owner's words with no word of a change, one of them a reply to a Reminder that
asked about its tracker; what is compared is that the proposal marks that one item: the Action
complete, the Check Passed.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

import pytest
from database_key import keyed
from schedule_helpers import create_card, create_check

from llm_gateway import OpenAICompatibleProvider
from safwa.bootstrap.modules import AI_VIEWS, TEXT_MODEL, WORD_FORMS
from safwa.qa import qa_provider_settings
from telegram_llm import DialogueMessage
from tg_agent_shell.proposals.api import ChangeAction
from tg_agent_shell.search.index import SearchIndex
from tg_agent_shell.search.words import FastEmbedEncoder

pytestmark = [pytest.mark.e2e, pytest.mark.live_provider]

REASONING_EFFORT = "none"


@dataclass(frozen=True)
class Done:
    words: str
    title: str
    # A Reminder asked about the item just before, as Safwa says it.
    asked: bool = False


DONE = (
    Done("Позвонил маме, всё хорошо.", "Позвонить маме"),
    Done("Пробежался сегодня, 5 км.", "Пробежка"),
    Done("Помедитировал утром минут десять.", "Помедитировал?"),
    Done("Just got back with the groceries.", "Buy groceries"),
    Done("I read for 20 minutes tonight.", "Read 20 minutes?"),
    Done("Сделал!", "Сделал зарядку?", asked=True),
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


async def _workspace(harness) -> dict[str, tuple[str, int]]:
    """Every item by its title, as the entity a change to it names and its id."""
    items: dict[str, tuple[str, int]] = {}
    async with harness.sessions() as session:
        for title, stage, schedule in (
            ("Позвонить маме", "today", None),
            ("Buy groceries", "today", None),
            ("Пробежка", "backlog", "Mon Wed Fri 07:00"),
            ("Силовая тренировка", "backlog", "Tue Thu 19:00"),
        ):
            card = await create_card(
                session, kind="action", title=title, stage=stage, schedule=schedule
            )
            items[title] = ("card", card.id)
        for title, schedule in (
            ("Помедитировал?", "daily 07:00"),
            ("Сделал зарядку?", "daily 08:00"),
            ("Read 20 minutes?", "daily 21:00"),
        ):
            check = await create_check(session, title=title, schedule=schedule)
            items[title] = ("check", check.id)
        await session.commit()
    return items


@pytest.mark.parametrize(
    "case", [pytest.param(case, id=f"did-{number:02d}") for number, case in enumerate(DONE, start=1)]
)
async def test_the_model_marks_what_the_owner_did(settings, encoder, e2e_harness, case) -> None:
    """AD-DONE-008 — tests/brd/advisor.feature"""
    items = await _workspace(e2e_harness)
    e2e_harness.search = SearchIndex(
        keyed(e2e_harness.database_path), TEXT_MODEL, lambda: encoder, WORD_FORMS, AI_VIEWS
    )
    await e2e_harness.search.load()
    entity, item_id = items[case.title]
    dialogue = [DialogueMessage(role="user", content=case.words)]
    if case.asked:
        dialogue.insert(
            0,
            DialogueMessage(
                role="assistant", content=f"Пора: [{case.title}]({entity}:{item_id}) Как прошло?"
            ),
        )
    provider = Live(OpenAICompatibleProvider(settings))
    try:
        advisor, _ = e2e_harness.advisor([], provider_factory=lambda _: provider)
        outcome = await advisor.handle(case.words, dialogue=dialogue)
    finally:
        await provider.aclose()
    changes = advisor.reviews.proposal(outcome.proposal_id).changes if outcome.proposal_id else []
    marked = [(change.entity, change.action, change.entity_id) for change in changes]
    print(f"{case.words!r}: said {outcome.message!r}")
    assert marked == [(entity, ChangeAction.COMPLETE, item_id)], f"{case.words!r} proposed {marked}"
