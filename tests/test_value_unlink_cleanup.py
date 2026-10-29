from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from schedule_helpers import create_card, create_check
from sqlalchemy import select

from llm_gateway import CompletionTurn, ToolCall
from safwa.bootstrap.modules import REGISTRY
from safwa.features.cards.use_cases import (
    archive_subtree,
    finish_action,
    toggle_card_value,
    update_card_fields,
)
from safwa.features.checks.use_cases import toggle_check_value
from safwa.features.onboarding.agent import MANUAL
from safwa.features.profile.api import hook_switched_on, set_hook_switch
from safwa.features.values.hooks import VALUE_UNLINK_HOOK
from safwa.features.values.link_review import ValueLinkReviewer
from safwa.features.values.use_cases import create_value, delete_value
from tg_agent_shell.cues.background import tick
from tg_agent_shell.cues.initiatives import bind_committed
from tg_agent_shell.cues.model import Cue
from tg_agent_shell.hooks.registry import HookRegistry


class MentionProvider:
    """Says a Note mentions the Value when it holds the word "health"."""

    def __init__(self, *, wait=False):
        self.calls = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        if not wait:
            self.release.set()

    async def complete(self, request):
        self.calls.append(request)
        self.started.set()
        await self.release.wait()
        note = json.loads(request.messages[1]["content"])["note"]
        return CompletionTurn(
            content="",
            tool_calls=(ToolCall(
                id="review",
                name="value_mention_review",
                arguments_json=json.dumps({"mentioned": "health" in note.lower()}),
            ),),
        )


def hooks():
    return HookRegistry.of(
        tuple(spec for spec in REGISTRY.hooks.specs if spec.name == VALUE_UNLINK_HOOK.name),
        owners=frozenset({"values"}), policy=hook_switched_on
    )


