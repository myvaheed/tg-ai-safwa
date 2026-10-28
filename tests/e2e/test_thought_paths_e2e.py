from __future__ import annotations

import json

import pytest
from advisor_e2e_helpers import mutation_turn, route_turn
from review_e2e_helpers import on_screen, resolve_queued_proposal
from sqlalchemy import select
from telegram_fakes import QueueTestCallback, QueueTestMessage
from ui_harness import history_source, services_for

from llm_gateway import CompletionTurn, ToolCall
from safwa.features.blueprint.hooks import BLUEPRINT_ASKED_HOOK
from safwa.features.cards.model import Card
from safwa.features.cards.telegram import render_card
from safwa.features.cards.use_cases import create_card
from safwa.features.profile.model import ProfileField
from safwa.features.profile.use_cases import set_profile_field
from safwa.features.tags.model import Tag
from safwa.features.tags.use_cases import create_tag
from tg_agent_shell.foundation.errors import DomainError
from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.history import Subsession, current_subsession
from tg_agent_shell.proposals.hooks import PLAN_HOOK
from tg_agent_shell.telegram import callback_token_handler
from tg_agent_shell.telegram.dialogue import run_dialogue_turn

pytestmark = pytest.mark.e2e


async def chat(e2e_harness, responses, *, asked=False):
    history = history_source(e2e_harness.sessions)
    root, provider = e2e_harness.advisor(
        responses,
        subagents=(e2e_harness.subagent("workspace_mutator"), e2e_harness.subagent("blueprint", history=history)),
        checks=(PLAN_HOOK, BLUEPRINT_ASKED_HOOK) if asked else (PLAN_HOOK,),
    )
    services = services_for(e2e_harness.sessions, root=root)
    services.history = history
    message = QueueTestMessage(owner_id=42, is_bot=False, answer_as_new=True)
    message.chat.id = 42
    return services, message, provider


async def say(services, message, text):
    message.text = text
    await services.chat.keep(message, kind=MessageKind.DIALOGUE_USER.value)
    await run_dialogue_turn(message, services, text, message.message_id)


async def press(services, message, text):
    button = next(button for row in message.markups[-1].inline_keyboard for button in row if button.text == text)
    await callback_token_handler(QueueTestCallback(button.callback_data.split(":", 1)[1], message), services)
    return button


@pytest.mark.parametrize("saved", [False, True])
@pytest.mark.parametrize("plan", [False, True])
@pytest.mark.parametrize("default_on", [False, True])
async def test_thought_paths_keep_content_decisions_and_the_saved_source(e2e_harness, saved, plan, default_on):
    """CD-THOUGHT-075 — tests/brd/cards.feature"""
    fields = {"title": "Создать Цель и Действия", "note": "Цель: пробежать 5 км. Действия: купить кроссовки, пробежать 1 км.", "blocked_description": "Жду <ответ & дату>"}
    initial = [] if saved else [
        route_turn("workspace_mutator"),
        mutation_turn(
            ("tag", {"mode": "create", "name": "Kept before the Thought"}),
            ("thought", {"mode": "create", **fields}),
            ("tag", {"mode": "create", "name": "Discarded tail"}),
        ),
    ]
    if plan:
        after = [
            CompletionTurn(content="", tool_calls=(ToolCall(id="open-blueprint", name="sub", arguments_json='{"name":"blueprint"}'),)),
            CompletionTurn(content="asked"),
            CompletionTurn(content="Как поймём, что Цель достигнута?"),
        ]
    else:
        after = [
            route_turn("workspace_mutator"),
            mutation_turn(
                ("goal", {"mode": "create", "title": "Пробежать 5 км", "note": "Финишировать на забеге 5 км."}),
                ("action", {"mode": "create", "title": "Купить кроссовки"}),
                ("action", {"mode": "create", "title": "Пробежать 1 км"}),
            ),
            CompletionTurn(content="Готово."), CompletionTurn(content="Сохранено."),
        ]
    services, message, provider = await chat(e2e_harness, [*initial, *after], asked=plan)
    async with e2e_harness.sessions() as session:
        await set_profile_field(session, ProfileField.THOUGHTS_BY_DEFAULT, default_on)
        source = await create_card(session, kind="thought", **fields) if saved else None
        await session.commit()
        source_id = source.id if source else None
    if saved:
        await render_card(message, services, source_id)
    else:
        await say(services, message, "Запиши эту мысль.")
        await resolve_queued_proposal(e2e_harness, services, message, on_screen(e2e_harness), "proposal_approve")
        assert message.buttons() == ["Quality Plan", "Direct Create", "✅ Create", "🗑 Discard"]
    clicked = await press(services, message, "Quality Plan" if plan else "Direct Create")
    assert not services.turn.active
    all_messages = json.dumps(provider.calls, ensure_ascii=False)
    for value in fields.values():
        assert value in all_messages
    async with e2e_harness.sessions() as session:
        thoughts = list(await session.scalars(select(Card).where(Card.kind == "thought")))
        assert [item.id for item in thoughts] == ([source_id] if saved else [])
        names = list(await session.scalars(select(Tag.name)))
        assert names == ([] if saved else ["Kept before the Thought"])
        if plan:
            assert (await current_subsession(session, 42)).kind == "blueprint"
        else:
            assert len(list(await session.scalars(select(Card)))) == int(saved)
    if not plan:
        for _ in range(3):
            await resolve_queued_proposal(e2e_harness, services, message, on_screen(e2e_harness), "proposal_approve")
        async with e2e_harness.sessions() as session:
            cards = list(await session.scalars(select(Card).order_by(Card.id)))
            assert [(item.kind, item.title) for item in cards if item.kind != "thought"] == [
                ("goal", "Пробежать 5 км"), ("action", "Купить кроссовки"), ("action", "Пробежать 1 км"),
            ]
    calls = len(provider.calls)
    await callback_token_handler(QueueTestCallback(clicked.callback_data.split(":", 1)[1], message), services)
    assert len(provider.calls) == calls


