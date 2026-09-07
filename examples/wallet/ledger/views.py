"""The `ai_*` view over the ledger, with both names joined in so a read needs one query."""

from __future__ import annotations

from tg_agent_shell.ai.sql import SqlView

AI_ENTRIES = SqlView(
    "ai_entries",
    """SELECT e.id AS id,
              e.happened_on AS happened_on,
              e.amount_minor AS amount_minor,
              e.note AS note,
              e.wallet_id AS wallet_id,
              w.name AS wallet_name,
              e.category_id AS category_id,
              c.name AS category_name,
              c.kind AS kind
         FROM entries e
         JOIN wallets w ON w.id = e.wallet_id
         JOIN categories c ON c.id = e.category_id""",
    doc="""- `ai_entries(id, happened_on, amount_minor, note, wallet_id, wallet_name, category_id, category_name, kind)`
  - `happened_on` is `YYYY-MM-DD`; `amount_minor` is always positive and `kind` says the direction""",
)

VIEWS = (AI_ENTRIES,)
