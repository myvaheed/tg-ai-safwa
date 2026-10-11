from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from schedule_helpers import create_card, create_check
from sqlalchemy import select

from llm_gateway import CompletionTurn, ToolCall
from safwa.bootstrap.modules import REGISTRY, SYSTEM_PROMPT, routed_prompt
from safwa.features.cards.use_cases import finish_action, toggle_card_value, update_card_fields
from safwa.features.checks.model import Check
from safwa.features.checks.use_cases import resolve_check, toggle_check_value, update_check_fields
from safwa.features.onboarding.agent import MANUAL
from safwa.features.profile.api import hook_switched_on, set_hook_switch
from safwa.features.values.api import VALUE_LINK_MEANING
from safwa.features.values.hooks import VALUE_LINK_HOOK
from safwa.features.values.link_review import ValueLinkReviewer
from safwa.features.values.use_cases import create_value, update_value_fields
from safwa.features.workspace_mutator.agent import MUTATOR_AGENT
from tg_agent_shell.ai.mini import MiniSessionError
from tg_agent_shell.cues.background import tick
from tg_agent_shell.cues.initiatives import bind_committed
from tg_agent_shell.cues.model import Cue
from tg_agent_shell.hooks.contracts import RunAndAdvise
from tg_agent_shell.hooks.registry import HookRegistry


class ReviewProvider:
    def __init__(self, clear=False, *, wait=False, malformed=False):
        self.clear = clear
        self.malformed = malformed
        self.calls = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        if not wait:
            self.release.set()

    async def complete(self, request):
        self.calls.append(request)
        self.started.set()
        await self.release.wait()
        return CompletionTurn(
            content="",
            tool_calls=(ToolCall(
                id="review",
                name="value_link_review",
                arguments_json='{"clear":"maybe"}' if self.malformed else json.dumps({"clear": self.clear}),
            ),)
        )


def hooks():
    return HookRegistry.of(
        (VALUE_LINK_HOOK,),
        owners=frozenset({"values"}),
        policy=hook_switched_on,
    )


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
        sessions,
        gate=free,
        speak=speak,
        delivered=delivered,
        prepare=prepare,
        allow_hooks=lambda: hooks_allowed,
    )
    return said


async def pending(sessions):
    async with sessions() as session:
        return list(await session.scalars(select(Cue)))


async def pair(sessions, kind):
    async with sessions() as session:
        value = await create_value(session, "Health", "Money for care")
        if kind == "check":
            item = await create_check(session, title="Earned $4k?", note="Monthly result")
        else:
            item = await create_card(session, kind=kind, title="Earn $4k", note="Monthly result")
        await session.commit()
        return item.id, value.id


async def link(session, kind, item_id, value_id):
    toggle = toggle_check_value if kind == "check" else toggle_card_value
    return await toggle(session, item_id, value_id)


def test_all_three_prompts_teach_the_same_supporting_direction():
    """VL-LINK-018 — tests/brd/values.feature"""
    assert VALUE_LINK_MEANING in SYSTEM_PROMPT
    assert VALUE_LINK_MEANING in routed_prompt(MUTATOR_AGENT)
    assert VALUE_LINK_MEANING in MANUAL


def test_one_hook_checks_the_link_and_asks_under_its_own_switch():
    """VL-LINK-019 — tests/brd/values.feature"""
    assert [spec for spec in REGISTRY.hooks.specs if spec.owner == "values"] == [VALUE_LINK_HOOK]
    assert isinstance(VALUE_LINK_HOOK.effect, RunAndAdvise)
    assert VALUE_LINK_HOOK.agent_related and VALUE_LINK_HOOK.switch is None


@pytest.mark.parametrize("kind", ["goal", "action", "check"])
@pytest.mark.parametrize("clear", [True, False])
async def test_saved_link_reviews_only_the_pair_and_asks_only_when_unclear(sessions, kind, clear):
    """VL-LINK-019 — tests/brd/values.feature"""
    item_id, value_id = await pair(sessions, kind)
    provider = ReviewProvider(clear)
    registry = hooks()
    sink = bind_committed(sessions, registry, resources=SimpleNamespace(
        value_link_reviewer=ValueLinkReviewer(provider)
    ))
    try:
        async with sessions() as session:
            await link(session, kind, item_id, value_id)
            await session.commit()
        await sink.drain()
        assert len(provider.calls) == 1
        request = provider.calls[0]
        assert len(request.messages) == 2
        assert request.reasoning_effort == "none"
        assert [tool["function"]["name"] for tool in request.tools] == ["value_link_review"]
        context = json.loads(request.messages[1]["content"])
        assert set(context) == {"kind", "title", "note", "value", "value_description"}
        assert context["value_description"] == "Money for care"
        assert context["note"] == "Monthly result"
        cues = await pending(sessions)
        assert len(cues) == (0 if clear else 1)
        if not clear:
            assert cues[0].hook == VALUE_LINK_HOOK.name and cues[0].text is None
            [words] = await say_waiting(sessions, registry)
            assert "Note" in words
            assert f"value:{value_id}" in words
            assert "Read the current items and Values first" in words
            assert "Change nothing until they answer" in words
    finally:
        await sink.close()


