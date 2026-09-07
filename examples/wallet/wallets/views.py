"""The `ai_*` views over the two lists, and the balance derived from the entries.

`ai_wallet_balances` reads the ledger's table by name, the way a view crossing two features
does: a wallet's balance is a wallet's state, and nothing but the entries can tell it.
"""

from __future__ import annotations

from tg_agent_shell.ai.sql import SqlView

AI_WALLETS = SqlView(
    "ai_wallets",
    "SELECT id, name, currency FROM wallets",
    doc="""- `ai_wallets(id, name, currency)`
  - `currency` is a three-letter code such as `USD`""",
)

AI_CATEGORIES = SqlView(
    "ai_categories",
    "SELECT id, name, kind FROM categories",
    doc="""- `ai_categories(id, name, kind)`
  - `kind` is `income` or `expense`; it is what decides the direction of an entry""",
)

AI_WALLET_BALANCES = SqlView(
    "ai_wallet_balances",
    """SELECT w.id AS wallet_id,
              w.name AS wallet_name,
              w.currency AS currency,
              COALESCE(SUM(CASE WHEN c.kind = 'income'
                                THEN e.amount_minor ELSE -e.amount_minor END), 0)
                AS balance_minor
         FROM wallets w
         LEFT JOIN entries e ON e.wallet_id = w.id
         LEFT JOIN categories c ON c.id = e.category_id
     GROUP BY w.id, w.name, w.currency""",
    doc="""- `ai_wallet_balances(wallet_id, wallet_name, currency, balance_minor)`
  - `balance_minor` is in minor units; a wallet with no entries reads 0""",
)

VIEWS = (AI_WALLETS, AI_CATEGORIES, AI_WALLET_BALANCES)