async def test_a_stale_thought_does_not_launch_or_write_its_queue(e2e_harness):
    """CD-THOUGHT-075 — tests/brd/cards.feature"""
    services, message, provider = await chat(e2e_harness, [
        route_turn("workspace_mutator"), mutation_turn(("thought", {"mode": "create", "title": "Idea"})),
    ])
    await say(services, message, "Запиши мысль.")
    async with e2e_harness.sessions() as session:
        await create_tag(session, "Workspace moved on")
        await session.commit()
    await press(services, message, "Direct Create")
    assert len(provider.calls) == 2
    assert "workspace changed" in message.bot.drawn[-1].lower()
    async with e2e_harness.sessions() as session:
        assert list(await session.scalars(select(Card))) == []
    assert not services.turn.active and on_screen(e2e_harness) is None


async def test_failed_earlier_saves_prevent_processing_the_thought(e2e_harness, monkeypatch):
    """CD-THOUGHT-075 — tests/brd/cards.feature"""
    from safwa.features.tags import proposal as tags_proposal

    services, message, provider = await chat(e2e_harness, [
        route_turn("workspace_mutator"),
        mutation_turn(
            ("tag", {"mode": "create", "name": "Must save first"}),
            ("thought", {"mode": "create", "title": "Idea"}),
        ),
    ])
    await say(services, message, "Сохрани Tag и мысль.")
    await resolve_queued_proposal(e2e_harness, services, message, on_screen(e2e_harness), "proposal_approve")

    async def failed(*args, **kwargs):
        raise DomainError("Could not save the earlier Tag.")

    monkeypatch.setattr(tags_proposal, "create_tag", failed)
    await press(services, message, "Direct Create")
    assert len(provider.calls) == 2
    assert "earlier Saves failed" in message.bot.drawn[-1]
    async with e2e_harness.sessions() as session:
        assert list(await session.scalars(select(Card))) == []
        assert list(await session.scalars(select(Tag))) == []
    assert not services.turn.active and on_screen(e2e_harness) is None


async def test_quality_plan_from_a_saved_thought_leaves_an_open_subsession_first(e2e_harness):
    """CD-THOUGHT-075 — tests/brd/cards.feature"""
    enter = CompletionTurn(content="", tool_calls=(ToolCall(id="open-blueprint", name="sub", arguments_json='{"name":"blueprint"}'),))
    services, message, provider = await chat(e2e_harness, [
        enter, CompletionTurn(content="asked"), CompletionTurn(content="Old question?"),
        enter, CompletionTurn(content="asked"), CompletionTurn(content="New question?"),
    ], asked=True)
    async with e2e_harness.sessions() as session:
        thought = await create_card(session, kind="thought", title="Another idea", note="Plan a garden.")
        await session.commit()
        thought_id = thought.id
    await say(services, message, "Plan an item with me.")
    async with e2e_harness.sessions() as session:
        old_id = (await current_subsession(session, 42)).id
    await render_card(message, services, thought_id)
    await press(services, message, "Quality Plan")
    async with e2e_harness.sessions() as session:
        assert (await session.get(Subsession, old_id)).through_message_id is not None
        assert (await current_subsession(session, 42)).id != old_id
        assert await session.get(Card, thought_id) is not None
    assert not provider.responses and not services.turn.active
