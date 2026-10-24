from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from schedule_helpers import create_card, create_check
from sqlalchemy import select

from llm_gateway import CompletionTurn, ToolCall
from safwa.bootstrap.modules import REGISTRY
from safwa.features.cards.use_cases import toggle_card_check
from safwa.features.checks.check_review import (
    CHECK_REVIEW_HOOK,
    CHECK_REVIEW_PROMPT,
    CheckReviewer,
)
from safwa.features.checks.use_cases import (
    CHECK_CREATED,
    delete_check,
    resolve_check,
    update_check_fields,
)
from safwa.features.onboarding.agent import MANUAL
from safwa.features.profile.api import hook_switched_on, set_hook_switch
from tg_agent_shell.cues.background import tick
from tg_agent_shell.cues.initiatives import bind_committed
from tg_agent_shell.cues.model import Cue
from tg_agent_shell.hooks.contracts import OnCommitted, RunAndAdvise
from tg_agent_shell.hooks.registry import HookRegistry

ADVICE = "No fact shows it. Ask what fact would show it, such as Played with the kids?."


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
        (CHECK_REVIEW_HOOK,), owners=frozenset({"checks"}), policy=hook_switched_on
    )


def bound(sessions, provider, registry=None):
    return bind_committed(sessions, registry or hooks(), resources=SimpleNamespace(
        check_reviewer=CheckReviewer(provider)
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


async def created(sessions, *, card_title=None, **fields):
    async with sessions() as session:
        check = await create_check(session, **fields)
        if card_title is not None:
            card = await create_card(session, kind="action", title=card_title)
            await toggle_card_check(session, card.id, check.id)
        await session.commit()
        return check.id


def test_one_hook_checks_a_new_check_under_its_own_switch():
    """CH-REVIEW-019 — tests/brd/checks.feature"""
    assert CHECK_REVIEW_HOOK in REGISTRY.hooks.specs
    assert CHECK_REVIEW_HOOK.on == (OnCommitted(kind=CHECK_CREATED),)
    assert isinstance(CHECK_REVIEW_HOOK.effect, RunAndAdvise)
    assert CHECK_REVIEW_HOOK.agent_related and CHECK_REVIEW_HOOK.switch is None
    assert f"{CHECK_REVIEW_HOOK.title} in Profile → Hooks" in MANUAL


def test_the_review_knows_every_criterion_and_advises():
    """CH-REVIEW-019 — tests/brd/checks.feature"""
    for criterion in (
        "a state, a result or a habit done, such as Drank water? or No sweets today?",
        "answered yes or no",
        "observable: some fact shows the answer",
        "on a Card, about that Card's result",
        '"No sweets today?", every evening: clear()',
        '"Did my morning exercises?", every morning: clear()',
        '"Read for 20 minutes?", every evening: clear()',
        '"Was I a good father?": unclear(',
        "Do not judge whether the Check is worth keeping.",
    ):
        assert criterion in CHECK_REVIEW_PROMPT


@pytest.mark.parametrize("on_card", [False, True])
@pytest.mark.parametrize("clear", [True, False])
async def test_new_check_is_reviewed_alone_and_asked_about_only_when_unclear(
    sessions, on_card, clear
):
    """CH-REVIEW-019 — tests/brd/checks.feature"""
    provider = ReviewProvider(clear)
    registry = hooks()
    sink = bound(sessions, provider, registry)
    try:
        check_id = await created(
            sessions, title="Was I a good father?", note="With the kids",
            card_title="Morning routine" if on_card else None,
        )
        await sink.drain()
        assert len(provider.calls) == 1
        request = provider.calls[0]
        assert len(request.messages) == 2
        assert request.reasoning_effort == "none"
        assert [tool["function"]["name"] for tool in request.tools] == ["clear", "unclear"]
        assert json.loads(request.messages[1]["content"]) == {
            "title": "Was I a good father?",
            "note": "With the kids",
            "schedule": "",
            "card": "Action «Morning routine»" if on_card else "",
        }
        cues = await pending(sessions)
        assert len(cues) == (0 if clear else 1)
        if not clear:
            assert cues[0].hook == CHECK_REVIEW_HOOK.name and cues[0].text is None
            [words] = await say_waiting(sessions, registry)
            assert f"- [Was I a good father?](check:{check_id}): {ADVICE}" in words
            assert "Read the current Checks first" in words
            assert "you looked at the new Checks again and they could be improved" in words
            assert "route to workspace_mutator to update the Check.\n" in words
            assert "Change nothing until they answer" in words
            assert await pending(sessions) == []
    finally:
        await sink.close()


async def test_a_next_instance_a_rollback_or_an_edit_starts_no_review(sessions):
    """CH-REVIEW-019 — tests/brd/checks.feature"""
    provider = ReviewProvider(clear=True)
    sink = bound(sessions, provider)
    try:
        async with sessions() as session:
            await create_check(session, title="Slept 7 hours?")
            await session.rollback()
        await sink.drain()
        assert provider.calls == []

        check_id = await created(sessions, title="Slept 7 hours?", schedule="daily 21:00")
        await sink.drain()
        assert len(provider.calls) == 1
        async with sessions() as session:
            await update_check_fields(session, check_id, {"note": "In bed by eleven"})
            _, successor = await resolve_check(session, check_id, "passed")
            await session.commit()
        assert successor is not None
        await sink.drain()
        assert len(provider.calls) == 1
    finally:
        await sink.close()


@pytest.mark.parametrize("change", ["edit", "answer", "delete"])
async def test_check_changed_during_review_produces_no_cue(sessions, change):
    """CH-REVIEW-019 — tests/brd/checks.feature"""
    provider = ReviewProvider(wait=True)
    sink = bound(sessions, provider)
    try:
        check_id = await created(sessions, title="Was I a good father?")
        await asyncio.wait_for(provider.started.wait(), timeout=5)
        async with sessions() as session:
            if change == "edit":
                await update_check_fields(session, check_id, {"title": "Back without pain?"})
            elif change == "answer":
                await resolve_check(session, check_id, "passed")
            else:
                await delete_check(session, check_id)
            await session.commit()
        provider.release.set()
        await sink.drain()
        assert len(provider.calls) == 1
        assert await pending(sessions) == []
    finally:
        await sink.close()


async def test_unclear_checks_are_asked_together_and_an_answered_one_is_left_out(sessions):
    """CH-REVIEW-019 — tests/brd/checks.feature"""
    provider = ReviewProvider()
    registry = hooks()
    sink = bound(sessions, provider, registry)
    try:
        ids = [
            await created(sessions, title=title)
            for title in ("Run 5 km?", "How long did I sleep?", "Was I a good father?")
        ]
        await sink.drain()
        assert len(await pending(sessions)) == len(provider.calls) == 3
        async with sessions() as session:
            await resolve_check(session, ids[2], "missed")
            await session.commit()
        said = await say_waiting(sessions, registry)
        assert len(said) == 1
        assert f"[Run 5 km?](check:{ids[0]})" in said[0]
        assert f"[How long did I sleep?](check:{ids[1]})" in said[0]
        assert "good father" not in said[0]
        assert await pending(sessions) == []
    finally:
        await sink.close()


async def test_the_request_waits_while_hooks_are_held_and_its_switch_drops_it(sessions):
    """CH-REVIEW-019 — tests/brd/checks.feature"""
    provider = ReviewProvider()
    registry = hooks()
    sink = bound(sessions, provider, registry)
    try:
        await created(sessions, title="Run 5 km?")
        await sink.drain()
        assert await say_waiting(sessions, registry, hooks_allowed=False) == []
        assert len(await pending(sessions)) == 1
        async with sessions() as session:
            await set_hook_switch(
                session, CHECK_REVIEW_HOOK.name, on=False,
                followers=registry.followers(CHECK_REVIEW_HOOK.name),
            )
            await session.commit()
        assert await pending(sessions) == []
        await created(sessions, title="How long did I sleep?")
        await sink.drain()
        assert len(provider.calls) == 1 and await pending(sessions) == []
    finally:
        await sink.close()
