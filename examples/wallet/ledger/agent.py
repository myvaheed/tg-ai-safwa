"""The bookkeeper: it reads the ledger and proposes one entry. It writes nothing itself."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import Field, PositiveInt, field_validator, model_validator

from tg_agent_shell.ai.contracts import ToolInput
from tg_agent_shell.foundation.clock import SystemClock
from tg_agent_shell.proposals.api import MutationToolSpec, entity_change
from tg_agent_shell.telegram.manifest import AgentContext, AgentSpec


class EntryToolInput(ToolInput):
    """One line of the ledger: written, replaced whole, or removed."""

    mode: Literal["create", "update", "delete"] = Field(
        description="create writes a line; update replaces one whole; delete removes one."
    )
    id: PositiveInt | None = Field(
        default=None, description="With update and delete: the entry from ai_entries."
    )
    wallet_id: PositiveInt | None = Field(
        default=None, description="With create: the wallet from ai_wallets."
    )
    category_id: PositiveInt | None = Field(
        default=None, description="With create: the category from ai_categories."
    )
    amount_minor: PositiveInt | None = Field(
        default=None, description="With create: the amount in minor units, always positive."
    )
    happened_on: str | None = Field(
        default=None, description="With create: the day it happened, as YYYY-MM-DD."
    )
    note: str | None = Field(
        default=None, description="One short line about this entry, in the user's words."
    )

    @field_validator("happened_on")
    @classmethod
    def validate_calendar_date(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            return date.fromisoformat(value.strip()).isoformat()
        except ValueError as error:
            raise ValueError("happened_on must be a calendar date written as YYYY-MM-DD") from error

    @model_validator(mode="after")
    def a_change_names_its_target(self) -> EntryToolInput:
        if self.mode == "create":
            missing = [
                name
                for name in ("wallet_id", "category_id", "amount_minor", "happened_on")
                if getattr(self, name) is None
            ]
            if missing:
                raise ValueError(f"create needs {', '.join(missing)}")
        elif self.id is None:
            raise ValueError(f"{self.mode} needs the id of the entry it changes")
        if self.mode == "delete" and (self.amount_minor is not None or self.note is not None):
            raise ValueError("A deletion carries only mode and id")
        return self


BOOKKEEPER_PROMPT = """You keep the user's ledger. One movement of money, one entry.

1. Read before you write. `query_data` runs one read-only SELECT over these views only:
{views}
2. Match the wallet and the category the user means to their ids. If neither the wallet nor
   the category exists, say which one is missing and propose nothing — the user makes those
   by hand.
3. In the response with the `entry` tool, write your plan as text: what you will record.
4. The `entry` tool, in that same response:
   - `entry(mode="create", wallet_id=…, category_id=…, amount_minor=…, happened_on=…, note=…)`
   - `entry(mode="update", id=…, …)` — every field you leave out keeps its saved value.
   - `entry(mode="delete", id=…)` — the user asked for that line to go.
   `amount_minor` is always positive. The category's `kind` is what makes it income or expense.
"""


def bookkeeper_clock(timezone: str) -> str:
    now = SystemClock().now().astimezone(ZoneInfo(timezone))
    return f"Today is {now.date().isoformat()}, local time now {now:%H:%M}, timezone {timezone}"


def _clock(context: AgentContext) -> Callable[[], str]:
    return lambda: bookkeeper_clock(context.timezone)


def today_in(timezone: str) -> date:
    return datetime.now(ZoneInfo(timezone)).date()


BOOKKEEPER = AgentSpec(
    name="bookkeeper",
    purpose="write, correct or remove one ledger entry.",
    instructions=BOOKKEEPER_PROMPT,
    views=("ai_wallets", "ai_categories", "ai_entries", "ai_wallet_balances"),
    mutation_tools=("entry",),
    clock=_clock,
)

ENTRY_TOOL = MutationToolSpec(
    name="entry",
    input_model=EntryToolInput,
    description="Propose one ledger entry: money in or out of one wallet.",
    to_change=entity_change("entry"),
)
