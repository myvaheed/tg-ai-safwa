"""A proposal turned into another call before Save (PR-SWITCH-037), in the example application.

The ledger here offers to keep a new entry as a memo instead: a made-up entity of the test's
own, so the switch is the shell's and nothing in it knows either entity. Nothing here
imports Safwa.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import replace
from typing import Any

import pytest
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from wallet import app
from wallet.ledger.model import Entry
from wallet.ledger.module import MODULE as LEDGER
from wallet.world import ledger_world
from wallet_harness import entry_script, live_token, press, seed_lists, take_a_turn

from tg_agent_shell.ai.contracts import AgentChange, ToolInput
from tg_agent_shell.proposals.api import (
    ChangeAction,
    MutationToolSpec,
    PreparedChange,
    ProposalChange,
    ProposalScreen,
    ProposalSwitch,
    ShownField,
    ToolPreparationError,
    entity_change,
)
from tg_agent_shell.proposals.render import USER_CHOSE_NEXT
from tg_agent_shell.registry import Registry
from tg_agent_shell.telegram.manifest import FeatureModule, ProposalContribution

KEEP_AS_MEMO = "📝 Keep as a memo"
MEMO_ID = 7


class MemoInput(ToolInput):
    mode: str = Field(description="create")
    text: str = Field(description="The memo.")


class Memos:
    """The test's own entity: a memo, kept in a list rather than a table."""

    entity = "memo"
    version_model: type[Any] | None = None

    def __init__(self) -> None:
        self.written: list[str] = []
        self.conversations: list[str] = []

    async def prepare(self, context: Any, change: AgentChange) -> PreparedChange:
        self.conversations.append(context.conversation)
        if change.values["text"] == "Refused":
            raise ToolPreparationError("invalid_value", "A memo cannot say Refused.", "Retry.")
        return PreparedChange(values={"text": change.values["text"]}, expected_version=None)

    async def apply(self, context: Any, change: ProposalChange) -> list[int]:
        self.written.append(change.values["text"])
        return [MEMO_ID]


class MemoPresenter:
    entity = "memo"

    def raw_details(self, change: AgentChange) -> list[str]:
        return [f"Text: {change.values['text']}"]

    async def details(
        self, session: AsyncSession, change: ProposalChange, fallback: AgentChange | None
    ) -> list[str]:
        return [f"Text: {change.values['text']}"]

    async def summary(
        self, session: AsyncSession, change: ProposalChange, details: list[str]
    ) -> str:
        return f"Keep the memo “{change.values['text']}”"

    async def screen(
        self, session: AsyncSession, changes: Sequence[ProposalChange]
    ) -> ProposalScreen | None:
        return ProposalScreen(
            item="Memo", fields=(ShownField("Text", None, changes[-1].values["text"]),)
        )


def _memo_of(text: str | None = None) -> ProposalSwitch:
    async def rewrite(context: Any, change: ProposalChange) -> AgentChange:
        return AgentChange(
            entity="memo",
            action=ChangeAction.CREATE,
            values={"text": text or change.values["note"]},
        )

    return ProposalSwitch(
        label=KEEP_AS_MEMO,
        offered=lambda change: change.action is ChangeAction.CREATE,
        rewrite=rewrite,
    )


def _registry(memos: Memos, switch: ProposalSwitch) -> Registry:
    """The example's own features, with the switch on its entries and the memo beside them."""
    ledger = replace(LEDGER, proposals=(replace(LEDGER.proposals[0], switches=(switch,)),))
    memo = FeatureModule(
        name="memos",
        proposals=(
            ProposalContribution(
                handler=memos,
                tool=MutationToolSpec(
                    name="memo",
                    input_model=MemoInput,
                    description="Keep a memo.",
                    to_change=entity_change("memo"),
                ),
                presenter=MemoPresenter(),
            ),
        ),
    )
    modules = tuple(ledger if module is LEDGER else module for module in app.MODULES)
    return Registry.of((*modules, memo), world=ledger_world)


async def _proposed(wallet_bot, monkeypatch, switch: ProposalSwitch, *, answer: str = "Done."):
    memos = Memos()
    monkeypatch.setattr(app, "REGISTRY", _registry(memos, switch))
    seeding = await wallet_bot.start()
    ids = await seed_lists(seeding.sessions)
    running = await wallet_bot.start(*entry_script(ids, answer=answer))
    await take_a_turn(running, "I spent 12.50 on lunch out of Cash")
    return running, memos


