from __future__ import annotations

import pytest
from advisor_e2e_helpers import mutation_turn, route_turn
from sqlalchemy import select

from safwa.bootstrap.modules import ALLOWED_VIEWS, PROPOSALS
from safwa.constants import INBOX_TAG_NAME
from safwa.features.cards.model import Card, CardCategory, CardEnergyType
from safwa.features.cards.use_cases import create_card, edit_card_text, toggle_card_tag
from safwa.features.profile.model import ProfileField
from safwa.features.profile.use_cases import set_profile_field
from safwa.features.saved_requests.api import request_cards
from safwa.features.saved_requests.model import SavedRequest
from safwa.features.saved_requests.use_cases import seed_inbox_request
from safwa.features.tags.model import CardTag, Tag
from safwa.features.tags.use_cases import seed_inbox_tag
from tg_agent_shell.ai.outcome import AIOutcomeKind
from tg_agent_shell.proposals.use_cases import approve_proposal

pytestmark = pytest.mark.e2e


async def test_a_capture_request_reaches_save_with_only_the_thought_and_inbox_tag(e2e_harness):
    """AD-CAPTURE-006 — tests/brd/advisor.feature"""
    async with e2e_harness.sessions() as session:
        await seed_inbox_tag(session)
        await seed_inbox_request(session, views=ALLOWED_VIEWS)
        await set_profile_field(session, ProfileField.EFFORT_TRACKING, True)
        ordinary = await create_card(session, kind="action", title="Подготовить черновик договора")
        await session.commit()
        ordinary_id = ordinary.id

    thought = "Попробовать другой формат ретро. Пока не знаю какой."
    advisor, provider = e2e_harness.advisor(
        [
            route_turn("workspace_mutator"),
            mutation_turn(
                (
                    "action",
                    {
                        "mode": "create",
                        "title": "Разобрать: формат ретро",
                        "note": thought,
                        "tags": INBOX_TAG_NAME,
                    },
                )
            ),
        ]
    )
    outcome = await advisor.handle(f"Inbox: {thought}")
    assert outcome.kind is AIOutcomeKind.PROPOSAL
    assert len(provider.calls) == 2

    async with e2e_harness.sessions() as session:
        assert list(await session.scalars(select(Card.id))) == [ordinary_id]
        affected = await approve_proposal(session, advisor.reviews, PROPOSALS, outcome.proposal_id)
        await session.commit()
        card = await session.get(Card, affected[0])
        tag = await session.scalar(select(Tag).where(Tag.name == INBOX_TAG_NAME))
        assert card.note == thought and card.kind == "action"
        assert card.effective_stage == "backlog"
        assert card.effort_points is None and card.schedule is None and card.parent_id is None
        assert await session.get(CardTag, (card.id, tag.id)) is not None
        assert await session.get(CardTag, (ordinary_id, tag.id)) is None
        assert list(await session.scalars(select(CardCategory))) == []
        assert list(await session.scalars(select(CardEnergyType))) == []
        request = await session.scalar(select(SavedRequest).where(SavedRequest.name == "Inbox"))
        assert [item.id for item in await request_cards(session, request.query_sql, ALLOWED_VIEWS)] == [card.id]


async def test_an_ordinary_edit_keeps_capture_and_explicit_unlink_resolves_it(e2e_harness):
    """WS-CAPTURE-008 — tests/brd/workspace_mutator.feature"""
    async with e2e_harness.sessions() as session:
        await seed_inbox_tag(session)
        await seed_inbox_request(session, views=ALLOWED_VIEWS)
        tag = await session.scalar(select(Tag).where(Tag.name == INBOX_TAG_NAME))
        card = await create_card(session, kind="action", title="Разобрать: идея", note="Исходная мысль")
        await toggle_card_tag(session, card.id, tag.id)
        await session.commit()
        card_id, tag_id = card.id, tag.id

        await edit_card_text(session, card.id, "note", "Уточнённая мысль")
        await session.commit()
        assert await session.get(CardTag, (card_id, tag_id)) is not None

    advisor, _provider = e2e_harness.advisor(
        [
            route_turn("workspace_mutator"),
            mutation_turn(
                ("action", {"mode": "unlink", "id": card_id, "tags": INBOX_TAG_NAME})
            ),
        ]
    )
    outcome = await advisor.handle("Эту мысль разобрали, сними тег Inbox.")
    assert outcome.kind is AIOutcomeKind.PROPOSAL

    async with e2e_harness.sessions() as session:
        await approve_proposal(session, advisor.reviews, PROPOSALS, outcome.proposal_id)
        await session.commit()
        assert await session.get(CardTag, (card_id, tag_id)) is None
        assert await session.get(Tag, tag_id) is not None
        card = await session.get(Card, card_id)
        assert card.note == "Уточнённая мысль" and card.effort_points is None
        request = await session.scalar(select(SavedRequest).where(SavedRequest.name == "Inbox"))
        assert await request_cards(session, request.query_sql, ALLOWED_VIEWS) == []
