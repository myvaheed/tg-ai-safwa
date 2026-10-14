from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from llm_gateway import CompletionTurn, ToolCall
from safwa.bootstrap.modules import REGISTRY, SYSTEM_PROMPT, routed_prompt
from safwa.features.onboarding.agent import MANUAL
from safwa.features.profile.api import hook_switched_on, set_hook_switch
from safwa.features.values.use_cases import (
    VALUE_CREATED,
    create_value,
    delete_value,
    update_value_fields,
)
from safwa.features.values.value_review import (
    VALUE_CLARIFICATION_HOOK,
    VALUE_REVIEW_PROMPT,
    ValueReviewer,
)
from safwa.features.workspace_mutator.agent import MUTATOR_AGENT
from tg_agent_shell.cues.background import tick
from tg_agent_shell.cues.initiatives import bind_committed
from tg_agent_shell.cues.model import Cue
from tg_agent_shell.hooks.contracts import OnCommitted, RunAndAdvise
from tg_agent_shell.hooks.registry import HookRegistry

ADVICE = "A means. Ask what it is for, and again, until a direction such as Security or Freedom."


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
        (VALUE_CLARIFICATION_HOOK,), owners=frozenset({"values"}), policy=hook_switched_on
    )


def bound(sessions, provider, registry=None):
    return bind_committed(sessions, registry or hooks(), resources=SimpleNamespace(
        value_reviewer=ValueReviewer(provider)
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


async def created(sessions, name, description="What matters to me"):
    async with sessions() as session:
        value = await create_value(session, name, description)
        await session.commit()
        return value.id


def test_safwa_says_what_a_value_is_before_proposing_one():
    """VL-MEANING-023 — tests/brd/values.feature"""
    mutator = routed_prompt(MUTATOR_AGENT)
    for prompt in (SYSTEM_PROMPT, mutator):
        for line in (
            "- Value: a direction the user lives by, with no deadline. It is never Done.",
            "- A Value is not a result, a practice, a feeling, a means or a rule.",
            "- A result is a Goal. A practice is a repeating Action.",
            "- Value name: short and vivid, in the user's language.",
            "- Value description: two or three vivid sentences on why it matters, "
            "then what the user does for it on an ordinary day.",
            "- Build both from the user's words. Never invent why it matters or what they do.",
        ):
            assert line in prompt
    assert (
        "- A new Value: if the user has not said why it matters or what they do for it, "
        "call nothing_to_do with what needs clarification." in mutator
    )


def test_one_hook_checks_a_new_value_under_its_own_switch():
    """VL-CLARIFY-022 — tests/brd/values.feature"""
    assert VALUE_CLARIFICATION_HOOK in REGISTRY.hooks.specs
    assert VALUE_CLARIFICATION_HOOK.on == (OnCommitted(kind=VALUE_CREATED),)
    assert isinstance(VALUE_CLARIFICATION_HOOK.effect, RunAndAdvise)
    assert VALUE_CLARIFICATION_HOOK.agent_related and VALUE_CLARIFICATION_HOOK.switch is None
    assert f"{VALUE_CLARIFICATION_HOOK.title} in Profile → Hooks" in MANUAL


def test_the_review_knows_every_criterion_and_advises_with_exercises():
    """VL-CLARIFY-022 — tests/brd/values.feature"""
    for criterion in (
        "not a result that can be Done",
        "not one practice such as running",
        "no feeling to have in its name",
        "not away from something",
        "an end, not a means",
        "not a rule with must, always, never or perfect in its name",
        "the name may be about others, such as their smile, when the description says what "
        "the user does",
        "the description says what the user does for it on an ordinary day",
    ):
        assert criterion in VALUE_REVIEW_PROMPT
    assert "how it looks on an ordinary day" in VALUE_REVIEW_PROMPT
    assert "a description of what it gives the user in itself, not only what it buys" in (
        VALUE_REVIEW_PROMPT
    )
    assert (
        "Do not judge whether the Value is right for the user, or how vivid its words are."
        in VALUE_REVIEW_PROMPT
    )


@pytest.mark.parametrize("clear", [True, False])
async def test_new_value_is_reviewed_alone_and_asked_about_only_when_unclear(sessions, clear):
    """VL-CLARIFY-022 — tests/brd/values.feature"""
    provider = ReviewProvider(clear)
    registry = hooks()
    sink = bound(sessions, provider, registry)
    try:
        value_id = await created(sessions, "Money", "More of it")
        await sink.drain()
        assert len(provider.calls) == 1
        request = provider.calls[0]
        assert len(request.messages) == 2
        assert request.reasoning_effort == "none"
        assert [tool["function"]["name"] for tool in request.tools] == ["clear", "unclear"]
        assert json.loads(request.messages[1]["content"]) == {
            "name": "Money", "description": "More of it",
        }
        cues = await pending(sessions)
        assert len(cues) == (0 if clear else 1)
        if not clear:
            assert cues[0].hook == VALUE_CLARIFICATION_HOOK.name and cues[0].text is None
            [words] = await say_waiting(sessions, registry)
            assert f"- [Money](value:{value_id}): {ADVICE}" in words
            assert "Read the current Values first" in words
            assert "you looked at the new Values again and they could be improved" in words
            assert "Offer to update the Value with their answer" in words
            assert "update the Value, or to propose a Goal or a repeating Action instead" in words
            assert "Change nothing until they answer" in words
            assert await pending(sessions) == []
    finally:
        await sink.close()


async def test_a_rename_a_new_description_or_a_rollback_starts_no_review(sessions):
    """VL-CLARIFY-022 — tests/brd/values.feature"""
    provider = ReviewProvider()
    sink = bound(sessions, provider)
    try:
        async with sessions() as session:
            await create_value(session, "Money", "What matters to me")
            await session.rollback()
        await sink.drain()
        assert provider.calls == []

        value_id = await created(sessions, "Money")
        await sink.drain()
        assert len(provider.calls) == 1
        async with sessions() as session:
            await update_value_fields(
                session, value_id, name="Security", description="A year of savings for the family"
            )
            await session.commit()
        await sink.drain()
        assert len(provider.calls) == 1
    finally:
        await sink.close()


@pytest.mark.parametrize("change", ["rename", "describe", "delete"])
async def test_value_changed_during_review_produces_no_cue(sessions, change):
    """VL-CLARIFY-022 — tests/brd/values.feature"""
    provider = ReviewProvider(wait=True)
    sink = bound(sessions, provider)
    try:
        value_id = await created(sessions, "Money")
        await asyncio.wait_for(provider.started.wait(), timeout=5)
        async with sessions() as session:
            if change == "rename":
                await update_value_fields(session, value_id, name="Security")
            elif change == "describe":
                await update_value_fields(session, value_id, description="Savings for the family")
            else:
                await delete_value(session, value_id)
            await session.commit()
        provider.release.set()
        await sink.drain()
        assert len(provider.calls) == 1
        assert await pending(sessions) == []
    finally:
        await sink.close()


async def test_unclear_values_are_asked_together_and_a_deleted_one_is_left_out(sessions):
    """VL-CLARIFY-022 — tests/brd/values.feature"""
    provider = ReviewProvider()
    registry = hooks()
    sink = bound(sessions, provider, registry)
    try:
        ids = [await created(sessions, name) for name in ("Money", "Be happy", "Running")]
        await sink.drain()
        assert len(await pending(sessions)) == len(provider.calls) == 3
        async with sessions() as session:
            await delete_value(session, ids[2])
            await session.commit()
        said = await say_waiting(sessions, registry)
        assert len(said) == 1
        assert f"[Money](value:{ids[0]})" in said[0]
        assert f"[Be happy](value:{ids[1]})" in said[0]
        assert "Running" not in said[0]
        assert await pending(sessions) == []
    finally:
        await sink.close()


async def test_the_request_waits_while_hooks_are_held_and_its_switch_drops_it(sessions):
    """VL-CLARIFY-022 — tests/brd/values.feature"""
    provider = ReviewProvider()
    registry = hooks()
    sink = bound(sessions, provider, registry)
    try:
        await created(sessions, "Money")
        await sink.drain()
        assert await say_waiting(sessions, registry, hooks_allowed=False) == []
        assert len(await pending(sessions)) == 1
        async with sessions() as session:
            await set_hook_switch(
                session, VALUE_CLARIFICATION_HOOK.name, on=False,
                followers=registry.followers(VALUE_CLARIFICATION_HOOK.name),
            )
            await session.commit()
        assert await pending(sessions) == []
        await created(sessions, "Be happy")
        await sink.drain()
        assert len(provider.calls) == 1 and await pending(sessions) == []
    finally:
        await sink.close()