@pytest.mark.parametrize("kind", ["goal", "check"])
@pytest.mark.parametrize("change", ["unlink", "edit", "value_edit"])
async def test_pair_changed_during_review_produces_no_cue(sessions, kind, change):
    """VL-LINK-019 — tests/brd/values.feature"""
    item_id, value_id = await pair(sessions, kind)
    registry = hooks()
    provider = ReviewProvider(wait=True)
    sink = bind_committed(sessions, registry, resources=SimpleNamespace(
        value_link_reviewer=ValueLinkReviewer(provider)
    ))
    try:
        async with sessions() as session:
            await link(session, kind, item_id, value_id)
            await session.commit()
        await asyncio.wait_for(provider.started.wait(), timeout=5)
        async with sessions() as session:
            if change == "unlink":
                await link(session, kind, item_id, value_id)
            elif change == "edit":
                update = update_check_fields if kind == "check" else update_card_fields
                await update(session, item_id, {"note": "Money pays for care"})
            else:
                await update_value_fields(session, value_id, description="Care and rest")
            await session.commit()
        provider.release.set()
        await sink.drain()
        assert len(provider.calls) == 1
        assert await pending(sessions) == []
    finally:
        await sink.close()


async def test_unclear_links_are_asked_together_in_one_request(sessions):
    """VL-LINK-019 — tests/brd/values.feature"""
    item_id, first_id = await pair(sessions, "goal")
    provider = ReviewProvider()
    registry = hooks()
    sink = bind_committed(sessions, registry, resources=SimpleNamespace(
        value_link_reviewer=ValueLinkReviewer(provider)
    ))
    try:
        async with sessions() as session:
            second = await create_value(session, "Perseverance")
            second_id = second.id
            await link(session, "goal", item_id, first_id)
            await session.commit()
        await sink.drain()
        async with sessions() as session:
            await link(session, "goal", item_id, second_id)
            await session.commit()
        await sink.drain()
        cues = await pending(sessions)
        assert len(cues) == len(provider.calls) == 2
        said = await say_waiting(sessions, registry)
        assert len(said) == 1
        assert said[0].count(f"value:{first_id}") == 1
        assert said[0].count(f"value:{second_id}") == 1
        assert await pending(sessions) == []
    finally:
        await sink.close()


async def test_the_request_waits_while_hooks_are_held_and_its_switch_drops_it(sessions):
    """VL-LINK-019 — tests/brd/values.feature"""
    item_id, value_id = await pair(sessions, "goal")
    provider = ReviewProvider()
    registry = hooks()
    sink = bind_committed(sessions, registry, resources=SimpleNamespace(
        value_link_reviewer=ValueLinkReviewer(provider)
    ))
    try:
        async with sessions() as session:
            await link(session, "goal", item_id, value_id)
            await session.commit()
        await sink.drain()
        # A locked chat holds hook requests: nothing is worded or said.
        assert await say_waiting(sessions, registry, hooks_allowed=False) == []
        assert len(await pending(sessions)) == 1
        async with sessions() as session:
            await set_hook_switch(
                session, VALUE_LINK_HOOK.name, on=False,
                followers=registry.followers(VALUE_LINK_HOOK.name),
            )
            await session.commit()
        assert await pending(sessions) == []
        async with sessions() as session:
            await link(session, "goal", item_id, value_id)
            await link(session, "goal", item_id, value_id)
            await session.commit()
        await sink.drain()
        assert len(provider.calls) == 1 and await pending(sessions) == []
    finally:
        await sink.close()


async def test_rollback_and_repeat_copies_start_no_review(sessions):
    """VL-LINK-019 — tests/brd/values.feature"""
    provider = ReviewProvider(clear=True)
    sink = bind_committed(sessions, hooks(), resources=SimpleNamespace(
        value_link_reviewer=ValueLinkReviewer(provider)
    ))
    try:
        item_id, value_id = await pair(sessions, "action")
        async with sessions() as session:
            await link(session, "action", item_id, value_id)
            await session.rollback()
        await sink.drain()
        assert provider.calls == []
        async with sessions() as session:
            action = await create_card(
                session, kind="action", title="Walk", schedule="daily 18:00", value_ids={value_id}
            )
            check = await create_check(session, title="Walked?", schedule="daily 18:00")
            await toggle_check_value(session, check.id, value_id)
            await session.commit()
            action_id, check_id = action.id, check.id
        await sink.drain()
        assert len(provider.calls) == 2
        async with sessions() as session:
            await finish_action(session, action_id)
            await resolve_check(session, check_id, "passed")
            await session.commit()
        await sink.drain()
        assert len(provider.calls) == 2
        assert await pending(sessions) == []
    finally:
        await sink.close()


async def test_a_malformed_answer_is_not_an_unclear_link_or_a_retry_loop(sessions):
    """VL-LINK-019 — tests/brd/values.feature"""
    provider = ReviewProvider(malformed=True)
    reviewer = ValueLinkReviewer(provider)
    with pytest.raises(MiniSessionError):
        await reviewer.clear({
            "kind": "goal", "title": "Earn $4k", "note": "", "value": "Health",
            "value_description": "",
        })
    assert len(provider.calls) == 1


async def test_check_note_survives_repetition_and_is_readable(read_views):
    """CH-WRITE-018 — tests/brd/checks.feature"""
    sessions, reader = read_views
    async with sessions() as session:
        check = await create_check(
            session, title="Slept?", note="Sleep supports health", schedule="daily 18:00"
        )
        await update_check_fields(session, check.id, {"note": "Seven hours restores me"})
        _, successor = await resolve_check(session, check.id, "passed")
        await session.commit()
        successor_id = successor.id
    async with sessions() as session:
        assert (await session.get(Check, successor_id)).note == "Seven hours restores me"
    result = await reader.run(f"SELECT note FROM ai_checks WHERE id={successor_id}")
    assert result.rows == [{"note": "Seven hours restores me"}]
