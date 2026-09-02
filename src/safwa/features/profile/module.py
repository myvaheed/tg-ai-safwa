"""Owner profile and Settings, including their startup reconciliation."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from ...bootstrap.module_manifest import FeatureModule
from ...foundation.clock import SystemClock
from ...foundation.screens import ScreenCommand
from . import telegram
from .telegram import SETTINGS_CALLBACK_ACTIONS, command_settings
from .use_cases import sync_diary_reminder


async def _reconcile_diary_trigger(session: AsyncSession) -> None:
    """Recovery binds the wall clock; the operation itself is told what time it is."""
    await sync_diary_reminder(session, clock=SystemClock())


MODULE = FeatureModule(
    name="profile",
    recover=_reconcile_diary_trigger,
    commands=(
        ScreenCommand(
            handler=command_settings,
            command="settings",
            description="Profile and reminders",
            nav="settings",
            title="⚙️ Settings",
        ),
    ),
    callback_actions=SETTINGS_CALLBACK_ACTIONS,
    text_inputs=(telegram.TEXT_INPUT,),
)
