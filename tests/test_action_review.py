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
from safwa.features.cards.action_review import (
    ACTION_REVIEW_EXCHANGES,
    ACTION_REVIEW_HOOK,
    ACTION_REVIEW_PROMPT,
    SIMILAR_ACTIONS,
    ActionReviewer,
)
from safwa.features.cards.model import EnergyType
from safwa.features.cards.use_cases import (
    CARD_CREATED,
    delete_one_card,
    finish_action,
    toggle_card_energy_type,
)
from safwa.features.cards.views import AI_CARDS, CARD_ITEM_TYPE
from safwa.features.onboarding.agent import MANUAL
from safwa.features.profile.api import hook_switched_on, set_hook_switch
from safwa.features.profile.model import ProfileField
from safwa.features.profile.use_cases import set_profile_field
from safwa.features.tags.model import Tag
from safwa.features.tags.use_cases import seed_inbox_tag
from telegram_llm import DialogueMessage
from tg_agent_shell.cues.background import tick
from tg_agent_shell.cues.initiatives import bind_committed
from tg_agent_shell.cues.model import Cue
from tg_agent_shell.hooks.contracts import OnCommitted, RunAndAdvise
from tg_agent_shell.hooks.registry import HookRegistry

ADVICE = "Running costs the body, not focus. Suggest physical instead of cognitive."
OWNER = 42


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

    def context(self, index=0):
        return json.loads(self.calls[index].messages[1]["content"])


class History:
    """The conversation as the window lays it out: seven exchanges, oldest first."""

    def __init__(self):
        self.chats = []

    async def dialogue(self, chat_id):
        self.chats.append(chat_id)
        return [
            DialogueMessage(role=role, content=f"{word} {number}")
            for number in range(1, 8)
            for role, word in (("user", "message"), ("assistant", "answer"))
        ]


class Ranking:
    """The search index, answering with the relevance it is given."""

    def __init__(self):
        self.scores = {}
        self.searches = []

    async def relevance(self, search, views):
        self.searches.append((search, tuple(views)))
        return {(CARD_ITEM_TYPE, item_id): score for item_id, score in self.scores.items()}


def hooks():
    return HookRegistry.of(
        (ACTION_REVIEW_HOOK,), owners=frozenset({"cards"}), policy=hook_switched_on
    )


