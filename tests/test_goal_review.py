from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from schedule_helpers import create_card
from sqlalchemy import select

from llm_gateway import CompletionTurn, ToolCall
from safwa.bootstrap.modules import REGISTRY
from safwa.constants import INBOX_TAG_NAME
from safwa.features.cards.goal_review import (
    GOAL_CLARIFICATION_HOOK,
    GOAL_REVIEW_PROMPT,
    GoalReviewer,
)
from safwa.features.cards.use_cases import (
    CARD_CREATED,
    delete_one_card,
    finish_card,
    update_card_fields,
)
from safwa.features.onboarding.agent import MANUAL
from safwa.features.profile.api import hook_switched_on, set_hook_switch
from safwa.features.tags.model import Tag
from safwa.features.tags.use_cases import seed_inbox_tag
from tg_agent_shell.cues.background import tick
from tg_agent_shell.cues.initiatives import bind_committed
from tg_agent_shell.cues.model import Cue
from tg_agent_shell.hooks.contracts import OnCommitted, RunAndAdvise
from tg_agent_shell.hooks.registry import HookRegistry

ADVICE = "Done is not observable. Ask what result shows it, such as passing DELE B2."


class ReviewProvider:
    def __init__(self, clear=False, *, wait=False):
        self.clear = clear
        self.calls = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        if not wait:
            self.release.set()

    async def complete(self, request):
        self.calls.append(request)
        self.started.set()
        await self.release.wait()
        call = (
            ToolCall(id="review", name="clear", arguments_json="{}")
            if self.clear
            else ToolCall(id="review", name="unclear", arguments_json=json.dumps({"advice": ADVICE}))
        )
        return CompletionTurn(content="", tool_calls=(call,))


def hooks():
    return HookRegistry.of(
        (GOAL_CLARIFICATION_HOOK,), owners=frozenset({"cards"}), policy=hook_switched_on
    )


def bound(sessions, provider, registry=None):
    return bind_committed(sessions, registry or hooks(), resources=SimpleNamespace(
        goal_reviewer=GoalReviewer(provider)
    ))


async def say_waiting(sessions, registry, *, hooks_allowed=True):
    said = []

    async def speak(event_id, text, shown=(), passing=None):
        said.append(text)
        return True

    async def free():
        return True

    async def delivered(event_id):
        return False

    async def prepare(hook, items):
        return await registry.prepare(sessions, hook, items)

    await tick(
        sessions, gate=free, speak=speak, delivered=delivered, prepare=prepare,
        allow_hooks=lambda: hooks_allowed,
    )
    return said


async def pending(sessions):
    async with sessions() as session:
        return list(await session.scalars(select(Cue)))


async def created(sessions, **fields):
    async with sessions() as session:
        card = await create_card(session, **fields)
        await session.commit()
        return card.id


def test_one_hook_checks_a_new_goal_under_its_own_switch():
    """CD-GOAL-051 — tests/brd/cards.feature"""
    assert GOAL_CLARIFICATION_HOOK in REGISTRY.hooks.specs
    assert GOAL_CLARIFICATION_HOOK.on == (OnCommitted(kind=CARD_CREATED),)
    assert isinstance(GOAL_CLARIFICATION_HOOK.effect, RunAndAdvise)
    assert GOAL_CLARIFICATION_HOOK.agent_related and GOAL_CLARIFICATION_HOOK.switch is None
    assert f"{GOAL_CLARIFICATION_HOOK.title} in Profile → Hooks" in MANUAL


def test_the_review_knows_every_criterion_and_advises():
    """CD-GOAL-051 — tests/brd/cards.feature"""
    for criterion in (
        "names one concrete result, not a direction",
        "is a result, not an ongoing practice",
        "takes more than one day; work for one day is an Action",
        "has an observable Done",
        "Two results. Suggest two Goals.",
        "Do not judge whether the Goal is attainable or worth doing.",
    ):
        assert criterion in GOAL_REVIEW_PROMPT
    assert "parent Goal's result" not in GOAL_REVIEW_PROMPT