def _buttons(running) -> list[str]:
    """The buttons of the review the turn stopped at."""
    markup = running.message.markups[-1]
    return [button.text for row in markup.inline_keyboard for button in row]


def _entry_result(running) -> dict[str, Any]:
    """What the bookkeeper read back for its own entry call once the owner answered."""
    for request in reversed(running.provider.requests):
        for message in request.messages:
            if message.get("role") == "tool" and message.get("tool_call_id") == "mutation-1":
                return json.loads(message["content"])
    raise AssertionError("the entry call was never answered")


async def test_a_switch_is_a_button_above_the_decision(wallet_bot, monkeypatch):
    """PR-SWITCH-037 — tests/brd/tg_agent_shell/proposals.feature"""
    running, _memos = await _proposed(wallet_bot, monkeypatch, _memo_of())

    assert "Ledger entry" in running.chat[-1]
    assert _buttons(running) == [KEEP_AS_MEMO, "✅ Create", "🗑 Discard"]


async def test_saving_the_switched_proposal_writes_the_other_call_and_answers_the_first(
    wallet_bot, monkeypatch
):
    """PR-SWITCH-037 — tests/brd/tg_agent_shell/proposals.feature"""
    running, memos = await _proposed(wallet_bot, monkeypatch, _memo_of(), answer="Kept a memo.")

    await press(running, "proposal_switch")
    assert "Memo" in running.chat[-1] and "Lunch" in running.chat[-1]
    assert "Ledger entry" not in running.chat[-1]
    await press(running, "proposal_approve")

    async with running.sessions() as session:
        assert list(await session.scalars(select(Entry))) == []
    assert memos.written == ["Lunch"]
    result = _entry_result(running)
    assert (result["status"], result["entity"], result["affected_ids"]) == (
        "approved",
        "memo",
        [MEMO_ID],
    )
    # Told only what the memo is, the entry call would still read as unfinished.
    assert result["next"] == USER_CHOSE_NEXT
    switched = result["user_chose_on_screen"]
    assert "ledger entry" in switched["proposed"]
    assert switched["saved"] == "Keep the memo “Lunch”"
    assert "Kept a memo." in running.chat[-1]
    assert len(running.provider.requests) == 5


async def test_ag_session_063_a_switched_call_is_prepared_with_no_conversation(
    wallet_bot, monkeypatch
):
    """AG-SESSION-063 — tests/brd/tg_agent_shell/agents.feature"""
    running, memos = await _proposed(wallet_bot, monkeypatch, _memo_of())

    await press(running, "proposal_switch")

    assert memos.conversations == [""]


async def test_a_refused_switch_leaves_the_proposal_as_it_was(wallet_bot, monkeypatch):
    """PR-SWITCH-037 — tests/brd/tg_agent_shell/proposals.feature"""
    running, memos = await _proposed(wallet_bot, monkeypatch, _memo_of("Refused"))

    await press(running, "proposal_switch")
    assert "A memo cannot say Refused." in running.chat[-1]
    assert "Ledger entry" in running.chat[-1]
    await press(running, "proposal_approve")

    async with running.sessions() as session:
        assert len(list(await session.scalars(select(Entry)))) == 1
    assert memos.written == []


async def test_discard_drops_the_switched_proposal(wallet_bot, monkeypatch):
    """PR-SWITCH-037 — tests/brd/tg_agent_shell/proposals.feature"""
    running, memos = await _proposed(wallet_bot, monkeypatch, _memo_of(), answer="Nothing kept.")

    await press(running, "proposal_switch")
    await press(running, "proposal_reject")

    async with running.sessions() as session:
        assert list(await session.scalars(select(Entry))) == []
    assert memos.written == []
    result = _entry_result(running)
    assert result["status"] == "discarded"
    assert USER_CHOSE_NEXT not in result["next"]


async def test_a_switch_its_feature_does_not_offer_draws_no_button(wallet_bot, monkeypatch):
    """PR-SWITCH-037 — tests/brd/tg_agent_shell/proposals.feature"""
    switch = replace(_memo_of(), offered=lambda change: False)
    running, _memos = await _proposed(wallet_bot, monkeypatch, switch)

    assert KEEP_AS_MEMO not in _buttons(running)
    with pytest.raises(AssertionError, match="no live proposal_switch"):
        await live_token(running, "proposal_switch")
