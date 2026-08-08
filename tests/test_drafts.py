from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from safwa.ai.contracts import AgentChange
from safwa.ai.service import AIAdvisor
from safwa.drafts import DraftService
from safwa.enums import CardKind, CardStage, DraftStatus
from safwa.models import Board, Card, CardDraft
from safwa.recovery import recover_startup


async def test_draft_is_isolated_until_review_and_commit(sessions):
    async with sessions() as session:
        inbox = await session.scalar(select(Board).where(Board.name == "Inbox"))
        bundle = await DraftService(session).create_bundle(
            "ai",
            [
                {
                    "title": "Push ups 30 times",
                    "kind": CardKind.ACTION.value,
                    "board_id": inbox.id,
                    "expected_board_version": inbox.version,
                    "root_confirmed": True,
                    "stage": CardStage.BACKLOG.value,
                    "effort_points": None,
                }
            ],
        )
        await session.commit()

    async with sessions() as session:
        assert await session.scalar(select(func.count(Card.id))) == 0
        draft = await session.get(CardDraft, bundle.active_draft_id)
        assert draft.status == DraftStatus.EDITING.value
        assert "effort" in " ".join(draft.validation_errors).lower()
        await DraftService(session).update(draft.id, effort_points=2)
        await DraftService(session).mark_reviewed(draft.id)
        cards = await DraftService(session).commit_bundle(bundle.id)
        await session.commit()
        assert cards[0].title == "Push ups 30 times"

    async with sessions() as session:
        assert await session.scalar(select(func.count(Card.id))) == 1
        draft = await session.get(CardDraft, bundle.active_draft_id)
        assert draft.status == DraftStatus.COMMITTED.value


async def test_batch_draft_to_draft_parent_commits_atomically(sessions):
    async with sessions() as session:
        inbox = await session.scalar(select(Board).where(Board.name == "Inbox"))
        bundle = await DraftService(session).create_bundle(
            "ai",
            [
                {
                    "title": "Be fit",
                    "kind": "goal",
                    "board_id": inbox.id,
                    "expected_board_version": inbox.version,
                    "root_confirmed": True,
                    "draft_ref": "goal-1",
                },
                {
                    "title": "Push ups",
                    "kind": "action",
                    "board_id": inbox.id,
                    "expected_board_version": inbox.version,
                    "root_confirmed": False,
                    "effort_points": 2,
                    "draft_ref": "action-1",
                    "parent_draft_ref": "goal-1",
                },
            ],
        )
        drafts = await DraftService(session).get_bundle_drafts(bundle.id)
        for draft in drafts:
            await DraftService(session).mark_reviewed(draft.id)
        cards = await DraftService(session).commit_bundle(bundle.id)
        await session.commit()
        goal = next(card for card in cards if card.kind == "goal")
        action = next(card for card in cards if card.kind == "action")
        assert action.parent_id == goal.id


async def test_unresolved_parent_blocks_ai_draft(sessions):
    async with sessions() as session:
        inbox = await session.scalar(select(Board).where(Board.name == "Inbox"))
        bundle = await DraftService(session).create_bundle(
            "ai",
            [
                {
                    "title": "Push ups",
                    "kind": "action",
                    "board_id": inbox.id,
                    "expected_board_version": inbox.version,
                    "root_confirmed": False,
                    "effort_points": 2,
                    "field_provenance": {"parent_query": "To be fit"},
                }
            ],
        )
        draft = await session.get(CardDraft, bundle.active_draft_id)
        assert any("parent" in error.lower() for error in draft.validation_errors)


async def test_ai_pushups_example_resolves_unique_goal_but_requires_effort(sessions):
    async with sessions() as session:
        inbox = await session.scalar(select(Board).where(Board.name == "Inbox"))
        goal_bundle = await DraftService(session).create_bundle(
            "manual",
            [
                {
                    "title": "To be fit",
                    "kind": "goal",
                    "board_id": inbox.id,
                    "expected_board_version": inbox.version,
                    "root_confirmed": True,
                }
            ],
        )
        goal_draft = await session.get(CardDraft, goal_bundle.active_draft_id)
        await DraftService(session).mark_reviewed(goal_draft.id)
        goal = (await DraftService(session).commit_bundle(goal_bundle.id))[0]
        advisor = object.__new__(AIAdvisor)
        payload = await advisor._resolve_card_draft(
            session,
            AgentChange(
                entity="card",
                action="create",
                values={
                    "kind": "action",
                    "title": "Push ups 30 times",
                    "parent_query": "To be fit",
                    "effort_points": None,
                },
            ),
        )
        bundle = await DraftService(session).create_bundle("ai", [payload])
        draft = await session.get(CardDraft, bundle.active_draft_id)
        assert draft.parent_id == goal.id
        assert draft.board_id == goal.board_id
        assert any("effort" in error.lower() for error in draft.validation_errors)
        assert await session.scalar(select(func.count(Card.id))) == 1


async def test_startup_marks_inactive_expired_drafts_recoverable_but_uncommittable(sessions):
    async with sessions() as session:
        inbox = await session.scalar(select(Board).where(Board.name == "Inbox"))
        bundle = await DraftService(session).create_bundle(
            "manual",
            [
                {
                    "title": "Expired action",
                    "kind": CardKind.ACTION.value,
                    "board_id": inbox.id,
                    "expected_board_version": inbox.version,
                    "root_confirmed": True,
                    "effort_points": 1,
                }
            ],
        )
        bundle.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await recover_startup(session)
        draft = await session.get(CardDraft, bundle.active_draft_id)
        assert bundle.status == DraftStatus.EXPIRED.value
        assert draft.status == DraftStatus.EXPIRED.value
        try:
            await DraftService(session).commit_bundle(bundle.id)
        except ValueError as error:
            assert "not committable" in str(error)
        else:
            raise AssertionError("Expired drafts must not commit")
