from __future__ import annotations

from types import SimpleNamespace

import pytest
from agent_turns import mutation_turn, route_turn
from hook_helpers import run_hooks
from sqlalchemy import select
from ui_harness import (
    FakeCallback,
    FakeMessage,
    ScriptedTranscriber,
    history_source,
    voice_message_for,
)
from usage_harness import total, tracked_services

from llm_gateway import CompletionTurn, ScriptedProvider
from safwa.features.profile.telegram import command_profile
from safwa.features.tags.model import Tag
from tg_agent_shell.hooks.contracts import BeforeTurn
from tg_agent_shell.proposals.telegram import render_ai_outcome
from tg_agent_shell.telegram import callback_token_handler
from tg_agent_shell.telegram.dialogue import ordinary_text, run_before_turn, voice_message
from tg_agent_shell.telegram.model import CallbackToken
from tg_agent_shell.telegram.services import OwnerAndWritingMiddleware
from tg_agent_shell.usage.model import UsageInterval

pytestmark = pytest.mark.e2e


def timed_provider(clock, seconds):
    class TimedProvider(ScriptedProvider):
        def __init__(self, responses):
            super().__init__(
                CompletionTurn(content=item) if isinstance(item, str) else item
                for item in responses
            )

        async def complete(self, request):
            clock.advance(seconds)
            return await super().complete(request)

    return TimedProvider


async def test_voice_transcription_answer_and_profile_count_once(e2e_harness, monkeypatch):
    """TG-WAIT-026 — tests/brd/tg_agent_shell/telegram_history.feature"""
    services, clock = tracked_services(e2e_harness.sessions, monkeypatch)

    class TimedTranscriber(ScriptedTranscriber):
        async def transcribe(self, clip, *, progress=None):
            clock.advance(45)
            return await super().transcribe(clip, progress=progress)

    advisor, provider = e2e_harness.advisor(
        ["Here is your answer."], plan_required=False,
        provider_factory=timed_provider(clock, 240),
    )
    services.root = advisor
    services.history = history_source(e2e_harness.sessions)
    services.transcriber = TimedTranscriber("Tell me about today")
    message = voice_message_for(1, duration=60)
    message.date = clock.now()

    async def handle(event, data):
        await voice_message(event, data["services"])

    await OwnerAndWritingMiddleware()(handle, message, {"services": services})
    assert len(provider.requests) == 1
    assert any(item.text == "Here is your answer." for item in message.sent_messages)
    assert await total(services) == 345
    clock.advance(1000)
    assert await total(services) == 465
    profile = FakeMessage(2, bot_message=True, answer_as_new=True)
    await command_profile(profile, services)
    assert "Usage time: ~7m" in profile.edits[-1][0]
    async with e2e_harness.sessions() as session:
        rows = list(await session.scalars(select(UsageInterval)))
        assert len(rows) == 2
        assert all(row.event_key.endswith("message:700:1") for row in rows)


async def test_after_answer_hooks_and_system_requests_add_no_usage(e2e_harness, monkeypatch):
    """TG-WAIT-026 — tests/brd/tg_agent_shell/telegram_history.feature"""
    services, clock = tracked_services(e2e_harness.sessions, monkeypatch)
    advisor, provider = e2e_harness.advisor(
        ["The owner's answer.", "A system initiative."], plan_required=False,
        provider_factory=timed_provider(clock, 240),
    )
    services.root = advisor
    services.history = history_source(e2e_harness.sessions)

    async def long_hook(event, context):
        clock.advance(3600)
        await context.publish("Automatic work finished.", "event")

    services.hooks = run_hooks(long_hook)
    message = FakeMessage(3, text="Answer me", bot_message=False, answer_as_new=True)
    message.date = clock.now()

    async def handle(event, data):
        await ordinary_text(event, data["services"])

    await OwnerAndWritingMiddleware()(handle, message, {"services": services})
    assert await total(services) == 360
    # The same entry used by CueRuntime has no owner event or usage scope.
    await run_before_turn(
        message, services,
        BeforeTurn(owner_id=42, chat_id=700, dialogue_revision=0, source="system"),
        lambda: True,
    )
    outcome = await advisor.handle("Say this on your own", dialogue=[])
    await render_ai_outcome(message, services, outcome)
    clock.advance(500)
    assert len(provider.requests) == 2
    assert await total(services) == 360


async def test_review_pause_and_resumed_subagents_are_separate_intervals(
    e2e_harness, monkeypatch,
):
    """TG-PAUSE-027 — tests/brd/tg_agent_shell/telegram_history.feature"""
    services, clock = tracked_services(e2e_harness.sessions, monkeypatch)
    advisor, provider = e2e_harness.advisor(
        [
            route_turn("workspace_mutator"),
            mutation_turn(("tag", {"mode": "create", "name": "Family"})),
            "Created it.", "The Family Tag is saved.",
        ],
        plan_required=False, provider_factory=timed_provider(clock, 60),
    )
    services.root = advisor
    services.history = history_source(e2e_harness.sessions)
    message = FakeMessage(4, text="Add Family tag", bot_message=False, answer_as_new=True)
    message.date = clock.now()
    middleware = OwnerAndWritingMiddleware()

    async def handle(event, data):
        await ordinary_text(event, data["services"])

    await middleware(handle, message, {"services": services})
    assert await total(services) == 120
    clock.advance(600)
    assert await total(services) == 240
    async with e2e_harness.sessions() as session:
        button = await session.scalar(
            select(CallbackToken).where(CallbackToken.action == "proposal_approve")
        )
    screen = message.sent_messages[-1]
    callback = FakeCallback(button.token, screen)
    callback.id = "save-1"
    callback.from_user = SimpleNamespace(id=42, is_bot=False)

    async def press(event, data):
        await callback_token_handler(event, data["services"])

    await middleware(press, callback, {"services": services})
    assert len(provider.requests) == 4
    assert await total(services) == 360
    clock.advance(1000)
    assert await total(services) == 480
    async with e2e_harness.sessions() as session:
        assert (await session.scalar(select(Tag))).name == "Family"
        assert len(list(await session.scalars(select(UsageInterval)))) == 4
