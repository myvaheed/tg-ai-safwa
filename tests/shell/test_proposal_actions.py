from __future__ import annotations

from dataclasses import replace

from telegram_fakes import QueueTestCallback
from wallet import app
from wallet.ledger.module import MODULE as LEDGER
from wallet.world import ledger_world
from wallet_harness import entry_script, seed_lists, take_a_turn

from tg_agent_shell.proposals.api import ProposalAction
from tg_agent_shell.registry import Registry
from tg_agent_shell.telegram import callback_token_handler


async def test_a_feature_offers_an_extra_review_action_without_importing_safwa(wallet_bot, monkeypatch):
    """PR-ACTION-041 — tests/brd/tg_agent_shell/proposals.feature"""
    pressed = []

    async def inspect(context):
        pressed.append(context.payload)

    ledger = replace(
        LEDGER,
        proposals=(replace(LEDGER.proposals[0], actions=lambda change: ((
            ProposalAction("Inspect", "inspect_entry", {"chosen": "entry"}),
        ),)),),
        callback_actions={**LEDGER.callback_actions, "inspect_entry": inspect},
    )
    modules = tuple(ledger if module is LEDGER else module for module in app.MODULES)
    monkeypatch.setattr(app, "REGISTRY", Registry.of(modules, world=ledger_world))
    seed = await wallet_bot.start()
    ids = await seed_lists(seed.sessions)
    running = await wallet_bot.start(*entry_script(ids, answer="Saved."))
    await take_a_turn(running, "I spent 12.50 on lunch out of Cash")
    markup = running.message.markups[-1]
    assert markup.inline_keyboard[0][0].text == "Inspect"
    button = markup.inline_keyboard[0][0]
    for _ in range(2):
        await callback_token_handler(QueueTestCallback(button.callback_data.split(":", 1)[1], running.screen), running.services)
    assert len(pressed) == 1 and pressed[0]["chosen"] == "entry"
    assert isinstance(pressed[0]["id"], int)
