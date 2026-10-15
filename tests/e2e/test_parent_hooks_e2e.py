from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import select

from safwa.bootstrap.modules import REGISTRY
from safwa.features.cards.hooks import (
    CLOSED_PARENTS_HOOK,
    OPEN_UNDER_CLOSED_HOOK,
    ORPHANS_HOOK,
    PARENT_COMPLETION_HOOK,
)
from safwa.features.cards.model import CardStage
from safwa.features.cards.use_cases import (
    create_card,
    delete_card,
    finish_action,
    finish_card,
    move_card,
)
from safwa.features.onboarding.hooks import ONBOARDING_HOOK
from safwa.features.profile.api import set_hook_switch
from tg_agent_shell.cues.background import tick
from tg_agent_shell.cues.initiatives import bind_committed
from tg_agent_shell.cues.model import Cue
from tg_agent_shell.cues.runtime import CueRuntime

pytestmark = pytest.mark.e2e


async def pending(sessions):
    async with sessions() as session:
        return [(cue.hook, cue.payload) for cue in await session.scalars(select(Cue))]


async def said_once(sessions):
    """What the one turn the waiting rows make says, and that nothing waits after it."""
    said = []

    async def speak(event_id, text, shown=(), passing=None):
        said.append(text)
        return True

    async def gate():
        return True

    runtime = CueRuntime(
        SimpleNamespace(sessions=sessions, hooks=REGISTRY.hooks), bot=None, owner_id=42
    )
    assert await tick(
        sessions, gate=gate, speak=speak, delivered=runtime.delivered, prepare=runtime.prepare
    )
    assert await pending(sessions) == []
    return said


def runtime_for(sessions):
    return CueRuntime(
        SimpleNamespace(sessions=sessions, hooks=REGISTRY.hooks), bot=None, owner_id=42
    )


@pytest.mark.parametrize("switched_off", [False, True])
async def test_cd_close_057_a_goal_closed_over_open_cards_asks_once(e2e_harness, switched_off):
    """CD-CLOSE-057 — tests/brd/cards.feature"""
    sessions = e2e_harness.sessions
    sink = bind_committed(sessions, REGISTRY.hooks)
    async with sessions() as session:
        await set_hook_switch(session, ONBOARDING_HOOK.name, on=False)
        goal = await create_card(session, kind="goal", title="Launch")
        draft = await create_card(session, kind="action", title="Draft", parent_ids={goal.id})
        done = await create_card(session, kind="action", title="Upload", parent_ids={goal.id})
        await session.commit()
        goal_id, draft_id, done_id = goal.id, draft.id, done.id
    await sink.drain()
    async with sessions() as session:
        await finish_action(session, done_id)
        await session.commit()
    await sink.drain()
    # An Action finished is a Card closed too, and it has nothing under it.
    assert await pending(sessions) == []

    async with sessions() as session:
        await finish_card(session, goal_id)
        await session.commit()
    await sink.drain()
    assert await pending(sessions) == [(OPEN_UNDER_CLOSED_HOOK.name, [goal_id])]
    if switched_off:
        async with sessions() as session:
            await set_hook_switch(session, OPEN_UNDER_CLOSED_HOOK.name, on=False)
            await session.commit()
        assert await runtime_for(sessions).prepare(OPEN_UNDER_CLOSED_HOOK.name, [goal_id]) is None
        return
    said = await said_once(sessions)
    assert len(said) == 1
    assert f"#{goal_id} «Launch» (Goal): #{draft_id} «Draft»" in said[0]
    assert "Upload" not in said[0]


@pytest.mark.parametrize("switched_off", [False, True])
async def test_cd_reopen_058_a_card_reopened_under_closed_goals_asks_once(e2e_harness, switched_off):
    """CD-REOPEN-058 — tests/brd/cards.feature"""
    sessions = e2e_harness.sessions
    sink = bind_committed(sessions, REGISTRY.hooks)
    async with sessions() as session:
        await set_hook_switch(session, ONBOARDING_HOOK.name, on=False)
        await set_hook_switch(session, OPEN_UNDER_CLOSED_HOOK.name, on=False)
        await set_hook_switch(session, PARENT_COMPLETION_HOOK.name, on=False)
        goal = await create_card(session, kind="goal", title="Launch")
        draft = await create_card(session, kind="action", title="Draft", parent_ids={goal.id})
        await finish_action(session, draft.id)
        await finish_card(session, goal.id)
        await session.commit()
        goal_id, draft_id = goal.id, draft.id
    await sink.drain()
    assert await pending(sessions) == []

    async with sessions() as session:
        await move_card(session, draft_id, CardStage.TODAY)
        await session.commit()
    await sink.drain()
    assert await pending(sessions) == [(CLOSED_PARENTS_HOOK.name, [draft_id])]
    if switched_off:
        async with sessions() as session:
            await set_hook_switch(session, CLOSED_PARENTS_HOOK.name, on=False)
            await session.commit()
        assert await runtime_for(sessions).prepare(CLOSED_PARENTS_HOOK.name, [draft_id]) is None
        return
    said = await said_once(sessions)
    assert len(said) == 1
    assert f"#{draft_id} «Draft»: #{goal_id} «Launch»" in said[0]


@pytest.mark.parametrize("switched_off", [False, True])
async def test_cd_orphan_059_a_deletion_that_leaves_cards_without_a_parent_asks_once(
    e2e_harness, switched_off
):
    """CD-ORPHAN-059 — tests/brd/cards.feature"""
    sessions = e2e_harness.sessions
    sink = bind_committed(sessions, REGISTRY.hooks)
    async with sessions() as session:
        await set_hook_switch(session, ONBOARDING_HOOK.name, on=False)
        blog = await create_card(session, kind="goal", title="Launch the blog")
        post = await create_card(session, kind="goal", title="First post", parent_ids={blog.id})
        write = await create_card(session, kind="action", title="Write", parent_ids={post.id})
        await session.commit()
        blog_id, post_id, write_id = blog.id, post.id, write.id
    await sink.drain()
    async with sessions() as session:
        await delete_card(session, write_id)
        await session.commit()
    await sink.drain()
    # A Card with nothing under it leaves nobody without a parent.
    assert await pending(sessions) == []

    async with sessions() as session:
        write = await create_card(session, kind="action", title="Write", parent_ids={post_id})
        await session.commit()
        write_id = write.id
    await sink.drain()
    async with sessions() as session:
        await delete_card(session, post_id)
        await session.commit()
    await sink.drain()
    item = {"title": "First post", "parent_ids": [blog_id], "children": [write_id]}
    assert await pending(sessions) == [(ORPHANS_HOOK.name, [item])]
    if switched_off:
        async with sessions() as session:
            await set_hook_switch(session, ORPHANS_HOOK.name, on=False)
            await session.commit()
        assert await runtime_for(sessions).prepare(ORPHANS_HOOK.name, [item]) is None
        return
    said = await said_once(sessions)
    assert len(said) == 1
    assert (
        f"«First post»; it stood under #{blog_id} «Launch the blog». "
        f"Left without a parent: #{write_id} «Write»"
    ) in said[0]
