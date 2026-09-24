"""Onboarding: the notice before the first answer, the tips after a save, the proposal to stop,
and the manual the subagent explains Safwa from.

What the model does with a tip or a question is in `tests/e2e/test_onboarding_e2e.py`, where
the provider is scripted; here are the hooks, the words they make and the proposal's rules.
"""

from __future__ import annotations

import asyncio
import html
import re
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from marks import read_kind_mark
from sqlalchemy import select
from ui_harness import FakeMessage, services_for

from safwa.bootstrap.modules import AGENTS, FEATURE_COMMANDS, PROPOSALS, REGISTRY
from safwa.features.cards.hooks import BLOCKER_HOOK
from safwa.features.cards.use_cases import create_card, finish_action
from safwa.features.checks.use_cases import create_check, resolve_check
from safwa.features.onboarding.agent import MANUAL, ONBOARDING_AGENT
from safwa.features.onboarding.hooks import (
    NOTICE_HOOK,
    ONBOARDING_HOOK,
    ONBOARDING_NOTICE,
    ONBOARDING_TIP_ITEMS,
    onboarding_request,
)
from safwa.features.onboarding.model import OnboardingNotice
from safwa.features.onboarding.proposal import OnboardingProposalHandler
from safwa.features.profile.api import hook_switched_on, set_hook_switch
from safwa.features.values.use_cases import create_value
from safwa.foundation.workspace import Workspace
from telegram_llm import HistoryEntry
from tg_agent_shell.ai.contracts import AgentChange, ChangeAction
from tg_agent_shell.ai.outcome import AIOutcome, AIOutcomeKind
from tg_agent_shell.cues.background import tick
from tg_agent_shell.cues.initiatives import bind_committed
from tg_agent_shell.cues.model import Cue
from tg_agent_shell.cues.queue import add_hook_cue
from tg_agent_shell.cues.runtime import CueRuntime
from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.hooks.contracts import BeforeTurn
from tg_agent_shell.hooks.registry import HookRegistry
from tg_agent_shell.proposals.api import (
    ApplyContext,
    PreparationContext,
    ToolPreparationError,
    World,
)
from tg_agent_shell.proposals.model import ProposalChange
from tg_agent_shell.proposals.store import ProposalStore
from tg_agent_shell.proposals.use_cases import approve_proposal
from tg_agent_shell.telegram import SHELL_COMMANDS
from tg_agent_shell.telegram.dialogue import run_dialogue_turn


def onboarding_hooks() -> HookRegistry:
    """The feature's two hooks under Safwa's own switch policy, and nothing else."""
    return HookRegistry.of(
        (ONBOARDING_HOOK, NOTICE_HOOK), owners=frozenset({"onboarding"}), policy=hook_switched_on
    )


# ------------------------------------------------------------------- the notice


class Answering:
    """An Advisor that answers every turn in one line."""

    async def handle(self, request, *, source_message_id=None, dialogue=None):
        del request, source_message_id, dialogue
        return AIOutcome(AIOutcomeKind.ANSWER, "The answer.")


async def _no_dialogue(_chat_id=None, *, source_message=None):
    del source_message
    return []


def _owner_services(sessions):
    services = services_for(sessions)
    services.root = Answering()
    services.history = SimpleNamespace(dialogue=_no_dialogue)
    services.hooks = onboarding_hooks()
    return services


async def _owner_turn(services, message_id: int) -> list[tuple[str | None, str]]:
    """One owner turn; what it put in the chat, as kind and text."""
    message = FakeMessage(message_id, text="Hello", bot_message=False, answer_as_new=True)
    source = HistoryEntry(
        message_id=message_id, sender_id=42, role="user", text="Hello",
        created_at=datetime.now(UTC), kind=MessageKind.DIALOGUE_USER.value,
    )
    await run_dialogue_turn(message, services, "Hello", source)
    return [read_kind_mark(item.text) for item in message.sent_messages]


