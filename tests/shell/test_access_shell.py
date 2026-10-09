"""Another application plugs into access through shell callbacks alone."""

from __future__ import annotations

from telegram_fakes import QueueTestMessage

from tg_agent_shell.access.credentials import hash_secret_word
from tg_agent_shell.access.manager import AccessManager


async def test_a_second_bot_can_lock_and_restore_its_own_home_without_safwa(wallet_bot):
    """TG-LOCK-031 — tests/brd/tg_agent_shell/telegram_history.feature"""
    running = await wallet_bot.start()
    verifier = hash_secret_word("🔑")

    async def read_verifier(session):
        return verifier

    async def home(message, services):
        await services.chat.send(message, "Wallet home", kind="dashboard", replace=False)

    access = AccessManager(running.services, read_verifier, home)
    running.services.access = access
    await access.initialize(running.message)
    assert access.blocked
    await access.defer(running.message, {"text": "A wallet notification", "kind": "cue"})
    word = QueueTestMessage(
        message_id=2000,
        text="🔑",
        is_bot=False,
        answer_as_new=True,
        parent=running.message,
    )
    await access.intercept(word)
    assert not access.blocked
    assert running.chat == ["A wallet notification", "Wallet home"]
