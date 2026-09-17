"""The owner's Profile."""

from __future__ import annotations

from tg_agent_shell.telegram.contributions import ScreenCommand
from tg_agent_shell.telegram.manifest import FeatureModule

from . import telegram
from .hooks import DAILY_SUMMARY_HOOK as DAILY_SUMMARY_HOOK
from .telegram import PROFILE_CALLBACK_ACTIONS, command_profile

MODULE = FeatureModule(
    name="profile",
    commands=(
        # The Profile is one tap away in the menu, so it needs no command line too.
        ScreenCommand(handler=command_profile, nav="profile", title="⚙️ Profile"),
    ),
    callback_actions=PROFILE_CALLBACK_ACTIONS,
    text_inputs=(telegram.TEXT_INPUT,),
)
