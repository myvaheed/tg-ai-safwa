"""What every test of the example application gets: one database file it may restart.

Everything the harness is lives in `wallet_harness`, so the same code drives the bot from
a process where `safwa` cannot be imported at all.
"""

from __future__ import annotations

import pytest_asyncio
from wallet_harness import WalletHarness


@pytest_asyncio.fixture
async def wallet_bot(tmp_path):
    """A real SQLite file and the real shell; only Telegram and the model are replaced."""
    harness = WalletHarness(tmp_path / "wallet.db")
    try:
        yield harness
    finally:
        await harness.stop()
