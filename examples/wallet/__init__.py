"""A whole second application on `tg_agent_shell`, with no Safwa in it.

Money in and out of a few wallets. Two features — `wallets` keeps the wallets and the
categories the owner edits by hand, `ledger` keeps the entries only the model proposes —
so the same path Safwa runs is run here by something that shares none of its nouns: a
subagent reads, proposes one entry, the owner presses Save or Discard, the suspended
session carries on to its own last word, and the saved entry has a screen of its own.

Run it: `BOT_TOKEN=... OWNER_ID=... uv run python -m wallet.app` from `examples/`.
"""

from __future__ import annotations
