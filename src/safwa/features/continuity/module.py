"""Continuity: the summaries and the memory file that outlive one conversation."""

from __future__ import annotations

from tg_agent_shell.telegram.contributions import ScreenCommand
from tg_agent_shell.telegram.manifest import FeatureModule

from .background import MEMORY_FILE_POLL, MEMORY_MAINTENANCE
from .telegram import (
    command_memory,
    command_remember,
    command_summarize,
    command_syncmem,
)

MODULE = FeatureModule(
    name="continuity",
    background=(MEMORY_FILE_POLL, MEMORY_MAINTENANCE),
    commands=(
        ScreenCommand(
            handler=command_syncmem,
            command="syncmem",
            description="Sync Telegram dialogue into memory",
        ),
        ScreenCommand(
            handler=command_remember,
            command="mem",
            description="Add a durable memory fact",
        ),
        ScreenCommand(
            handler=command_memory,
            command="memory",
            description="Inspect memory.md",
        ),
        ScreenCommand(
            handler=command_summarize,
            command="summarize",
            description="Summarize the dialogue now",
        ),
    ),
)