# Published as plain text, escaped on the way into the chat.
SENT_NOTICE = html.escape(ONBOARDING_NOTICE)


def _notices(said: list[tuple[str | None, str]]) -> list[str | None]:
    return [kind for kind, text in said if text == SENT_NOTICE]


async def test_ob_notice_001_the_first_turn_is_preceded_by_the_notice_and_only_the_first(
    sessions,
):
    """OB-NOTICE-001 — tests/brd/onboarding.feature"""
    services = _owner_services(sessions)

    first = await _owner_turn(services, 700)

    # Safwa's own words, standing before the answer.
    assert _notices(first) == [MessageKind.DIALOGUE_ASSISTANT.value]
    texts = [text for _, text in first]
    assert texts.index(SENT_NOTICE) < next(
        index for index, text in enumerate(texts) if "The answer." in text
    )
    assert _notices(await _owner_turn(services, 701)) == []
    # A restart is a new process: what was sent is a row, not a memory.
    assert _notices(await _owner_turn(_owner_services(sessions), 702)) == []
    async with sessions() as session:
        await set_hook_switch(session, ONBOARDING_HOOK.name, on=False)
        await set_hook_switch(session, ONBOARDING_HOOK.name, on=True)
        await session.commit()
    assert _notices(await _owner_turn(services, 703)) == []


async def test_ob_notice_001_no_notice_comes_while_onboarding_is_off(sessions):
    """OB-NOTICE-001 — tests/brd/onboarding.feature"""
    async with sessions() as session:
        await set_hook_switch(session, ONBOARDING_HOOK.name, on=False)
        await session.commit()

    assert _notices(await _owner_turn(_owner_services(sessions), 710)) == []
    async with sessions() as session:
        assert await session.get(OnboardingNotice, 1) is None


class CueChat:
    def __init__(self) -> None:
        self.said: list[str] = []

    async def send_parts(self, _message, text, *, kind, event_id=None, replace=None):
        del event_id, replace
        self.said.append(f"{kind}: {text}")


class HeldSessions:
    """The session factory, holding the next session it opens once asked to."""

    def __init__(self, sessions) -> None:
        self.sessions = sessions
        self.hold = False
        self.held = asyncio.Event()

    def __call__(self):
        return self._open()

    @asynccontextmanager
    async def _open(self):
        if self.hold:
            self.hold = False
            self.held.set()
            await asyncio.Future()
        async with self.sessions() as session:
            yield session


def _cue_runtime(sessions, chat: CueChat, monkeypatch) -> CueRuntime:
    services = SimpleNamespace(
        sessions=sessions, turn=services_for(sessions).turn, root=Answering(),
        hooks=onboarding_hooks(), features=None, chat=chat,
        history=SimpleNamespace(dialogue=_no_dialogue),
    )
    services.root.reviews = ProposalStore()
    runtime = CueRuntime(services, bot=None, owner_id=42)  # type: ignore[arg-type]

    async def render(_message, _services, outcome, *, kind, event_id):
        chat.said.append(f"{kind}: {outcome.message}")

    monkeypatch.setattr("tg_agent_shell.cues.runtime.render_ai_outcome", render)
    return runtime


async def test_ob_notice_001_a_turn_of_safwas_own_carries_it_too(sessions, monkeypatch):
    """OB-NOTICE-001 — tests/brd/onboarding.feature"""
    chat = CueChat()
    runtime = _cue_runtime(sessions, chat, monkeypatch)

    assert await runtime.can_speak() is True
    assert await runtime.speak("f" * 32, "Sprint 1 is over.") is True
    runtime.release()

    assert chat.said == [
        f"{MessageKind.DIALOGUE_ASSISTANT.value}: {SENT_NOTICE}",
        f"{MessageKind.CUE.value}: The answer.",
    ]