@pytest.mark.parametrize("subgoal", [False, True])
@pytest.mark.parametrize("clear", [True, False])
async def test_new_goal_is_reviewed_alone_and_asked_about_only_when_unclear(
    sessions, subgoal, clear
):
    """CD-GOAL-051 — tests/brd/cards.feature"""
    parent_id = await created(sessions, kind="goal", title="Get in shape") if subgoal else None
    provider = ReviewProvider(clear)
    registry = hooks()
    sink = bound(sessions, provider, registry)
    try:
        goal_id = await created(
            sessions, kind="goal", title="Learn Spanish", note="Read books",
            schedule="20.12.2026 18:00", parent_id=parent_id,
        )
        await sink.drain()
        assert len(provider.calls) == 1
        request = provider.calls[0]
        assert len(request.messages) == 2
        assert request.reasoning_effort == "none"
        assert [tool["function"]["name"] for tool in request.tools] == ["clear", "unclear"]
        context = json.loads(request.messages[1]["content"])
        assert context == {
            "kind": "Subgoal" if subgoal else "Goal",
            "title": "Learn Spanish",
            "note": "Read books",
            "deadline": "20.12.2026 18:00",
            "parent": "Get in shape" if subgoal else "",
        }
        cues = await pending(sessions)
        assert len(cues) == (0 if clear else 1)
        if not clear:
            assert cues[0].hook == GOAL_CLARIFICATION_HOOK.name and cues[0].text is None
            [words] = await say_waiting(sessions, registry)
            kind = "Subgoal" if subgoal else "Goal"
            assert f"#{goal_id} «Learn Spanish» ({kind}): {ADVICE}" in words
            assert "you looked at the new Goals again and they could be improved" in words
            assert "Offer to update the Goal with their answer" in words
            assert "write the criterion in Note, or to propose what it should be instead" in words
            assert "Read the current Goals first" in words
            assert "Change nothing until they answer" in words
            assert await pending(sessions) == []
    finally:
        await sink.close()


async def test_an_action_an_inbox_goal_a_rollback_or_an_edit_starts_no_review(sessions):
    """CD-GOAL-051 — tests/brd/cards.feature"""
    provider = ReviewProvider()
    sink = bound(sessions, provider)
    try:
        async with sessions() as session:
            await seed_inbox_tag(session)
            await session.commit()
            inbox_id = await session.scalar(select(Tag.id).where(Tag.name == INBOX_TAG_NAME))
        await created(sessions, kind="action", title="Call the bank")
        await created(sessions, kind="goal", title="Some idea", tag_ids={inbox_id})
        async with sessions() as session:
            await create_card(session, kind="goal", title="Learn Spanish")
            await session.rollback()
        await sink.drain()
        assert provider.calls == []

        goal_id = await created(sessions, kind="goal", title="Learn Spanish")
        await sink.drain()
        assert len(provider.calls) == 1
        async with sessions() as session:
            await update_card_fields(session, goal_id, {"note": "Pass DELE B2"})
            await session.commit()
        await sink.drain()
        assert len(provider.calls) == 1
    finally:
        await sink.close()


@pytest.mark.parametrize("change", ["edit", "close", "delete"])
async def test_goal_changed_during_review_produces_no_cue(sessions, change):
    """CD-GOAL-051 — tests/brd/cards.feature"""
    provider = ReviewProvider(wait=True)
    sink = bound(sessions, provider)
    try:
        goal_id = await created(sessions, kind="goal", title="Learn Spanish")
        await asyncio.wait_for(provider.started.wait(), timeout=5)
        async with sessions() as session:
            if change == "edit":
                await update_card_fields(session, goal_id, {"note": "Pass DELE B2"})
            elif change == "close":
                await finish_card(session, goal_id)
            else:
                await delete_one_card(session, goal_id)
            await session.commit()
        provider.release.set()
        await sink.drain()
        assert len(provider.calls) == 1
        assert await pending(sessions) == []
    finally:
        await sink.close()


async def test_unclear_goals_are_asked_together_and_a_closed_one_is_left_out(sessions):
    """CD-GOAL-051 — tests/brd/cards.feature"""
    provider = ReviewProvider()
    registry = hooks()
    sink = bound(sessions, provider, registry)
    try:
        ids = [
            await created(sessions, kind="goal", title=title)
            for title in ("Learn Spanish", "Get fit", "Write more")
        ]
        await sink.drain()
        assert len(await pending(sessions)) == len(provider.calls) == 3
        async with sessions() as session:
            await finish_card(session, ids[2])
            await session.commit()
        said = await say_waiting(sessions, registry)
        assert len(said) == 1
        assert f"#{ids[0]} «Learn Spanish»" in said[0]
        assert f"#{ids[1]} «Get fit»" in said[0]
        assert "Write more" not in said[0]
        assert await pending(sessions) == []
    finally:
        await sink.close()


async def test_the_request_waits_while_hooks_are_held_and_its_switch_drops_it(sessions):
    """CD-GOAL-051 — tests/brd/cards.feature"""
    provider = ReviewProvider()
    registry = hooks()
    sink = bound(sessions, provider, registry)
    try:
        await created(sessions, kind="goal", title="Learn Spanish")
        await sink.drain()
        assert await say_waiting(sessions, registry, hooks_allowed=False) == []
        assert len(await pending(sessions)) == 1
        async with sessions() as session:
            await set_hook_switch(
                session, GOAL_CLARIFICATION_HOOK.name, on=False,
                followers=registry.followers(GOAL_CLARIFICATION_HOOK.name),
            )
            await session.commit()
        assert await pending(sessions) == []
        await created(sessions, kind="goal", title="Get fit")
        await sink.drain()
        assert len(provider.calls) == 1 and await pending(sessions) == []
    finally:
        await sink.close()
