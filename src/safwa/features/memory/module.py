"""Memory: what Safwa remembers across Sprints, written from the retro analysis alone."""

from __future__ import annotations

from tg_agent_shell.telegram.contributions import ScreenCommand
from tg_agent_shell.telegram.manifest import FeatureModule

from .hooks import MEMORY_RETRO_HOOK as MEMORY_RETRO_HOOK
from .telegram import command_memory

MODULE = FeatureModule(
    name="memory",
    commands=(
        ScreenCommand(
            handler=command_memory,
            command="memory",
            description="Show what Safwa remembers",
        ),
    ),
)