async def test_ob_notice_001_the_owner_arriving_mid_check_carries_the_notice_instead(
    sessions, monkeypatch
):
    """OB-NOTICE-001 — tests/brd/onboarding.feature"""
    chat = CueChat()
    held = HeldSessions(sessions)
    runtime = _cue_runtime(held, chat, monkeypatch)

    assert await runtime.can_speak() is True
    held.hold = True
    speaking = asyncio.create_task(runtime.speak("g" * 32, "Sprint 1 is over."))
    await asyncio.wait_for(held.held.wait(), 2)
    runtime.services.turn.cancel()

    assert await asyncio.wait_for(speaking, 2) is False
    assert chat.said == []
    async with sessions() as session:
        assert await session.get(OnboardingNotice, 1) is None
    # The owner's own turn is the first one that reaches the chat.
    assert _notices(await _owner_turn(_owner_services(sessions), 720)) == [
        MessageKind.DIALOGUE_ASSISTANT.value
    ]


# ------------------------------------------------------------------------- tips


async def _pending(sessions) -> list[tuple[str | None, list]]:
    async with sessions() as session:
        return [
            (cue.hook, cue.payload)
            for cue in await session.scalars(select(Cue).order_by(Cue.id))
        ]


async def test_ob_tip_002_a_value_saved_by_hand_or_by_proposal_is_owed_a_tip(sessions):
    """OB-TIP-002 — tests/brd/onboarding.feature"""
    sink = bind_committed(sessions, REGISTRY.hooks)
    async with sessions() as session:
        by_hand = await create_value(session, "Health")
        await session.commit()
    # One in-memory connection: what a commit hands on is let finish before the next write.
    await sink.drain()
    reviews = ProposalStore()
    async with sessions() as session:
        revision = (await session.get(Workspace, 1)).revision
    proposal = reviews.open_proposal(
        message="A Value.",
        workspace_revision=revision,
        changes=[ProposalChange(entity="value", action=ChangeAction.CREATE, values={"name": "Calm"})],
    )
    async with sessions() as session:
        (proposed,) = await approve_proposal(session, reviews, PROPOSALS, proposal.id)
    await sink.drain()

    assert await _pending(sessions) == [
        (ONBOARDING_HOOK.name, [["value.created", by_hand.id]]),
        (ONBOARDING_HOOK.name, [["value.created", proposed]]),
    ]
    words = await REGISTRY.hooks.prepare(
        sessions, ONBOARDING_HOOK.name,
        [["value.created", by_hand.id], ["value.created", proposed]],
    )
    assert words == (
        "Onboarding. The user just:\n"
        f"- created a Value [Health](value:{by_hand.id});\n"
        f"- created a Value [Calm](value:{proposed}).\n"
        'Call route("onboarding") with these.'
    )


async def test_ob_tip_002_many_items_are_cited_up_to_five_and_the_rest_counted(sessions):
    """OB-TIP-002 — tests/brd/onboarding.feature"""
    async with sessions() as session:
        values = [await create_value(session, f"Value {index}") for index in range(7)]
        await session.commit()
        words = await onboarding_request(
            session, [["value.created", value.id] for value in values]
        )

    items = [line for line in words.splitlines() if line.startswith("- ")]
    assert len(items) == ONBOARDING_TIP_ITEMS + 1
    assert items[-1] == f"- and {7 - ONBOARDING_TIP_ITEMS} more."
    assert "Value 5" not in words and "Value 6" not in words


