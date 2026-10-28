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

from agent_turns import mutation_turn, route_turn  # noqa: E402
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

from llm_gateway import CompletionTurn  # noqa: E402
from tg_agent_shell.media.library import ChatMedia, Photo  # noqa: E402


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
    entry_answer = running.chat[-1]
    renaming = await harness.start(
        route_turn("bookkeeper"),
        mutation_turn(("rename_media", {"media_id": 1, "meta": "Lunch receipt"})),
        CompletionTurn(content="Renamed."),
        CompletionTurn(content="The photo is renamed."),
    )
    (media_id,) = await renaming.services.media.keep(
        [(Photo(b"jpeg", 1280, 960, "receipt-file"), "Receipt")]
    )
    await take_a_turn(renaming, f"Call [Receipt](media:{media_id}) Lunch receipt.")
    await press(renaming, "proposal_approve")
    async with renaming.sessions() as session:
        photo_label = (await session.get(ChatMedia, media_id)).meta
    await harness.stop()
    return {
        "answer": entry_answer,
        "entries": len(entries),
        "photo_label": photo_label,
        "tables": sorted(tables),
        "safwa_imported": any(name == "safwa" or name.startswith("safwa.") for name in sys.modules),
    }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(main(Path(sys.argv[1])))))
