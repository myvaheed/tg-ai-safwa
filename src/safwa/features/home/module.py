"""Home: the dashboard, its menu, and the command that empties the chat."""

from __future__ import annotations

from tg_agent_shell.telegram.contributions import ScreenCommand
from tg_agent_shell.telegram.manifest import FeatureModule

from .hooks import HOME_HOOK as HOME_HOOK
from .telegram import command_clear, render_home

MODULE = FeatureModule(
    name="home",
    commands=(
        ScreenCommand(
            handler=render_home, command="start", description="Open Safwa", nav="home"
        ),
        ScreenCommand(
            handler=command_clear, command="clear", description="Empty the chat"
        ),
    ),
)
