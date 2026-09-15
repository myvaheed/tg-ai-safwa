"""The example bot's whole path, in a process where `safwa` cannot be imported at all.

Run as a subprocess by `test_standalone.py`. What it proves is what an import graph cannot:
that the second application reaches the model, the review, the Save and the saved row
through the shell alone, and that a fresh database gets its tables and no others.

Usage: `python standalone_run.py <database path>`; prints one JSON line.
"""

from __future__ import annotations

import asyncio
import json
import sys
from importlib.abc import MetaPathFinder
from pathlib import Path


class NoSafwa(MetaPathFinder):
    """Every `safwa` import fails here, so nothing can reach it by accident."""

    def find_spec(self, fullname, path=None, target=None):  # noqa: ANN001, ANN201
        if fullname == "safwa" or fullname.startswith("safwa."):
            raise ImportError(f"{fullname} is not available to this application")
        return None


sys.meta_path.insert(0, NoSafwa())
HERE = Path(__file__).resolve()
for folder in (HERE.parent, HERE.parents[2] / "examples", HERE.parents[2] / "tests"):
    sys.path.insert(0, str(folder))

from hook_helpers import run_hooks  # noqa: E402
from sqlalchemy import inspect, select  # noqa: E402
from wallet.ledger.model import Entry  # noqa: E402
from wallet_harness import (  # noqa: E402
    WalletHarness,
    entry_script,
    press,
    seed_lists,
    take_a_turn,
)


async def main(path: Path) -> dict[str, object]:
    harness = WalletHarness(path)
    seeding = await harness.start()
    ids = await seed_lists(seeding.sessions)
    running = await harness.start(*entry_script(ids, answer="Lunch is on the Cash wallet."))
    assert running.services.hooks.specs == ()
    turns = []

    async def observe(event, context):
        assert context.still_current()
        turns.append(event.source_message_id)

    running.services.hooks = run_hooks(observe)

    await take_a_turn(running, "I spent 12.50 on lunch out of Cash")
    assert turns == [running.message.message_id]
    await press(running, "proposal_approve")

    async with running.sessions() as session:
        entries = list(await session.scalars(select(Entry)))
        connection = await session.connection()
        tables = await connection.run_sync(lambda sync: inspect(sync).get_table_names())
    await harness.stop()
    return {
        "answer": running.chat[-1],
        "entries": len(entries),
        "tables": sorted(tables),
        "safwa_imported": any(name == "safwa" or name.startswith("safwa.") for name in sys.modules),
    }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(main(Path(sys.argv[1])))))
