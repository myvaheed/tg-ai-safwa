"""The owner's Profile, including its startup reconciliation."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.foundation.clock import SystemClock
from tg_agent_shell.telegram.contributions import ScreenCommand
from tg_agent_shell.telegram.manifest import FeatureModule

from . import telegram
from .telegram import PROFILE_CALLBACK_ACTIONS, command_profile
from .use_cases import sync_diary_reminder, sync_summary_reminder


async def _reconcile_triggers(session: AsyncSession) -> None:
    """Recovery binds the wall clock; the operations themselves are told what time it is."""
    await sync_diary_reminder(session, clock=SystemClock())
    await sync_summary_reminder(session, clock=SystemClock())


MODULE = FeatureModule(
    name="profile",
    recover=_reconcile_triggers,
    commands=(
        # The Profile is one tap away in the menu, so it needs no command line too.
        ScreenCommand(handler=command_profile, nav="profile", title="⚙️ Profile"),
    ),
    callback_actions=PROFILE_CALLBACK_ACTIONS,
    text_inputs=(telegram.TEXT_INPUT,),
)
