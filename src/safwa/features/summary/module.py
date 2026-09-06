"""Summary: the running account of the conversation that the window ends at."""

from __future__ import annotations

from tg_agent_shell.telegram.contributions import ScreenCommand
from tg_agent_shell.telegram.manifest import FeatureModule

from .telegram import close_window_after_turn, command_summarize

MODULE = FeatureModule(
    name="summary",
    after_turn=(close_window_after_turn,),
    commands=(
        ScreenCommand(
            handler=command_summarize,
            command="summarize",
            description="Summarize the dialogue now",
        ),
    ),
)
