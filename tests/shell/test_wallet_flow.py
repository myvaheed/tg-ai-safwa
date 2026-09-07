"""The whole path of a second application, through the shell and nothing else.

A read becomes a proposal, the owner answers it, the suspended session carries on to its
own last word, and what was saved has a screen of its own. Nothing here imports Safwa.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select
from wallet.ledger.model import Entry
from wallet.wallets.use_cases import wallet_balance
from wallet_harness import entry_script, press, seed_lists, take_a_turn

from llm_gateway import ToolCall
from tg_agent_shell.ai.sql import read_query


@pytest.mark.parametrize(
    ("action", "written", "answer"),
    [
        ("proposal_approve", True, "Lunch is on the Cash wallet."),
        ("proposal_reject", False, "Nothing was written down."),
    ],
)
async def test_a_read_becomes_a_proposal_and_the_owner_decides_it(
    wallet_bot, action, written, answer
):
    seeding = await wallet_bot.start()
    ids = await seed_lists(seeding.sessions)
    running = await wallet_bot.start(*entry_script(ids, answer=answer))

    await take_a_turn(running, "I spent 12.50 on lunch out of Cash")
    assert "Ledger entry" in running.chat[-1]

    await press(running, action)

    async with running.sessions() as session:
        entries = list(await session.scalars(select(Entry)))
        balance = await wallet_balance(session, ids["cash"])
    assert bool(entries) is written
    assert balance == (-1250 if written else 0)
    assert answer in running.chat[-1]
    # The script is the whole of what the model said: five requests, and no sixth.
    assert len(running.provider.requests) == 5


async def test_the_saved_entry_has_a_screen_of_its_own(wallet_bot):
    seeding = await wallet_bot.start()
    ids = await seed_lists(seeding.sessions)
    running = await wallet_bot.start(*entry_script(ids, answer="Done."))
    await take_a_turn(running, "I spent 12.50 on lunch out of Cash")
    await press(running, "proposal_approve")

    async with running.sessions() as session:
        entry = await session.scalar(select(Entry))
    assert entry is not None

    spec = running.services.screens.by_type["entry"]
    await spec.open(running.message, running.services, entry.id)
    assert "Lunch" in running.chat[-1]
    assert "−12.50 USD" in running.chat[-1]


async def test_each_reader_reaches_only_the_views_its_own_list_names(wallet_bot):
    """The ledger publishes `ai_entries`; the root session's own list leaves it out."""
    running = await wallet_bot.start()
    await seed_lists(running.sessions)
    call = ToolCall(
        id="read-1",
        name="query_data",
        arguments_json=json.dumps({"sql": "SELECT id FROM ai_entries"}),
    )

    assert "ai_entries" in running.services.views
    allowed = await read_query(running.root.subagents["bookkeeper"].query_runner, call)
    refused = await read_query(running.root.adapters.query_runner, call)

    assert allowed.rows == []
    assert "ai_entries" in str(refused.rows)
