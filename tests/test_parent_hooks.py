"""The three questions a Card under several Goals brings: a Goal closed over open Cards, an
open Card whose Goals are all closed, and Cards a deletion left without a parent."""

from __future__ import annotations

from types import SimpleNamespace

from hook_helpers import changes_of
from schedule_helpers import create_card

from safwa.bootstrap.modules import REGISTRY
from safwa.features.cards.hooks import (
    CLOSED_PARENTS_HOOK,
    OPEN_UNDER_CLOSED_HOOK,
    ORPHANS_HOOK,
    closed_over_open,
    closed_parents_request,
    open_under_closed_request,
    opened_under_closed,
    orphans_request,
)
from safwa.features.cards.model import CardStage
from safwa.features.cards.use_cases import (
    CARD_CREATED,
    CARD_DONE,
    CARD_ORPHANED,
    CARD_REOPENED,
    delete_card,
    finish_action,
    finish_card,
    move_card,
    reopen_card,
    toggle_card_parent,
)
from tg_agent_shell.hooks.contracts import OnCommitted


def _context(sessions):
    return SimpleNamespace(sessions=sessions)


async def test_cd_close_057_a_goal_closed_over_open_cards_names_the_ones_only_it_holds(sessions):
    """CD-CLOSE-057 — tests/brd/cards.feature"""
    assert OPEN_UNDER_CLOSED_HOOK in REGISTRY.hooks.specs
    assert OPEN_UNDER_CLOSED_HOOK.on == (OnCommitted(kind=CARD_DONE),)
    assert OPEN_UNDER_CLOSED_HOOK.agent_related
    async with sessions() as session:
        goal = await create_card(session, kind="goal", title="Launch")
        other = await create_card(session, kind="goal", title="Grow audience")
        done = await create_card(session, kind="goal", title="Old plan")
        write = await create_card(session, kind="goal", title="Write", parent_ids={goal.id})
        draft = await create_card(
            session, kind="action", title="Draft", schedule="after completion",
            parent_ids={write.id},
        )
        await create_card(
            session, kind="action", title="Share", parent_ids={goal.id, other.id}
        )
        edit = await create_card(
            session, kind="action", title="Edit", parent_ids={write.id, done.id}
        )
        await finish_card(session, done.id)
        await finish_card(session, goal.id)
        # Closing a Goal is a Card finished, like an Action.
        assert goal.id in {change.subject_id for change in changes_of(session, CARD_DONE)}
        await session.commit()

    assert await closed_over_open(goal.id, _context(sessions)) == (goal.id,)
    assert await closed_over_open(other.id, _context(sessions)) == ()
    async with sessions() as session:
        request = await open_under_closed_request(session, [goal.id])
    assert request is not None
    assert f"#{goal.id} «Launch» (Goal): " in request
    for card in (write, edit):
        assert f"#{card.id} «{card.title}»" in request
    assert f"#{draft.id} «Draft» (repeats)" in request
    assert "Share" not in request
    assert "keep it closed, reopen it, or close" in request
    assert "remove the Schedule of one marked repeats" in request
    assert "Change nothing until they answer" in request

    async with sessions() as session:
        lonely = await create_card(session, kind="goal", title="Lonely")
        await create_card(session, kind="action", title="Talk", parent_ids={lonely.id, other.id})
        await finish_card(session, lonely.id)
        await reopen_card(session, goal.id)
        await session.commit()
    # Only a shared Card is open under it, and the other one was reopened.
    assert await closed_over_open(lonely.id, _context(sessions)) == ()
    async with sessions() as session:
        assert await open_under_closed_request(session, [goal.id, lonely.id]) is None


