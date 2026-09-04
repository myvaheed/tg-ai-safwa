"""The owner's Profile, including its startup reconciliation."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.foundation.clock import SystemClock
from tg_agent_shell.telegram.contributions import ScreenCommand
from tg_agent_shell.telegram.manifest import FeatureModule

from . import telegram
from .telegram import PROFILE_CALLBACK_ACTIONS, command_profile
from .use_cases import sync_diary_reminder


async def _reconcile_diary_trigger(session: AsyncSession) -> None:
    """Recovery binds the wall clock; the operation itself is told what time it is."""
    await sync_diary_reminder(session, clock=SystemClock())


MODULE = FeatureModule(
    name="profile",
    recover=_reconcile_diary_trigger,
    commands=(
        ScreenCommand(
            handler=command_profile,
            command="profile",
            description="Profile and reminders",
            nav="profile",
            title="⚙️ Profile",
        ),
    ),
    callback_actions=PROFILE_CALLBACK_ACTIONS,
    text_inputs=(telegram.TEXT_INPUT,),
)