def bound(sessions, provider, registry=None, *, history=None, ranking=None):
    return bind_committed(sessions, registry or hooks(), resources=SimpleNamespace(
        action_reviewer=ActionReviewer(
            provider, history or History(), OWNER, ranking or Ranking()
        )
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
    fields.setdefault("kind", "action")
    async with sessions() as session:
        card = await create_card(session, **fields)
        await session.commit()
        return card.id


async def effort_points_on(sessions):
    async with sessions() as session:
        await set_profile_field(session, ProfileField.EFFORT_TRACKING, True)
        await session.commit()


def test_one_hook_checks_a_new_action_under_its_own_switch():
    """CD-ACTION-052 — tests/brd/cards.feature"""
    assert ACTION_REVIEW_HOOK in REGISTRY.hooks.specs
    assert ACTION_REVIEW_HOOK.on == (OnCommitted(kind=CARD_CREATED),)
    assert isinstance(ACTION_REVIEW_HOOK.effect, RunAndAdvise)
    assert ACTION_REVIEW_HOOK.agent_related and ACTION_REVIEW_HOOK.switch is None
    assert f"{ACTION_REVIEW_HOOK.title} in Profile → Hooks" in MANUAL
    assert ACTION_REVIEW_EXCHANGES == 5 and SIMILAR_ACTIONS == 10


def test_the_review_knows_every_criterion_and_leaves_the_owner_s_words_alone():
    """CD-ACTION-052 — tests/brd/cards.feature"""
    for criterion in (
        "fits in one day; a result that takes longer is a Goal",
        "has `categories` that fit, what it gives: growth: skills, learning;",
        "has `energy_types` that fit, what it costs: physical: body;",
        "- 3: able to carry on only after a break",
        "only when it clearly does not fit, or the main one is clearly missing",
        "Compare `effort_points` with `similar`, finished ones first.",
        "`effort_points` null means not set",
        "Never advise changing what the user named in the conversation.",
        "Do not judge whether the Action is worth doing.",
        '"Run 20 km", 1 EP, the user asked for 1 EP: clear()',
    ):
        assert criterion in ACTION_REVIEW_PROMPT


@pytest.mark.parametrize("clear", [True, False])
async def test_new_action_is_read_with_the_last_exchanges_and_asked_about_only_when_unclear(
    sessions, clear
):
    """CD-ACTION-052 — tests/brd/cards.feature"""
    provider = ReviewProvider(clear)
    history = History()
    ranking = Ranking()
    registry = hooks()
    sink = bound(sessions, provider, registry, history=history, ranking=ranking)
    try:
        action_id = await created(
            sessions, title="Run 5 km", note="Around the park", schedule="20.12.2026 18:00",
            categories={"rest"}, energy_types={"cognitive"}, effort_points=1,
        )
        await sink.drain()
        assert len(provider.calls) == 1
        request = provider.calls[0]
        assert len(request.messages) == 2
        assert request.reasoning_effort == "none"
        assert [tool["function"]["name"] for tool in request.tools] == ["clear", "unclear"]
        context = provider.context()
        conversation = context.pop("conversation")
        # With Effort Points off the review reads no estimate and searches nothing.
        assert context == {
            "title": "Run 5 km",
            "note": "Around the park",
            "schedule": "20.12.2026 18:00",
            "categories": ["rest"],
            "energy_types": ["cognitive"],
        }
        assert ranking.searches == [] and history.chats == [OWNER]
        for number in range(3, 8):
            assert f"message {number}" in conversation and f"answer {number}" in conversation
        assert "message 2" not in conversation and "answer 2" not in conversation
        cues = await pending(sessions)
        assert len(cues) == (0 if clear else 1)
        if not clear:
            assert cues[0].hook == ACTION_REVIEW_HOOK.name and cues[0].text is None
            [words] = await say_waiting(sessions, registry)
            assert f"- #{action_id} «Run 5 km»: {ADVICE}" in words
            assert "Read the current Actions first" in words
            assert "you looked at the new Actions again and they could be improved" in words
            assert "say briefly what to change and why" in words
            assert "update the Action, or to propose a Goal instead" in words
            assert "Change nothing until they answer" in words
            assert await pending(sessions) == []
    finally:
        await sink.close()


async def test_with_effort_points_on_the_review_compares_closest_estimates_finished_first(
    sessions,
):
    """CD-ACTION-052 — tests/brd/cards.feature"""
    await effort_points_on(sessions)
    ranking = Ranking()
    async with sessions() as session:
        done = await create_card(session, kind="action", title="Run 15 km", effort_points=5)
        await finish_action(session, done.id)
        run = await create_card(session, kind="action", title="Run 10 km", effort_points=3)
        again = await create_card(session, kind="action", title="Run 10 km", effort_points=2)
        await session.flush()
        again.repeat_series_id = run.id
        run.repeat_series_id = run.id
        goal = await create_card(session, kind="goal", title="Run a marathon")
        unset = await create_card(session, kind="action", title="Run fast")
        walks = [
            await create_card(session, kind="action", title=f"Walk {number}", effort_points=1)
            for number in range(1, 11)
        ]
        await session.commit()
        ranking.scores = {
            again.id: 0.9, run.id: 0.8, goal.id: 0.7, unset.id: 0.6, done.id: 0.1,
            **{walk.id: 0.05 - number / 1000 for number, walk in enumerate(walks)},
        }
    provider = ReviewProvider(clear=True)
    sink = bound(sessions, provider, ranking=ranking)
    try:
        action_id = await created(sessions, title="Run 20 km", effort_points=1)
        ranking.scores[action_id] = 1.0
        await sink.drain()
        assert ranking.searches == [("Run 20 km", (AI_CARDS.name,))]
        context = provider.context()
        assert context["effort_points"] == 1
        assert context["similar"] == [
            {"title": "Run 15 km", "effort_points": 5, "finished": True},
            {"title": "Run 10 km", "effort_points": 2, "finished": False},
            *({"title": f"Walk {number}", "effort_points": 1, "finished": False}
              for number in range(1, 9)),
        ]
        assert len(context["similar"]) == SIMILAR_ACTIONS

        # Not set while Effort Points are on, the estimate is read as null.
        await created(sessions, title="Swim")
        await sink.drain()
        assert provider.context(1)["effort_points"] is None
    finally:
        await sink.close()


async def test_a_goal_an_inbox_action_a_rollback_or_an_edit_starts_no_review(sessions):
    """CD-ACTION-052 — tests/brd/cards.feature"""
    provider = ReviewProvider()
    sink = bound(sessions, provider)
    try:
        async with sessions() as session:
            await seed_inbox_tag(session)
            await session.commit()
            inbox_id = await session.scalar(select(Tag.id).where(Tag.name == INBOX_TAG_NAME))
        await created(sessions, kind="goal", title="Learn Spanish")
        await created(sessions, title="Some idea", tag_ids={inbox_id})
        async with sessions() as session:
            await create_card(session, kind="action", title="Run 5 km")
            await session.rollback()
        await sink.drain()
        assert provider.calls == []

        action_id = await created(sessions, title="Run 5 km")
        await sink.drain()
        assert len(provider.calls) == 1
        async with sessions() as session:
            await toggle_card_energy_type(session, action_id, EnergyType.PHYSICAL)
            await session.commit()
        await sink.drain()
        assert len(provider.calls) == 1
    finally:
        await sink.close()


@pytest.mark.parametrize("change", ["edit", "finish", "delete"])
async def test_action_changed_during_review_produces_no_cue(sessions, change):
    """CD-ACTION-052 — tests/brd/cards.feature"""
    provider = ReviewProvider(wait=True)
    sink = bound(sessions, provider)
    try:
        action_id = await created(sessions, title="Run 5 km", energy_types={"cognitive"})
        await asyncio.wait_for(provider.started.wait(), timeout=5)
        async with sessions() as session:
            if change == "edit":
                await toggle_card_energy_type(session, action_id, EnergyType.PHYSICAL)
            elif change == "finish":
                await finish_action(session, action_id)
            else:
                await delete_one_card(session, action_id)
            await session.commit()
        provider.release.set()
        await sink.drain()
        assert len(provider.calls) == 1
        assert await pending(sessions) == []
    finally:
        await sink.close()


async def test_unclear_actions_are_asked_together_and_a_finished_one_is_left_out(sessions):
    """CD-ACTION-052 — tests/brd/cards.feature"""
    provider = ReviewProvider()
    registry = hooks()
    sink = bound(sessions, provider, registry)
    try:
        ids = [
            await created(sessions, title=title)
            for title in ("Run 5 km", "Write the thesis", "Call mom")
        ]
        await sink.drain()
        assert len(await pending(sessions)) == len(provider.calls) == 3
        async with sessions() as session:
            await finish_action(session, ids[2])
            await session.commit()
        said = await say_waiting(sessions, registry)
        assert len(said) == 1
        assert f"#{ids[0]} «Run 5 km»" in said[0]
        assert f"#{ids[1]} «Write the thesis»" in said[0]
        assert "Call mom" not in said[0]
        assert await pending(sessions) == []
    finally:
        await sink.close()


async def test_the_request_waits_while_hooks_are_held_and_its_switch_drops_it(sessions):
    """CD-ACTION-052 — tests/brd/cards.feature"""
    provider = ReviewProvider()
    registry = hooks()
    sink = bound(sessions, provider, registry)
    try:
        await created(sessions, title="Run 5 km")
        await sink.drain()
        assert await say_waiting(sessions, registry, hooks_allowed=False) == []
        assert len(await pending(sessions)) == 1
        async with sessions() as session:
            await set_hook_switch(
                session, ACTION_REVIEW_HOOK.name, on=False,
                followers=registry.followers(ACTION_REVIEW_HOOK.name),
            )
            await session.commit()
        assert await pending(sessions) == []
        await created(sessions, title="Call mom")
        await sink.drain()
        assert len(provider.calls) == 1 and await pending(sessions) == []
    finally:
        await sink.close()
