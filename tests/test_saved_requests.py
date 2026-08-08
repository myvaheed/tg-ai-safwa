from __future__ import annotations

from sqlalchemy import select

from safwa.domain import DomainError, create_saved_request
from safwa.models import Card, CardTag, SavedRequest, Tag
from safwa.saved_requests import request_cards_statement


async def test_saved_request_combines_action_tag_and_nested_stage_filters(sessions):
    async with sessions() as session:
        family = Tag(name="Family")
        work = Tag(name="Work")
        family_today = Card(
            kind="action",
            title="Call family",
            manual_stage="today",
            effective_stage="today",
            priority="medium",
            effort_points=1,
        )
        family_backlog = Card(
            kind="action",
            title="Plan family trip",
            manual_stage="backlog",
            effective_stage="backlog",
            priority="medium",
            effort_points=3,
        )
        work_today = Card(
            kind="action",
            title="Work meeting",
            manual_stage="today",
            effective_stage="today",
            priority="medium",
            effort_points=1,
        )
        session.add_all([family, work, family_today, family_backlog, work_today])
        await session.flush()
        session.add_all(
            [
                CardTag(card_id=family_today.id, tag_id=family.id),
                CardTag(card_id=family_backlog.id, tag_id=family.id),
                CardTag(card_id=work_today.id, tag_id=work.id),
            ]
        )
        request = await create_saved_request(
            session,
            "Family actions to work on",
            {
                "all": [
                    {"field": "kind", "op": "eq", "value": "action"},
                    {"field": "tag_id", "op": "any_of", "value": [family.id]},
                    {
                        "any": [
                            {"field": "stage", "op": "eq", "value": "today"},
                            {"field": "stage", "op": "eq", "value": "backlog"},
                        ]
                    },
                ]
            },
            "Family tasks that are live.",
        )
        await session.commit()

    async with sessions() as session:
        stored = await session.get(SavedRequest, request.id)
        cards = list(
            await session.scalars(request_cards_statement(stored.filter_spec).order_by(Card.title))
        )
        assert [card.title for card in cards] == ["Call family", "Plan family trip"]


async def test_saved_request_rejects_unknown_or_unsafe_filter_fields(sessions):
    async with sessions() as session:
        try:
            await create_saved_request(
                session,
                "Unsafe request",
                {"all": [{"field": "sql", "op": "contains", "value": "DROP"}]},
            )
        except DomainError as error:
            assert "Unknown filter field" in str(error)
        else:
            raise AssertionError("Unsafe request filters must be rejected")
        assert list(await session.scalars(select(SavedRequest))) == []
