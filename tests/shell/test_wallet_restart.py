"""What a restart does to a second application, and what the owner edits by hand.

Recovery is the shell's: a review lives in the running process, so a restart has already
ended it, and every button drawn for it has to say so rather than claim.
"""

from __future__ import annotations

from sqlalchemy import select
from wallet.app import REGISTRY
from wallet.ledger.model import Entry
from wallet.wallets.model import Category, Wallet
from wallet_harness import Press, entry_script, live_token, press, seed_lists, take_a_turn

from agent_runtime import RunStatus
from tg_agent_shell.ai.runs import AgentRun
from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.history import TelegramMessage
from tg_agent_shell.telegram import callback_token_handler, open_home
from tg_agent_shell.telegram.dialogue import ordinary_text
from tg_agent_shell.telegram.model import CallbackToken, UiSession


async def test_a_button_drawn_before_a_restart_is_refused_rather_than_claimed(wallet_bot):
    seeding = await wallet_bot.start()
    ids = await seed_lists(seeding.sessions)
    running = await wallet_bot.start(*entry_script(ids, answer="Done."))
    await take_a_turn(running, "I spent 12.50 on lunch out of Cash")
    stale = await live_token(running, "proposal_approve")

    restarted = await wallet_bot.start()

    async with restarted.sessions() as session:
        assert list(await session.scalars(select(CallbackToken))) == []
        runs = list(await session.scalars(select(AgentRun)))
        approvals = list(
            await session.scalars(
                select(TelegramMessage).where(
                    TelegramMessage.kind == MessageKind.APPROVAL.value
                )
            )
        )
    assert runs and all(run.status == RunStatus.ABANDONED.value for run in runs)
    assert approvals and all(message.related_id is None for message in approvals)

    await callback_token_handler(Press(stale, restarted.screen), restarted.services)

    assert "out of date" in restarted.chat[-1]
    async with restarted.sessions() as session:
        assert list(await session.scalars(select(Entry))) == []


async def test_the_owner_adds_a_wallet_and_a_category_by_hand(wallet_bot):
    """The manual path writes through the same use cases an approved proposal calls."""
    running = await wallet_bot.start()
    await open_home(running.message, running.services)
    await press(running, "wallet_add_prompt")
    running.message.text = "Savings CHF"
    await ordinary_text(running.message, running.services)

    async with running.sessions() as session:
        wallet = await session.scalar(select(Wallet).where(Wallet.name == "Savings"))
    assert wallet is not None and wallet.currency == "CHF"
    assert "Savings" in running.chat[-1]

    await press(running, "category_list")
    await press(running, "category_add_prompt")
    running.message.text = "Rent expense"
    await ordinary_text(running.message, running.services)

    async with running.sessions() as session:
        category = await session.scalar(select(Category).where(Category.name == "Rent"))
    assert category is not None and category.kind == "expense"
    assert "Rent" in running.chat[-1]


async def test_a_refused_value_keeps_the_editor_open(wallet_bot):
    running = await wallet_bot.start()
    await open_home(running.message, running.services)
    await press(running, "wallet_add_prompt")
    running.message.text = "Savings francs"
    await ordinary_text(running.message, running.services)

    assert "three-letter code" in running.chat[-1]
    async with running.sessions() as session:
        assert list(await session.scalars(select(Wallet))) == []
        editor = await session.scalar(select(UiSession))
    assert editor is not None and editor.kind == "text_input"


def test_the_application_publishes_exactly_one_home_screen():
    """The shell refuses a feature list with no home screen, or with two."""
    assert [command.nav for command in REGISTRY.commands].count("home") == 1