async def test_ob_tip_002_each_item_is_one_line_with_its_state_as_it_is_now(sessions):
    """OB-TIP-002 — tests/brd/onboarding.feature"""
    async with sessions() as session:
        milk = await create_check(session, title="Milk", repeatable=True)
        posture = await create_check(session, title="Posture straight?")
        market = await create_card(
            session, kind="action", title="Go to the market", stage="today", effort_points=1,
            check_ids={milk.id},
        )
        gone = await create_value(session, "Gone")
        await session.flush()
        await session.delete(gone)
        await finish_action(session, market.id, check_outcomes={milk.id: "passed"})
        await resolve_check(session, posture.id, "missed")
        await session.commit()
        words = await onboarding_request(
            session,
            [
                ["card.created", market.id], ["card.today", market.id],
                ["check.answered", milk.id], ["card.done", market.id],
                ["value.created", gone.id], ["check.answered", posture.id],
            ],
        )

    # The Card created into Today and finished is one line, with the Check its Done screen
    # answered; a Check answered on its own is a line of its own; the deleted Value is gone.
    assert [line for line in words.splitlines() if line.startswith("- ")] == [
        f"- created and finished a Card [Go to the market](card:{market.id}) — an Action in "
        f"Done, 1 Check, no Value; answered its Check [Milk](check:{milk.id}) Passed;",
        f"- answered a Check [Posture straight?](check:{posture.id}) — on no Card, "
        "answered Missed.",
    ]


async def test_ob_tip_002_a_turn_that_does_not_route_loses_the_tip(sessions):
    """OB-TIP-002 — tests/brd/onboarding.feature"""
    async with sessions() as session:
        value = await create_value(session, "Health")
        await add_hook_cue(session, hook=ONBOARDING_HOOK.name, items=[["value.created", value.id]])
        await session.commit()
    said: list[str] = []

    async def speak(_event_id: str, text: str) -> bool:
        # Whatever the Advisor did with it, the answer reached the chat.
        said.append(text)
        return True

    async def open_gate() -> bool:
        return True

    async def delivered(_event_id: str) -> bool:
        return True

    async def prepare(hook, items):
        return await REGISTRY.hooks.prepare(sessions, hook, items)

    kwargs = {"gate": open_gate, "speak": speak, "delivered": delivered, "prepare": prepare}
    assert await tick(sessions, **kwargs) is True
    assert await _pending(sessions) == []
    assert await tick(sessions, **kwargs) is False
    assert len(said) == 1


async def test_ob_tip_003_a_tip_waits_behind_an_open_screen(sessions):
    """OB-TIP-003 — tests/brd/onboarding.feature"""
    async with sessions() as session:
        value = await create_value(session, "Health")
        await add_hook_cue(session, hook=ONBOARDING_HOOK.name, items=[["value.created", value.id]])
        await session.commit()
    reviews = ProposalStore()
    reviews.open_proposal(
        message="Rename it?", workspace_revision=1,
        changes=[ProposalChange(entity="value", action=ChangeAction.UPDATE, values={"name": "X"})],
    )
    services = SimpleNamespace(
        sessions=sessions, turn=services_for(sessions).turn,
        root=SimpleNamespace(reviews=reviews), hooks=REGISTRY.hooks,
    )
    runtime = CueRuntime(services, bot=None, owner_id=42)  # type: ignore[arg-type]

    assert await runtime.can_speak() is False
    assert [hook for hook, _ in await _pending(sessions)] == [ONBOARDING_HOOK.name]


async def test_ob_tip_003_a_tip_and_a_blocker_are_one_request_both_asked(sessions):
    """OB-TIP-003 — tests/brd/onboarding.feature"""
    async with sessions() as session:
        card = await create_card(
            session, kind="action", title="Call the bank", effort_points=1,
            blocked=True, blocked_description="Line is busy",
        )
        await add_hook_cue(session, hook=BLOCKER_HOOK.name, items=[card.id])
        await add_hook_cue(session, hook=ONBOARDING_HOOK.name, items=[["card.created", card.id]])
        await session.commit()
    said: list[str] = []

    async def speak(_event_id: str, text: str) -> bool:
        said.append(text)
        return True

    async def yes() -> bool:
        return True

    async def delivered(_event_id: str) -> bool:
        return True

    async def prepare(hook, items):
        return await REGISTRY.hooks.prepare(sessions, hook, items)

    assert await tick(sessions, gate=yes, speak=speak, delivered=delivered, prepare=prepare)

    (text,) = said
    assert "«Call the bank»: Line is busy" in text
    assert text.index("Blocked since") < text.index("Onboarding. The user just:")
    assert 'Call route("onboarding")' in text


