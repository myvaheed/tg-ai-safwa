"""Memory: what Safwa remembers across Sprints, written from the retro analysis alone."""

from __future__ import annotations

from tg_agent_shell.telegram.contributions import ScreenCommand
from tg_agent_shell.telegram.manifest import FeatureModule

from .background import MEMORY_RETRO
from .telegram import command_memory

MODULE = FeatureModule(
    name="memory",
    background=(MEMORY_RETRO,),
    commands=(
        ScreenCommand(
            handler=command_memory,
            command="memory",
            description="Show what Safwa remembers",
        ),
    ),
)