async def test_cd_reopen_058_an_open_card_under_closed_goals_names_them(sessions):
    """CD-REOPEN-058 — tests/brd/cards.feature"""
    assert CLOSED_PARENTS_HOOK in REGISTRY.hooks.specs
    assert CLOSED_PARENTS_HOOK.on == (
        OnCommitted(kind=CARD_CREATED), OnCommitted(kind=CARD_REOPENED),
    )
    assert CLOSED_PARENTS_HOOK.agent_related
    async with sessions() as session:
        goal = await create_card(session, kind="goal", title="Launch")
        write = await create_card(session, kind="goal", title="Write", parent_ids={goal.id})
        draft = await create_card(session, kind="action", title="Draft", parent_ids={write.id})
        loose = await create_card(session, kind="action", title="Loose")
        await finish_action(session, draft.id)
        await finish_card(session, write.id)
        await finish_card(session, goal.id)
        changes_of(session)
        await move_card(session, draft.id, CardStage.TODAY)
        assert [change.subject_id for change in changes_of(session, CARD_REOPENED)] == [draft.id]
        await session.commit()

    assert await opened_under_closed(draft.id, _context(sessions)) == (draft.id,)
    assert await opened_under_closed(loose.id, _context(sessions)) == ()
    async with sessions() as session:
        request = await closed_parents_request(session, [draft.id, loose.id])
    assert request is not None
    assert f"- #{draft.id} «Draft»: #{write.id} «Write», #{goal.id} «Launch»" in request
    assert "Loose" not in request
    assert "reopen each" in request and "Change nothing until they answer" in request

    async with sessions() as session:
        open_goal = await create_card(session, kind="goal", title="Open")
        await toggle_card_parent(session, draft.id, open_goal.id)
        repeat = await create_card(
            session, kind="action", title="Run", schedule="after completion", stage="today",
            parent_ids={write.id},
        )
        changes_of(session)
        await finish_action(session, repeat.id)
        # The next instance of a repeat is the system's, and is not a creation.
        assert changes_of(session, CARD_CREATED) == []
        await session.commit()
    # An open parent now holds it.
    assert await opened_under_closed(draft.id, _context(sessions)) == ()
    async with sessions() as session:
        assert await closed_parents_request(session, [draft.id]) is None


async def test_cd_orphan_059_the_cards_a_deletion_left_without_a_parent_are_asked_about(sessions):
    """CD-ORPHAN-059 — tests/brd/cards.feature"""
    assert ORPHANS_HOOK in REGISTRY.hooks.specs
    assert ORPHANS_HOOK.on == (OnCommitted(kind=CARD_ORPHANED),)
    assert ORPHANS_HOOK.agent_related
    async with sessions() as session:
        blog = await create_card(session, kind="goal", title="Launch the blog")
        audience = await create_card(session, kind="goal", title="Grow audience")
        post = await create_card(
            session, kind="goal", title="First post", parent_ids={blog.id, audience.id}
        )
        outline = await create_card(session, kind="goal", title="Outline", parent_ids={post.id})
        write = await create_card(session, kind="action", title="Write", parent_ids={post.id})
        await create_card(
            session, kind="action", title="Share", parent_ids={post.id, blog.id}
        )
        changes_of(session)
        await delete_card(session, post.id)
        orphaned = changes_of(session, CARD_ORPHANED)
        await session.commit()

    assert [change.subject_id for change in orphaned] == [post.id]
    item = dict(orphaned[0].data)
    assert item == {
        "title": "First post",
        "parent_ids": [blog.id, audience.id],
        "children": [outline.id, write.id],
    }
    async with sessions() as session:
        request = await orphans_request(session, [item])
    assert request is not None
    assert (
        f"- «First post»; it stood under #{blog.id} «Launch the blog», "
        f"#{audience.id} «Grow audience». Left without a parent: "
        f"#{outline.id} «Outline», #{write.id} «Write»"
    ) in request
    assert "Share" not in request
    assert "route to workspace_mutator to link that parent" in request

    async with sessions() as session:
        await toggle_card_parent(session, outline.id, blog.id)
        await delete_card(session, audience.id)
        await session.commit()
        request = await orphans_request(session, [item])
    assert f"it stood under #{blog.id} «Launch the blog». Left without a parent: #{write.id} «Write»" in request
    assert "Outline" not in request and "Grow audience" not in request

    async with sessions() as session:
        await delete_card(session, write.id)
        await session.commit()
        assert await orphans_request(session, [item]) is None
