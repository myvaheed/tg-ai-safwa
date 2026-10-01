"""How a proposal to change the Profile reads to the owner: each field, as it is and as it
becomes, in the words the Profile screen shows it with."""

from __future__ import annotations

import html
from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.ai.contracts import AgentChange
from tg_agent_shell.proposals.api import ProposalChange, ProposalScreen

from ..model import ProfileField, UserProfile
from ..proposal import stored_value
from .screens import PROFILE_FIELDS


def _title(name: str) -> str:
    field = PROFILE_FIELDS.get(name)
    return field.title if field is not None else {
        "time_tracking": "Time tracking", "effort_tracking": "Effort Points"
    }[name]


def _show(name: str, value: Any) -> str:
    """One value as the Profile screen writes it."""
    field = PROFILE_FIELDS.get(name)
    if field is None:
        return "on" if value else "off"
    return field.show(stored_value(ProfileField(name), value))


def _lines(values: dict[str, Any]) -> list[str]:
    return [f"{_title(name)}: {_show(name, value)}" for name, value in values.items()]


class ProfileProposalPresenter:
    entity = "profile"

    def raw_details(self, change: AgentChange) -> list[str]:
        return _lines(change.values)

    async def details(
        self, session: AsyncSession, change: ProposalChange, fallback: AgentChange | None
    ) -> list[str]:
        return _lines(change.values)

    async def summary(
        self, session: AsyncSession, change: ProposalChange, details: list[str]
    ) -> str:
        return "Change the Profile"

    async def screen(
        self, session: AsyncSession, changes: Sequence[ProposalChange]
    ) -> ProposalScreen | None:
        profile = await session.get(UserProfile, 1)
        if profile is None:
            return None
        diffs = [
            f"• {html.escape(_title(name))}: "
            f"{html.escape(_show(name, getattr(profile, name)))} → "
            f"{html.escape(_show(name, value))}"
            for change in changes
            for name, value in change.values.items()
        ]
        return ProposalScreen(mode="Edit", item="Profile", diffs=tuple(diffs))
