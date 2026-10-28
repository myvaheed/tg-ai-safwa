"""A field shown on every proposal of a kind (PR-SCREEN-039) and a field's page under Edit
(PR-EDIT-040), in the example application.

The test's own registry lets the owner set an update's category under Edit, though the call
says nothing for it, and gives the category a page. Nothing here imports Safwa.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

from agent_turns import mutation_turn, route_turn
from sqlalchemy import select
from wallet import app
from wallet.ledger.model import Entry
from wallet.ledger.module import MODULE as LEDGER
from wallet.ledger.use_cases import record_entry
from wallet.wallets.model import Category, Wallet
from wallet.world import ledger_world
from wallet_harness import READ, Press, press, query_turn, seed_lists, take_a_turn

from llm_gateway import CompletionTurn
from tg_agent_shell.ai.contracts import AgentChange
from tg_agent_shell.proposals.api import ChangeAction, EditableField, Effect, ShownField
from tg_agent_shell.proposals.marks import EMPTY_FIELD, field_line
from tg_agent_shell.registry import Registry
from tg_agent_shell.telegram import callback_token_handler

ABOUT = "ℹ️ About categories"
PAGE = "<b>Categories</b>\nWhere the money came from or went."


async def _categories(session, _entry_id):
    return [(category.name, category.id) for category in await session.scalars(select(Category))]


async def _wallets(session, _entry_id):
    return [(wallet.name, wallet.id) for wallet in await session.scalars(select(Wallet))]


def _updating(call: AgentChange) -> bool:
    return call.action is ChangeAction.UPDATE


def _fields(call: AgentChange) -> dict[str, EditableField]:
    return {
        "amount_minor": EditableField("💵 Amount", "Type the amount", parse=int),
        "wallet_id": EditableField(
            "👛 Wallet", "Choose a wallet", options=_wallets, offered=_updating
        ),
        "category_id": EditableField(
            "🏷 Category",
            "Choose a category",
            options=_categories,
            offered=_updating,
            about=(ABOUT, PAGE),
        ),
        # Kept by preparation like the category, but not offered: it has no button.
        "note": EditableField("📝 Note", "Type the note"),
        # Offered, but preparation keeps no value for it: it has no button either.
        "flag": EditableField("🚩 Flag", "Choose a flag", options=_wallets, offered=_updating),
    }


def _registry() -> Registry:
    ledger = replace(LEDGER, proposals=(replace(LEDGER.proposals[0], editable=_fields),))
    return Registry.of(
        tuple(ledger if module is LEDGER else module for module in app.MODULES),
        world=ledger_world,
    )


async def _update_proposed(wallet_bot, monkeypatch):
    """A review of an update that names only the amount of a lunch kept under Food."""
    monkeypatch.setattr(app, "REGISTRY", _registry())
    seeding = await wallet_bot.start()
    ids = await seed_lists(seeding.sessions)
    async with seeding.sessions() as session:
        entry = await record_entry(
            session,
            wallet_id=ids["cash"],
            category_id=ids["food"],
            amount_minor=1250,
            happened_on=date(2026, 9, 7),
            note="Lunch",
        )
        await session.commit()
    running = await wallet_bot.start(
        route_turn("bookkeeper"),
        query_turn(READ),
        mutation_turn(("entry", {"mode": "update", "id": entry.id, "amount_minor": 990})),
        CompletionTurn(content="Written down."),
        CompletionTurn(content="Updated."),
    )
    await take_a_turn(running, "The lunch was 9.90")
    return running, ids, entry.id


def _buttons(running) -> list[str]:
    markup = running.message.markups[-1]
    return [button.text for row in markup.inline_keyboard for button in row]


async def _tap(running, text: str) -> None:
    """Press the button reading `text` on the newest screen."""
    markup = running.message.markups[-1]
    button = next(
        button for row in markup.inline_keyboard for button in row if button.text == text
    )
    token = button.callback_data.split(":", 1)[1]
    await callback_token_handler(Press(token, running.screen), running.services)


def test_pr_screen_039_a_field_on_every_proposal_stands_there_and_reads_a_dash_when_empty():
    """PR-SCREEN-039 — tests/brd/tg_agent_shell/proposals.feature"""
    kept = ShownField("Effort", "3", "3", always=True)
    empty = ShownField("Time spent", None, None, always=True)

    assert field_line(kept, Effect.UPDATE) == "<i>Effort:</i> 3"
    assert field_line(empty, Effect.UPDATE) == f"<i>Time spent:</i> {EMPTY_FIELD}"
    assert field_line(empty, Effect.CREATE) == f"<i>Time spent:</i> {EMPTY_FIELD}"
    assert field_line(replace(kept, after="5"), Effect.UPDATE) == "<i>Effort:</i> <s>3</s> <b>5</b>"
    # Without the flag, a field the edit leaves as it is says nothing.
    assert field_line(replace(kept, always=False), Effect.UPDATE) is None


async def test_pr_screen_039_edit_offers_a_kept_field_the_call_said_nothing_for(
    wallet_bot, monkeypatch
):
    """PR-SCREEN-039 — tests/brd/tg_agent_shell/proposals.feature"""
    running, ids, entry_id = await _update_proposed(wallet_bot, monkeypatch)

    await press(running, "proposal_edit")
    assert _buttons(running) == ["💵 Amount", "👛 Wallet", "🏷 Category", "↩️ Back"]
    await _tap(running, "🏷 Category")
    # What the check kept is the value the field has now.
    assert "✓ Food" in _buttons(running)
    await _tap(running, "Salary")
    await press(running, "proposal_approve")

    async with running.sessions() as session:
        entry = await session.get(Entry, entry_id)
        assert (entry.amount_minor, entry.category_id) == (990, ids["salary"])


async def test_pr_edit_040_a_fields_page_opens_from_its_choices_and_goes_back_to_them(
    wallet_bot, monkeypatch
):
    """PR-EDIT-040 — tests/brd/tg_agent_shell/proposals.feature"""
    running, ids, entry_id = await _update_proposed(wallet_bot, monkeypatch)

    await press(running, "proposal_edit")
    await _tap(running, "🏷 Category")
    choices = _buttons(running)
    assert choices[-2:] == [ABOUT, "↩️ Back"]

    await _tap(running, ABOUT)
    assert running.chat[-1] == PAGE
    assert _buttons(running) == ["↩️ Back"]

    await _tap(running, "↩️ Back")
    assert _buttons(running) == choices
    await press(running, "proposal_approve")

    async with running.sessions() as session:
        entry = await session.get(Entry, entry_id)
        assert (entry.amount_minor, entry.category_id) == (990, ids["food"])


async def test_pr_edit_040_a_field_with_no_page_has_no_such_button(wallet_bot, monkeypatch):
    """PR-EDIT-040 — tests/brd/tg_agent_shell/proposals.feature"""
    running, _ids, _entry_id = await _update_proposed(wallet_bot, monkeypatch)

    await press(running, "proposal_edit")
    await _tap(running, "👛 Wallet")

    assert _buttons(running) == ["✓ Cash", "Card", "↩️ Back"]