def bound(sessions, provider, registry=None):
    return bind_committed(sessions, registry or hooks(), resources=SimpleNamespace(
        value_link_reviewer=ValueLinkReviewer(provider)
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


async def linked(sessions, kind, note):
    """A Value "Health" and one item carrying it, with this Note."""
    async with sessions() as session:
        value = await create_value(session, "Health", "Strength and sleep")
        if kind == "check":
            item = await create_check(session, title="Slept 8 hours?", note=note)
            await toggle_check_value(session, item.id, value.id)
        else:
            item = await create_card(
                session, kind=kind, title="Run 5 km", note=note, value_ids={value.id}
            )
        await session.commit()
        return item.id, value.id


async def unlink(session, kind, item_id, value_id):
    toggle = toggle_check_value if kind == "check" else toggle_card_value
    await toggle(session, item_id, value_id)


@pytest.mark.parametrize("kind", ["goal", "action", "check"])
@pytest.mark.parametrize("note", ["Keeps my health up", "Route along the river"])
async def test_unlinked_note_is_read_alone_and_proposed_only_when_it_mentions_the_value(
    sessions, kind, note
):
    """VL-UNLINK-021 — tests/brd/values.feature"""
    item_id, value_id = await linked(sessions, kind, note)
    provider = MentionProvider()
    registry = hooks()
    sink = bound(sessions, provider, registry)
    try:
        async with sessions() as session:
            await unlink(session, kind, item_id, value_id)
            await session.commit()
        await sink.drain()
        assert len(provider.calls) == 1
        request = provider.calls[0]
        assert request.reasoning_effort == "none"
        assert [tool["function"]["name"] for tool in request.tools] == ["value_mention_review"]
        assert json.loads(request.messages[1]["content"]) == {
            "kind": kind,
            "title": "Slept 8 hours?" if kind == "check" else "Run 5 km",
            "note": note,
            "value": "Health",
            "value_description": "Strength and sleep",
        }
        cues = await pending(sessions)
        mentions = "health" in note.lower()
        assert len(cues) == (1 if mentions else 0)
        if mentions:
            [words] = await say_waiting(sessions, registry)
            item_type = "check" if kind == "check" else "card"
            assert f"]({item_type}:{item_id}): Value «Health»" in words
            assert "Read their current Note first" in words
            assert "workspace_mutator" in words
            assert "Keep the rest of each Note word for word" in words
    finally:
        await sink.close()


async def test_a_deleted_value_asks_once_about_every_note_that_still_mentions_it(sessions):
    """VL-UNLINK-021 — tests/brd/values.feature"""
    provider = MentionProvider()
    registry = hooks()
    sink = bound(sessions, provider, registry)
    try:
        async with sessions() as session:
            value = await create_value(session, "Health", "What matters to me")
            notes = ("For my health", "Health first", "Route along the river", "")
            cards = [
                await create_card(
                    session, kind="action", title=f"Walk {n}", note=note, value_ids={value.id}
                )
                for n, note in enumerate(notes)
            ]
            check = await create_check(session, title="Slept?", note="Sleep is health")
            await toggle_check_value(session, check.id, value.id)
            done = await create_card(
                session, kind="action", title="Old run", note="Health", value_ids={value.id}
            )
            await finish_action(session, done.id)
            await archive_subtree(session, done.id)
            await session.commit()
            value_id, check_id = value.id, check.id
            card_ids = [card.id for card in cards]
        async with sessions() as session:
            await delete_value(session, value_id)
            await session.commit()
        await sink.drain()
        # The empty Note and the archived Action are not asked about.
        assert len(provider.calls) == 4
        [cue] = await pending(sessions)
        assert len(cue.payload) == 3
        [words] = await say_waiting(sessions, registry)
        for item_type, item_id in (
            ("card", card_ids[0]), ("card", card_ids[1]), ("check", check_id)
        ):
            assert f"]({item_type}:{item_id}): Value «Health»" in words
        assert "Route along the river" not in words and "Walk 2" not in words
        assert await pending(sessions) == []
    finally:
        await sink.close()


async def test_an_empty_note_or_a_rollback_starts_no_check(sessions):
    """VL-UNLINK-021 — tests/brd/values.feature"""
    empty_id, value_id = await linked(sessions, "action", "")
    provider = MentionProvider()
    sink = bound(sessions, provider)
    try:
        async with sessions() as session:
            await unlink(session, "action", empty_id, value_id)
            await session.commit()
        async with sessions() as session:
            other = await create_card(
                session, kind="goal", title="Earn", note="Pays for health", value_ids={value_id}
            )
            await session.commit()
        async with sessions() as session:
            await unlink(session, "goal", other.id, value_id)
            await session.rollback()
        await sink.drain()
        assert provider.calls == [] and await pending(sessions) == []
    finally:
        await sink.close()


@pytest.mark.parametrize("change", ["edit", "relink"])
async def test_item_changed_during_the_check_produces_no_cue(sessions, change):
    """VL-UNLINK-021 — tests/brd/values.feature"""
    item_id, value_id = await linked(sessions, "action", "Keeps my health up")
    provider = MentionProvider(wait=True)
    sink = bound(sessions, provider)
    try:
        async with sessions() as session:
            await unlink(session, "action", item_id, value_id)
            await session.commit()
        await asyncio.wait_for(provider.started.wait(), timeout=5)
        async with sessions() as session:
            if change == "edit":
                await update_card_fields(session, item_id, {"note": "Route along the river"})
            else:
                await toggle_card_value(session, item_id, value_id)
            await session.commit()
        provider.release.set()
        await sink.drain()
        assert len(provider.calls) == 1
        assert await pending(sessions) == []
    finally:
        await sink.close()


async def test_a_relink_before_delivery_drops_the_item_from_the_request(sessions):
    """VL-UNLINK-021 — tests/brd/values.feature"""
    item_id, value_id = await linked(sessions, "action", "Keeps my health up")
    registry = hooks()
    sink = bound(sessions, MentionProvider(), registry)
    try:
        async with sessions() as session:
            await unlink(session, "action", item_id, value_id)
            await session.commit()
        await sink.drain()
        assert len(await pending(sessions)) == 1
        async with sessions() as session:
            await toggle_card_value(session, item_id, value_id)
            await session.commit()
        assert await say_waiting(sessions, registry) == []
        assert await pending(sessions) == []
    finally:
        await sink.close()


async def test_the_request_waits_while_hooks_are_held_and_its_switch_drops_it(sessions):
    """VL-UNLINK-021 — tests/brd/values.feature"""
    assert f"{VALUE_UNLINK_HOOK.title} in Profile → Hooks" in MANUAL
    item_id, value_id = await linked(sessions, "goal", "Pays for health")
    provider = MentionProvider()
    registry = hooks()
    sink = bound(sessions, provider, registry)
    try:
        async with sessions() as session:
            await unlink(session, "goal", item_id, value_id)
            await session.commit()
        await sink.drain()
        assert await say_waiting(sessions, registry, hooks_allowed=False) == []
        assert len(await pending(sessions)) == 1
        async with sessions() as session:
            await set_hook_switch(
                session, VALUE_UNLINK_HOOK.name, on=False,
                followers=registry.followers(VALUE_UNLINK_HOOK.name),
            )
            await session.commit()
        assert await pending(sessions) == []
        async with sessions() as session:
            await toggle_card_value(session, item_id, value_id)
            await unlink(session, "goal", item_id, value_id)
            await session.commit()
        await sink.drain()
        assert len(provider.calls) == 1 and await pending(sessions) == []
    finally:
        await sink.close()