# ------------------------------------------------------------------------- stop


def _stop() -> AgentChange:
    return AgentChange(entity="onboarding", action=ChangeAction.UPDATE, values={"onboarding": "off"})


def _preparing(session) -> PreparationContext:
    return PreparationContext(
        session=session, world=World(revision=0, timezone="UTC"), provider=None,  # type: ignore[arg-type]
        query_runner=None, views=frozenset(),  # type: ignore[arg-type]
    )


async def test_ob_stop_005_saving_it_is_the_profile_switch_and_drops_the_tips_owed(sessions):
    """OB-STOP-005 — tests/brd/onboarding.feature"""
    async with sessions() as session:
        await add_hook_cue(session, hook=ONBOARDING_HOOK.name, items=[["value.created", 1]])
        await add_hook_cue(session, hook=BLOCKER_HOOK.name, items=[1])
        await session.commit()
        revision = (await session.get(Workspace, 1)).revision
    handler = OnboardingProposalHandler()
    async with sessions() as session:
        prepared = await handler.prepare(_preparing(session), _stop())
    assert prepared.values == {"onboarding": "off"}
    change = ProposalChange(entity="onboarding", action=ChangeAction.UPDATE, values=prepared.values)

    async with sessions() as session:
        assert await handler.apply(ApplyContext(session=session, views=frozenset()), change) == []
        await session.commit()

    async with sessions() as session:
        assert await hook_switched_on(session, ONBOARDING_HOOK.name) is False
        # Only what the switch turns: the notice goes with it, the workspace does not move.
        assert await hook_switched_on(session, BLOCKER_HOOK.name) is True
        assert (await session.get(Workspace, 1)).revision == revision
    assert [hook for hook, _ in await _pending(sessions)] == [BLOCKER_HOOK.name]
    # The notice follows the tips' switch.
    assert not [
        item async for item in onboarding_hooks().evaluate(BeforeTurn(42, 42, 0), sessions)
    ]


async def test_ob_stop_005_a_proposal_to_stop_it_when_it_is_off_is_refused(sessions):
    """OB-STOP-005 — tests/brd/onboarding.feature"""
    async with sessions() as session:
        await set_hook_switch(session, ONBOARDING_HOOK.name, on=False)
        await session.commit()

    async with sessions() as session:
        with pytest.raises(ToolPreparationError) as refused:
            await OnboardingProposalHandler().prepare(_preparing(session), _stop())

    assert refused.value.code == "already_off"
    assert "the switch is in Profile. Propose nothing." in refused.value.hint


# ----------------------------------------------------------------------- manual


def test_ob_manual_006_the_manual_names_only_what_the_application_registers():
    """OB-MANUAL-006 — tests/brd/onboarding.feature"""
    commands = {screen.command for screen in (*FEATURE_COMMANDS, *SHELL_COMMANDS) if screen.command}
    titles = {screen.title for screen in FEATURE_COMMANDS if screen.title}
    switches = {hook.title for hook in REGISTRY.hooks.agent_related}
    routes = {agent.name for agent in AGENTS}

    named_commands = set(re.findall(r"(?<![\w/])/([a-z_]+)\b", MANUAL))
    menu = next(line for line in MANUAL.splitlines() if line.startswith("- The menu, /start:"))
    named_switches = next(
        line for line in MANUAL.splitlines() if line.startswith("- Switches in the Profile:")
    )

    assert named_commands and named_commands <= commands
    assert set(re.findall(r'route\("([a-z_]+)"\)', ONBOARDING_AGENT.instructions)) <= routes
    # The menu and the switches are listed whole, so a new one is a line the manual gains.
    assert set(re.findall(r'"([^"]+)"', menu)) == titles
    assert set(re.findall(r'"([^"]+)"', named_switches)) == switches
