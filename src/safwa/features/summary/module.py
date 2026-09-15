"""Summary: the running account of the conversation that the window ends at."""

from __future__ import annotations

from tg_agent_shell.telegram.contributions import ScreenCommand
from tg_agent_shell.telegram.manifest import FeatureModule

from .hooks import SUMMARY_HOOK as SUMMARY_HOOK
from .telegram import command_summarize

MODULE = FeatureModule(
    name="summary",
    commands=(
        ScreenCommand(
            handler=command_summarize,
            command="summarize",
            description="Summarize the dialogue now",
        ),
    ),
)
