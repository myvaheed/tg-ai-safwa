from __future__ import annotations

import pytest
from advisor_e2e_helpers import mutation_turn, route_turn
from sqlalchemy import select

from safwa.bootstrap.modules import PROPOSALS
from safwa.features.cards.hierarchy import card_parent_ids
from safwa.features.cards.model import Card, CardCategory, CardEnergyType
from safwa.features.cards.use_cases import create_card, edit_card_text
from safwa.features.profile.model import ProfileField
from safwa.features.profile.use_cases import set_profile_field
from safwa.features.tags.model import CardTag
from tg_agent_shell.ai.outcome import AIOutcomeKind
from tg_agent_shell.proposals.use_cases import approve_proposal

pytestmark = pytest.mark.e2e


async def test_a_capture_request_reaches_save_with_only_the_thought(e2e_harness):
    """AD-CAPTURE-006 — tests/brd/advisor.feature"""
    async with e2e_harness.sessions() as session:
        await set_profile_field(session, ProfileField.EFFORT_TRACKING, True)
        ordinary = await create_card(session, kind="action", title="Подготовить черновик договора")
        await session.commit()
        ordinary_id = ordinary.id
    thought = "Попробовать другой формат ретро. Пока не знаю какой."
    advisor, provider = e2e_harness.advisor([
        route_turn("workspace_mutator"),
        mutation_turn(("thought", {"mode": "create", "title": "Формат ретро", "note": thought})),
    ])
    outcome = await advisor.handle(f"Запиши мысль: {thought}")
    assert outcome.kind is AIOutcomeKind.PROPOSAL and len(provider.calls) == 2
    async with e2e_harness.sessions() as session:
        assert list(await session.scalars(select(Card.id))) == [ordinary_id]
        affected = await approve_proposal(session, advisor.reviews, PROPOSALS, outcome.proposal_id)
        await session.commit()
        card = await session.get(Card, affected[0])
        assert card.note == thought and card.kind == "thought"
        assert card.effective_stage is card.manual_stage is card.priority is None
        assert card.effort_points is card.schedule is None
        assert await card_parent_ids(session, card.id) == []
        assert list(await session.scalars(select(CardTag))) == []
        assert list(await session.scalars(select(CardCategory))) == []
        assert list(await session.scalars(select(CardEnergyType))) == []


async def test_an_ordinary_edit_keeps_the_thought_in_its_selection(e2e_harness):
    """WS-CAPTURE-008 — tests/brd/workspace_mutator.feature"""
    async with e2e_harness.sessions() as session:
        card = await create_card(session, kind="thought", title="Идея", note="Исходная мысль")
        await session.commit()
        card_id = card.id
        await edit_card_text(session, card.id, "note", "Уточнённая мысль")
        await session.commit()
    advisor, _ = e2e_harness.advisor([
        route_turn("workspace_mutator"),
        mutation_turn(("thought", {"mode": "update", "id": card_id, "title": "Уточнённая идея"})),
    ])
    outcome = await advisor.handle("Переименуй эту мысль в Уточнённая идея.")
    async with e2e_harness.sessions() as session:
        await approve_proposal(session, advisor.reviews, PROPOSALS, outcome.proposal_id)
        await session.commit()
        card = await session.get(Card, card_id)
        assert card.note == "Уточнённая мысль" and card.kind == "thought"
        assert card.title == "Уточнённая идея"
